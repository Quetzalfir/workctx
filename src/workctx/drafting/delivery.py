"""Preview-pinned, approval-gated delivery of one canonical draft."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal, NoReturn, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, Field

from workctx.adapters.filesystem import CanonicalStore, ContextZone, render_markdown_bytes
from workctx.domain import EntityFrontmatter
from workctx.domain.transactions import (
    EntityDocumentPayload,
    PathHashCondition,
    SystemActor,
    TransactionProposal,
    UpdateOperation,
)
from workctx.drafting.errors import (
    SendApprovalRequiredError,
    SendAuditCommitError,
    SendAuthenticationError,
    SendConnectionError,
    SendFingerprintMismatchError,
    SendInputError,
    SendResponseError,
    SendSecretError,
    SendStateError,
    SendStatusError,
    SendTimeoutError,
)
from workctx.drafting.models import (
    GITHUB_COMMENT_URL_PATTERN,
    GITHUB_TARGET_PATTERN,
    SEND_FINGERPRINT_PATTERN,
    DraftDelivery,
    DraftRecord,
    _DraftFrontmatter,
)
from workctx.drafting.service import DraftService
from workctx.secrets import SecretValue, resolve
from workctx.transactions import ApplyResult, verify_ledger
from workctx.validation import contains_possible_secret

Clock = Callable[[], datetime]

_GITHUB_SECRET_REF = "github-token"
_GITHUB_ENV_VAR = "GITHUB_TOKEN"
_GITHUB_API_ROOT = "https://api.github.com"
_GITHUB_RESPONSE_LIMIT = 512 * 1024
_GITHUB_TARGET = re.compile(GITHUB_TARGET_PATTERN)
_GITHUB_COMMENT_URL = re.compile(GITHUB_COMMENT_URL_PATTERN)

_ChannelNameT = TypeVar("_ChannelNameT", bound=str, covariant=True)
_TargetT = TypeVar("_TargetT")


class _SendModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SendPreview(_SendModel):
    """Exact operator-review envelope for one draft, channel, and recipient."""

    schema_version: Literal[1] = 1
    operation: Literal["preview"] = "preview"
    draft_id: str
    channel: Literal["github"]
    target: str = Field(pattern=GITHUB_TARGET_PATTERN, max_length=200)
    recipient_display: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=100_000)
    draft_content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    fingerprint: str = Field(pattern=SEND_FINGERPRINT_PATTERN)


class SendResult(_SendModel):
    """Ledger-authenticated local record of one successful remote comment."""

    schema_version: Literal[1] = 1
    operation: Literal["sent"] = "sent"
    draft: DraftRecord
    delivery: DraftDelivery
    receipt: ApplyResult


@dataclass(frozen=True, slots=True)
class _GitHubTarget:
    target: str
    owner: str
    repository: str
    number: int

    @property
    def display(self) -> str:
        return f"{self.target} (GitHub issue or pull request)"

    @property
    def endpoint(self) -> str:
        return (
            f"{_GITHUB_API_ROOT}/repos/{self.owner}/{self.repository}/issues/{self.number}/comments"
        )


@dataclass(frozen=True, slots=True)
class _RemoteComment:
    comment_id: str
    url: str


class SendChannel(Protocol[_ChannelNameT, _TargetT]):
    """Deterministic seam for one explicitly selected external-write channel."""

    @property
    def name(self) -> _ChannelNameT: ...

    def resolve_target(self, target: str) -> _TargetT: ...

    def deliver(
        self,
        target: _TargetT,
        body: str,
        *,
        transport: httpx.BaseTransport | None,
    ) -> _RemoteComment: ...


class _DeliveryFailureKind(StrEnum):
    AUTHENTICATION = "authentication"
    CONNECTION = "connection"
    TIMEOUT = "timeout"
    STATUS = "status"
    RESPONSE = "response"


@dataclass(frozen=True, slots=True)
class _DeliveryFailure:
    kind: _DeliveryFailureKind
    status_code: int | None = None


@dataclass(frozen=True, slots=True)
class _DeliveryOutcome:
    comment: _RemoteComment | None = None
    failure: _DeliveryFailure | None = None


class _GitHubRequestBoundaryTransport(httpx.BaseTransport):
    """Reveal the GitHub token only in the request handed to the transport."""

    def __init__(self, inner: httpx.BaseTransport, secret: SecretValue) -> None:
        self._inner = inner
        self._secret: SecretValue | None = secret

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        secret = self._secret
        if secret is None:
            raise RuntimeError("GitHub authentication is unavailable")
        outgoing = httpx.Request(
            request.method,
            request.url,
            headers=request.headers,
            stream=request.stream,
            extensions=dict(request.extensions),
        )
        outgoing.headers["Authorization"] = f"Bearer {secret.reveal()}"
        response = self._inner.handle_request(outgoing)
        _sanitize_response_logging_metadata(response)
        return response

    def clear_secret(self) -> None:
        self._secret = None

    def close(self) -> None:
        self._inner.close()


class GitHubSendChannel:
    """Post one draft body as one GitHub issue-or-PR comment, without retries."""

    name: Literal["github"] = "github"

    def resolve_target(self, target: str) -> _GitHubTarget:
        if not isinstance(target, str):
            raise SendInputError("The GitHub target must use owner/repo#number grammar.")
        match = _GITHUB_TARGET.fullmatch(target)
        if match is None or match.group("repo") in {".", ".."}:
            raise SendInputError("The GitHub target must use owner/repo#number grammar.")
        return _GitHubTarget(
            target=target,
            owner=match.group("owner"),
            repository=match.group("repo"),
            number=int(match.group("number")),
        )

    def deliver(
        self,
        target: _GitHubTarget,
        body: str,
        *,
        transport: httpx.BaseTransport | None,
    ) -> _RemoteComment:
        outcome = _run_github_delivery(target, body, transport=transport)
        if outcome.failure is not None:
            _raise_delivery_failure(outcome.failure)
        if outcome.comment is None:  # pragma: no cover - closed outcome invariant
            raise SendResponseError()
        return outcome.comment


class _ApplyTransaction(Protocol):
    def __call__(
        self,
        context_root: Path,
        proposal: TransactionProposal,
        *,
        approved: bool = False,
        session_id: str | None = None,
    ) -> ApplyResult: ...


@dataclass(frozen=True, slots=True)
class _PreparedSend:
    preview: SendPreview
    draft: DraftRecord
    frontmatter: EntityFrontmatter
    adapter: SendChannel[Literal["github"], _GitHubTarget]
    target: _GitHubTarget


@dataclass(frozen=True, slots=True)
class _DeliveryRecordOutcome:
    result: SendResult | None = None
    failed: bool = False


class OutboxSendService:
    """Application service for previewing and sending exactly one canonical draft."""

    def __init__(
        self,
        context_root: Path,
        *,
        clock: Clock | None = None,
        transaction_apply: _ApplyTransaction | None = None,
    ) -> None:
        self._store = CanonicalStore(context_root)
        self._root = self._store.context_root
        self._clock = clock or _utc_now
        # The default apply shares this service's clock so delivery provenance
        # and the audit-ledger event use one injected timeline.
        self._transaction_apply = transaction_apply or self._clock_bound_apply

    def _clock_bound_apply(
        self,
        context_root: Path,
        proposal: TransactionProposal,
        *,
        approved: bool = False,
    ) -> ApplyResult:
        from workctx.transactions import TransactionEngine

        return TransactionEngine(context_root, clock=self._clock).apply(
            proposal,
            approved=approved,
        )

    def preview(self, draft_id: str, channel: str, target: str) -> SendPreview:
        """Return the exact body and fingerprint without auth, network, or mutation."""

        prepared = self._prepare(draft_id, channel, target)
        if contains_possible_secret(prepared.draft.body):
            raise SendSecretError()
        return prepared.preview

    def send(
        self,
        draft_id: str,
        channel: str,
        target: str,
        *,
        approved: bool,
        fingerprint: str,
        transport: httpx.BaseTransport | None = None,
    ) -> SendResult:
        """Deliver one fingerprint-pinned draft and atomically record sent state."""

        if approved is not True:
            raise SendApprovalRequiredError()
        prepared = self._prepare(draft_id, channel, target)
        if not isinstance(fingerprint, str) or not hmac.compare_digest(
            fingerprint,
            prepared.preview.fingerprint,
        ):
            raise SendFingerprintMismatchError()
        if contains_possible_secret(prepared.draft.body):
            raise SendSecretError()

        remote = prepared.adapter.deliver(
            prepared.target,
            prepared.draft.body,
            transport=transport,
        )
        recorded = self._record_delivery(prepared, remote)
        if recorded.failed or recorded.result is None:
            raise SendAuditCommitError(remote.comment_id, remote.url)
        return recorded.result

    def _prepare(self, draft_id: str, channel: str, target: str) -> _PreparedSend:
        adapter = _adapter(channel)
        resolved_target = adapter.resolve_target(target)
        draft, frontmatter, content_hash = self._stable_draft(draft_id)
        if draft.delivery_state != "unsent" or draft.delivery is not None:
            raise SendStateError("A sent draft cannot be previewed or sent again.")
        fingerprint = _send_fingerprint(content_hash, adapter.name, resolved_target.target)
        preview = SendPreview(
            draft_id=draft.id,
            channel=adapter.name,
            target=resolved_target.target,
            recipient_display=resolved_target.display,
            body=draft.body,
            draft_content_hash=content_hash,
            fingerprint=fingerprint,
        )
        return _PreparedSend(
            preview=preview,
            draft=draft,
            frontmatter=frontmatter,
            adapter=adapter,
            target=resolved_target,
        )

    def _stable_draft(self, draft_id: str) -> tuple[DraftRecord, EntityFrontmatter, str]:
        service = DraftService(self._root)
        selected = service.get_draft(draft_id)
        for _attempt in range(2):
            hash_before = self._draft_content_hash(selected.id)
            draft = service.get_draft(selected.id)
            try:
                document = self._store.read_entity(draft.path)
            except (OSError, ValueError) as exc:
                raise SendStateError("The canonical draft could not be read safely.") from exc
            hash_after = self._draft_content_hash(draft.id)
            if hash_before == hash_after and document.body == draft.body:
                return draft, document.frontmatter, hash_after
        raise SendStateError("The canonical draft changed repeatedly during preview.")

    def _draft_content_hash(self, draft_id: str) -> str:
        relative_path = f"05_outbox/{draft_id}.md"
        path = self._store.resolve_path(relative_path, zones=(ContextZone.OUTBOX,))
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise SendStateError("The canonical draft path is not a regular file.")
        if not path.is_file():
            raise SendStateError("The canonical draft path is not a regular file.")
        try:
            return _hash_bytes(path.read_bytes())
        except OSError as exc:
            raise SendStateError("The canonical draft could not be read safely.") from exc

    def _delivery_proposal(
        self,
        prepared: _PreparedSend,
        delivery: DraftDelivery,
    ) -> TransactionProposal:
        frontmatter_values = prepared.frontmatter.model_dump(mode="python")
        tags = [tag for tag in frontmatter_values.get("tags", []) if tag != "unsent"]
        if "sent" not in tags:
            tags.append("sent")
        frontmatter_values.update(
            {
                "tags": tags,
                "updated_at": delivery.sent_at,
                "delivery_state": "sent",
                "delivery": delivery,
            }
        )
        refined = _DraftFrontmatter.model_validate(frontmatter_values)
        # Delivery is an EntityFrontmatter extra at the unchanged transaction
        # boundary, so its nested timestamp must be JSON-native before preflight.
        updated = EntityFrontmatter.model_validate(refined.model_dump(mode="json"))
        payload = EntityDocumentPayload(
            kind="entity",
            document=updated,
            body=prepared.draft.body,
        )
        postimage_hash = _hash_bytes(render_markdown_bytes(updated, prepared.draft.body))
        source_refs = list(dict.fromkeys((prepared.draft.uri, *prepared.draft.source_refs)))
        return TransactionProposal(
            schema_version=1,
            id=_proposal_id(delivery.sent_at, prepared.draft.id),
            context_id=self._store.context_id,
            base_revision=verify_ledger(self._root).head_hash,
            actor=SystemActor(
                type="system",
                id="workctx-outbox-send",
                agent=None,
                model=None,
            ),
            created_at=delivery.sent_at,
            source_refs=source_refs,
            operations=[
                UpdateOperation(
                    op="update",
                    target=prepared.draft.path,
                    payload=payload,
                    expected_hash=prepared.preview.draft_content_hash,
                )
            ],
            preconditions=[
                PathHashCondition(
                    kind="path_hash",
                    path=prepared.draft.path,
                    content_hash=prepared.preview.draft_content_hash,
                )
            ],
            postconditions=[
                PathHashCondition(
                    kind="path_hash",
                    path=prepared.draft.path,
                    content_hash=postimage_hash,
                )
            ],
            expected_views=["sqlite"],
            approval="required",
        )

    def _record_delivery(
        self,
        prepared: _PreparedSend,
        remote: _RemoteComment,
    ) -> _DeliveryRecordOutcome:
        result: SendResult | None = None
        failed = False
        try:
            sent_at = _normalize_time(self._clock())
            delivery = DraftDelivery(
                channel="github",
                target=prepared.target.target,
                remote_comment_id=remote.comment_id,
                remote_comment_url=remote.url,
                sent_at=sent_at,
            )
            proposal = self._delivery_proposal(prepared, delivery)
            receipt = self._transaction_apply(self._root, proposal, approved=True)
            draft = DraftService(self._root).get_draft(prepared.draft.id)
            if draft.delivery != delivery or draft.delivery_state != "sent":
                failed = True
            else:
                result = SendResult(
                    draft=draft,
                    delivery=delivery,
                    receipt=receipt,
                )
        except Exception:
            failed = True
        return _DeliveryRecordOutcome(result=result, failed=failed)


def preview_send(root: Path, draft_id: str, channel: str, target: str) -> SendPreview:
    """Preview one exact external write without resolving auth or making a request."""

    return OutboxSendService(root).preview(draft_id, channel, target)


def send(
    root: Path,
    draft_id: str,
    channel: str,
    target: str,
    *,
    approved: bool,
    fingerprint: str,
    transport: httpx.BaseTransport | None = None,
    clock: Clock | None = None,
) -> SendResult:
    """Send one approved draft and record one audited delivery transition."""

    return OutboxSendService(root, clock=clock).send(
        draft_id,
        channel,
        target,
        approved=approved,
        fingerprint=fingerprint,
        transport=transport,
    )


def _adapter(channel: str) -> SendChannel[Literal["github"], _GitHubTarget]:
    if channel != "github":
        raise SendInputError("The only supported outbox channel is github.")
    return GitHubSendChannel()


def _send_fingerprint(draft_hash: str, channel: str, target: str) -> str:
    material = f"{draft_hash}{channel}{target}".encode()
    return _hash_bytes(material)


def _run_github_delivery(
    target: _GitHubTarget,
    body: str,
    *,
    transport: httpx.BaseTransport | None,
) -> _DeliveryOutcome:
    secret = _resolve_github_token()
    if secret is None:
        return _DeliveryOutcome(failure=_DeliveryFailure(_DeliveryFailureKind.AUTHENTICATION))

    boundary: _GitHubRequestBoundaryTransport | None = None
    client: httpx.Client | None = None
    response: httpx.Response | None = None
    outcome: _DeliveryOutcome | None = None
    try:
        boundary = _GitHubRequestBoundaryTransport(
            transport or httpx.HTTPTransport(retries=0, trust_env=False),
            secret,
        )
        client = httpx.Client(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=False,
            transport=boundary,
            trust_env=False,
        )
        request = client.build_request(
            "POST",
            target.endpoint,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "workctx-outbox",
            },
            json={"body": body},
        )
        response = client.send(request, stream=True)
        outcome = _github_response_outcome(response, target)
    except httpx.TimeoutException:
        outcome = _DeliveryOutcome(failure=_DeliveryFailure(_DeliveryFailureKind.TIMEOUT))
    except Exception:
        outcome = _DeliveryOutcome(failure=_DeliveryFailure(_DeliveryFailureKind.CONNECTION))
    finally:
        if response is not None:
            _close_response(response)
        if client is not None:
            _close_client(client)
        if boundary is not None:
            boundary.clear_secret()
        response = None
        client = None
        boundary = None
        secret = None
    return outcome or _DeliveryOutcome(failure=_DeliveryFailure(_DeliveryFailureKind.CONNECTION))


def _github_response_outcome(
    response: httpx.Response,
    target: _GitHubTarget,
) -> _DeliveryOutcome:
    if response.status_code != 201:
        return _DeliveryOutcome(
            failure=_DeliveryFailure(
                _DeliveryFailureKind.STATUS,
                status_code=response.status_code,
            )
        )
    body = bytearray()
    try:
        if response.is_stream_consumed:
            content = response.content
            if len(content) > _GITHUB_RESPONSE_LIMIT:
                return _DeliveryOutcome(failure=_DeliveryFailure(_DeliveryFailureKind.RESPONSE))
            body.extend(content)
        else:
            for chunk in response.iter_raw():
                if len(body) + len(chunk) > _GITHUB_RESPONSE_LIMIT:
                    body.clear()
                    return _DeliveryOutcome(failure=_DeliveryFailure(_DeliveryFailureKind.RESPONSE))
                body.extend(chunk)
    except httpx.TimeoutException:
        body.clear()
        return _DeliveryOutcome(failure=_DeliveryFailure(_DeliveryFailureKind.TIMEOUT))
    except Exception:
        body.clear()
        return _DeliveryOutcome(failure=_DeliveryFailure(_DeliveryFailureKind.CONNECTION))

    try:
        payload = json.loads(bytes(body))
    except (UnicodeError, json.JSONDecodeError):
        body.clear()
        return _DeliveryOutcome(failure=_DeliveryFailure(_DeliveryFailureKind.RESPONSE))
    body.clear()
    if not isinstance(payload, dict):
        return _DeliveryOutcome(failure=_DeliveryFailure(_DeliveryFailureKind.RESPONSE))
    comment_id_value = payload.get("id")
    remote_url = payload.get("html_url")
    if (
        isinstance(comment_id_value, bool)
        or not isinstance(comment_id_value, int)
        or comment_id_value < 1
        or not isinstance(remote_url, str)
    ):
        return _DeliveryOutcome(failure=_DeliveryFailure(_DeliveryFailureKind.RESPONSE))
    comment_id = str(comment_id_value)
    if not _remote_matches_target(target, comment_id, remote_url):
        return _DeliveryOutcome(failure=_DeliveryFailure(_DeliveryFailureKind.RESPONSE))
    return _DeliveryOutcome(comment=_RemoteComment(comment_id=comment_id, url=remote_url))


def _remote_matches_target(target: _GitHubTarget, comment_id: str, url: str) -> bool:
    match = _GITHUB_COMMENT_URL.fullmatch(url)
    return bool(
        match is not None
        and match.group("owner").casefold() == target.owner.casefold()
        and match.group("repo").casefold() == target.repository.casefold()
        and match.group("number") == str(target.number)
        and match.group("comment_id") == comment_id
    )


def _resolve_github_token() -> SecretValue | None:
    resolved: SecretValue | None = None
    try:
        resolved = resolve(_GITHUB_SECRET_REF)
    except Exception:
        resolved = None
    if resolved is not None:
        return resolved

    environment_value = os.environ.get(_GITHUB_ENV_VAR)
    if environment_value:
        return SecretValue(environment_value)
    environment_value = None

    completed: subprocess.CompletedProcess[str] | None = None
    try:
        completed = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except Exception:
        completed = None
    if completed is None or completed.returncode != 0:
        completed = None
        return None
    token_text = completed.stdout.strip()
    completed = None
    if (
        not token_text
        or len(token_text) > 16_384
        or any(character.isspace() for character in token_text)
    ):
        token_text = ""
        return None
    value = SecretValue(token_text)
    token_text = ""
    return value


def _sanitize_response_logging_metadata(response: httpx.Response) -> None:
    response.extensions["reason_phrase"] = b""
    if response.extensions.get("http_version") not in {
        None,
        b"HTTP/1.0",
        b"HTTP/1.1",
        b"HTTP/2",
        b"HTTP/3",
    }:
        response.extensions["http_version"] = b"HTTP"


def _close_response(response: httpx.Response) -> None:
    with suppress(Exception):
        response.close()


def _close_client(client: httpx.Client) -> None:
    with suppress(Exception):
        client.close()


def _raise_delivery_failure(failure: _DeliveryFailure) -> NoReturn:
    if failure.kind is _DeliveryFailureKind.AUTHENTICATION:
        raise SendAuthenticationError()
    if failure.kind is _DeliveryFailureKind.TIMEOUT:
        raise SendTimeoutError()
    if failure.kind is _DeliveryFailureKind.STATUS:
        raise SendStatusError(failure.status_code or 500)
    if failure.kind is _DeliveryFailureKind.RESPONSE:
        raise SendResponseError()
    raise SendConnectionError()


def _hash_bytes(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _proposal_id(created_at: datetime, draft_id: str) -> str:
    slug = f"draft-send-{draft_id.lower()}-{secrets.token_hex(4)}"
    return f"TXP-{created_at.astimezone(UTC):%Y%m%dT%H%M%SZ}-{slug}"


def _normalize_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("outbox send clocks must be timezone-aware")
    return value.astimezone(UTC).replace(microsecond=0)


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "Clock",
    "GitHubSendChannel",
    "OutboxSendService",
    "SendChannel",
    "SendPreview",
    "SendResult",
    "preview_send",
    "send",
]
