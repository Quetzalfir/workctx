from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import traceback
from pathlib import Path
from typing import Any

import httpx
import pytest

import workctx.drafting.delivery as delivery_runtime
from workctx.adapters.filesystem import CanonicalStore
from workctx.drafting import (
    DraftStateError,
    OutboxSendService,
    SendApprovalRequiredError,
    SendAuditCommitError,
    SendConnectionError,
    SendFingerprintMismatchError,
    SendInputError,
    SendResponseError,
    SendSecretError,
    SendStateError,
    SendStatusError,
    SendTimeoutError,
    get_draft,
    preview_send,
    save_draft,
    send,
)
from workctx.secrets import delete, store
from workctx.transactions import read_audit_events, verify_ledger

from .support import (
    DRAFT_ID,
    FICTIONAL_TOKEN,
    FIXED_SEND_TIME,
    GITHUB_COMMENT_ID,
    GITHUB_COMMENT_URL,
    GITHUB_TARGET,
    MutableClock,
    draft_payload,
    initialize_drafting_context,
    workspace_file_bytes,
)


def _saved_context(root: Path) -> Path:
    initialize_drafting_context(root)
    save_draft(root, draft_payload(), approved=True)
    return root


def _success_transport(
    captured: list[httpx.Request],
    *,
    comment_id: int = int(GITHUB_COMMENT_ID),
    url: str = GITHUB_COMMENT_URL,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            201,
            json={"id": comment_id, "html_url": url},
            request=request,
        )

    return httpx.MockTransport(handler)


def _workspace_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_preview_is_deterministic_and_fingerprint_pins_hash_channel_and_target(
    tmp_path: Path,
) -> None:
    root = _saved_context(tmp_path / "preview")

    first = preview_send(root, DRAFT_ID, "github", GITHUB_TARGET)
    second = preview_send(root, DRAFT_ID, "github", GITHUB_TARGET)

    assert first == second
    assert first.operation == "preview"
    assert first.body == draft_payload().body
    assert first.recipient_display == (
        "fictional-org/rollout-tracker#17 (GitHub issue or pull request)"
    )
    expected_draft_hash = (
        "sha256:" + hashlib.sha256((root / "05_outbox" / f"{DRAFT_ID}.md").read_bytes()).hexdigest()
    )
    expected_fingerprint = (
        "sha256:"
        + hashlib.sha256(f"{expected_draft_hash}github{GITHUB_TARGET}".encode()).hexdigest()
    )
    assert first.draft_content_hash == expected_draft_hash
    assert first.fingerprint == expected_fingerprint
    assert verify_ledger(root).event_count == 1


@pytest.mark.parametrize(
    "target",
    (
        "https://github.com/fictional-org/rollout-tracker/issues/17",
        "fictional-org/rollout-tracker#0",
        "fictional-org/rollout-tracker#017",
        "fictional-org/team/rollout-tracker#17",
        "fictional org/rollout-tracker#17",
        "fictional-org/.#17",
        "fictional-org/..#17",
    ),
)
def test_preview_refuses_noncanonical_or_url_github_targets(
    tmp_path: Path,
    target: str,
) -> None:
    root = _saved_context(tmp_path / "invalid-target")

    with pytest.raises(SendInputError):
        preview_send(root, DRAFT_ID, "github", target)

    with pytest.raises(SendInputError):
        preview_send(root, DRAFT_ID, "email", GITHUB_TARGET)


def test_send_refuses_without_approval_and_on_any_fingerprint_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _saved_context(tmp_path / "fingerprints")
    monkeypatch.setenv("WORKCTX_SECRET_GITHUB_TOKEN", FICTIONAL_TOKEN)
    preview = preview_send(root, DRAFT_ID, "github", GITHUB_TARGET)
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(201, request=request)

    transport = httpx.MockTransport(handler)
    with pytest.raises(SendApprovalRequiredError):
        send(
            root,
            DRAFT_ID,
            "github",
            GITHUB_TARGET,
            approved=False,
            fingerprint=preview.fingerprint,
            transport=transport,
        )
    assert called is False

    save_draft(
        root,
        draft_payload(body="Hello Alex,\n\nThis local revision changes the approval hash.\n"),
        approved=True,
    )
    ledger_after_edit = verify_ledger(root).event_count
    with pytest.raises(SendFingerprintMismatchError):
        send(
            root,
            DRAFT_ID,
            "github",
            GITHUB_TARGET,
            approved=True,
            fingerprint=preview.fingerprint,
            transport=transport,
        )
    assert called is False
    assert verify_ledger(root).event_count == ledger_after_edit

    current = preview_send(root, DRAFT_ID, "github", GITHUB_TARGET)
    with pytest.raises(SendFingerprintMismatchError):
        send(
            root,
            DRAFT_ID,
            "github",
            "fictional-org/rollout-tracker#18",
            approved=True,
            fingerprint=current.fingerprint,
            transport=transport,
        )
    assert called is False


def test_send_reruns_secret_scan_as_the_last_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _saved_context(tmp_path / "last-secret-gate")
    preview = preview_send(root, DRAFT_ID, "github", GITHUB_TARGET)
    before = _workspace_snapshot(root)
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(201, request=request)

    monkeypatch.setattr(delivery_runtime, "contains_possible_secret", lambda _body: True)
    with pytest.raises(SendSecretError):
        send(
            root,
            DRAFT_ID,
            "github",
            GITHUB_TARGET,
            approved=True,
            fingerprint=preview.fingerprint,
            transport=httpx.MockTransport(handler),
        )

    assert called is False
    assert _workspace_snapshot(root) == before


def test_success_posts_exact_body_then_commits_sent_provenance_and_one_ledger_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    root = _saved_context(tmp_path / "success")
    monkeypatch.setenv("WORKCTX_SECRET_GITHUB_TOKEN", FICTIONAL_TOKEN)
    preview = preview_send(root, DRAFT_ID, "github", GITHUB_TARGET)
    captured: list[httpx.Request] = []
    clock = MutableClock(FIXED_SEND_TIME)

    result = send(
        root,
        DRAFT_ID,
        "github",
        GITHUB_TARGET,
        approved=True,
        fingerprint=preview.fingerprint,
        transport=_success_transport(captured),
        clock=clock,
    )

    assert len(captured) == 1
    request = captured[0]
    assert request.method == "POST"
    assert str(request.url) == (
        "https://api.github.com/repos/fictional-org/rollout-tracker/issues/17/comments"
    )
    assert request.headers["Authorization"] == f"Bearer {FICTIONAL_TOKEN}"
    assert json.loads(request.content) == {"body": preview.body}
    assert result.operation == "sent"
    assert result.delivery.channel == "github"
    assert result.delivery.target == GITHUB_TARGET
    assert result.delivery.remote_comment_id == GITHUB_COMMENT_ID
    assert result.delivery.remote_comment_url == GITHUB_COMMENT_URL
    assert result.delivery.sent_at == FIXED_SEND_TIME
    assert result.receipt.committed is True
    assert FICTIONAL_TOKEN not in result.model_dump_json()
    assert FICTIONAL_TOKEN not in caplog.text

    stored = get_draft(root, DRAFT_ID)
    assert stored == result.draft
    assert stored.delivery_state == "sent"
    assert stored.delivery == result.delivery
    assert stored.body == preview.body
    entity = CanonicalStore(root).read_entity(stored.path)
    assert entity.frontmatter.tags == ["outbox", "sent"]
    events = read_audit_events(root)
    assert len(events) == 2
    assert events[-1].id == result.receipt.ledger_event_id
    assert events[-1].timestamp == FIXED_SEND_TIME
    assert events[-1].actor.id == "workctx-outbox-send"
    assert events[-1].source_refs[0] == stored.uri
    assert all(FICTIONAL_TOKEN.encode() not in content for content in workspace_file_bytes(root))

    with pytest.raises(SendStateError):
        preview_send(root, DRAFT_ID, "github", GITHUB_TARGET)
    with pytest.raises(SendStateError):
        send(
            root,
            DRAFT_ID,
            "github",
            GITHUB_TARGET,
            approved=True,
            fingerprint=preview.fingerprint,
            transport=_success_transport(captured),
            clock=clock,
        )
    with pytest.raises(DraftStateError):
        save_draft(root, draft_payload(body="A forbidden post-send revision.\n"), approved=True)
    assert len(captured) == 1
    assert verify_ledger(root).event_count == 2


@pytest.mark.parametrize(
    ("failure_mode", "error_type"),
    (
        ("connection", SendConnectionError),
        ("timeout", SendTimeoutError),
        ("status", SendStatusError),
        ("response", SendResponseError),
    ),
)
def test_delivery_failures_are_content_free_and_make_no_canonical_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure_mode: str,
    error_type: type[Exception],
) -> None:
    caplog.set_level(logging.DEBUG)
    root = _saved_context(tmp_path / failure_mode)
    monkeypatch.setenv("WORKCTX_SECRET_GITHUB_TOKEN", FICTIONAL_TOKEN)
    preview = preview_send(root, DRAFT_ID, "github", GITHUB_TARGET)
    before = _workspace_snapshot(root)

    def handler(request: httpx.Request) -> httpx.Response:
        if failure_mode == "connection":
            raise httpx.ConnectError(
                f"fictional backend echoed {FICTIONAL_TOKEN}",
                request=request,
            )
        if failure_mode == "timeout":
            raise httpx.ReadTimeout(
                f"fictional backend echoed {FICTIONAL_TOKEN}",
                request=request,
            )
        if failure_mode == "status":
            return httpx.Response(
                503,
                headers={"X-Fictional-Debug": FICTIONAL_TOKEN},
                content=f"hostile status body {FICTIONAL_TOKEN}".encode(),
                extensions={
                    "http_version": FICTIONAL_TOKEN.encode(),
                    "reason_phrase": FICTIONAL_TOKEN.encode(),
                },
                request=request,
            )
        return httpx.Response(
            201,
            content=f'{{"id": 1, "html_url": "{FICTIONAL_TOKEN}"}}'.encode(),
            request=request,
        )

    with pytest.raises(error_type) as caught:
        send(
            root,
            DRAFT_ID,
            "github",
            GITHUB_TARGET,
            approved=True,
            fingerprint=preview.fingerprint,
            transport=httpx.MockTransport(handler),
            clock=lambda: FIXED_SEND_TIME,
        )

    error = caught.value
    surfaces = (
        str(error),
        repr(error),
        json.dumps(vars(error), default=str, sort_keys=True),
        "".join(traceback.format_exception(type(error), error, error.__traceback__)),
        caplog.text,
    )
    assert all(FICTIONAL_TOKEN not in surface for surface in surfaces)
    assert error.__cause__ is None
    assert error.__context__ is None
    current_traceback = error.__traceback__
    while current_traceback is not None:
        frame_path = current_traceback.tb_frame.f_code.co_filename.replace("\\", "/")
        if "/src/workctx/drafting/" in frame_path:
            assert FICTIONAL_TOKEN not in repr(current_traceback.tb_frame.f_locals)
        current_traceback = current_traceback.tb_next
    assert _workspace_snapshot(root) == before
    assert get_draft(root, DRAFT_ID).delivery_state == "unsent"
    assert all(FICTIONAL_TOKEN.encode() not in content for content in workspace_file_bytes(root))


def test_token_chain_prefers_secret_ref_then_github_env_then_captured_gh_stdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials = (
        ("secret-ref", "fictional-secret-ref-token-720"),
        ("github-env", "fictional-github-env-token-720"),
        ("gh-stdout", "fictional-gh-stdout-token-720"),
    )
    for index, (layer, token) in enumerate(credentials):
        delete("github-token")
        root = _saved_context(tmp_path / f"chain-{index}")
        preview = preview_send(root, DRAFT_ID, "github", GITHUB_TARGET)
        captured: list[httpx.Request] = []
        calls: list[tuple[list[str], dict[str, Any]]] = []
        monkeypatch.delenv("WORKCTX_SECRET_GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        if layer == "secret-ref":
            store("github-token", token)
            monkeypatch.setenv("GITHUB_TOKEN", "must-not-win")
        elif layer == "github-env":
            monkeypatch.setenv("GITHUB_TOKEN", token)
        else:

            def fake_run(
                args: list[str],
                *,
                _token: str = token,
                _calls: list[tuple[list[str], dict[str, Any]]] = calls,
                **kwargs: Any,
            ) -> subprocess.CompletedProcess[str]:
                _calls.append((args, kwargs))
                return subprocess.CompletedProcess(
                    args,
                    0,
                    stdout=f"{_token}\n",
                    stderr=f"discarded hostile stderr {_token}",
                )

            monkeypatch.setattr(delivery_runtime.subprocess, "run", fake_run)

        result = send(
            root,
            DRAFT_ID,
            "github",
            GITHUB_TARGET,
            approved=True,
            fingerprint=preview.fingerprint,
            transport=_success_transport(captured),
            clock=lambda: FIXED_SEND_TIME,
        )

        assert captured[0].headers["Authorization"] == f"Bearer {token}"
        assert token not in result.model_dump_json()
        assert all(token.encode() not in content for content in workspace_file_bytes(root))
        if layer == "gh-stdout":
            assert len(calls) == 1
            argv, kwargs = calls[0]
            assert argv == ["gh", "auth", "token"]
            assert token not in repr(argv)
            assert kwargs == {
                "capture_output": True,
                "check": False,
                "text": True,
                "timeout": 10,
            }


def test_remote_success_with_local_commit_failure_surfaces_reconciliation_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _saved_context(tmp_path / "audit-failure")
    monkeypatch.setenv("WORKCTX_SECRET_GITHUB_TOKEN", FICTIONAL_TOKEN)
    preview = preview_send(root, DRAFT_ID, "github", GITHUB_TARGET)
    before = _workspace_snapshot(root)
    captured: list[httpx.Request] = []

    def fail_apply(*_args: object, **_kwargs: object) -> Any:
        raise RuntimeError(f"hostile transaction detail {FICTIONAL_TOKEN}")

    service = OutboxSendService(
        root,
        clock=lambda: FIXED_SEND_TIME,
        transaction_apply=fail_apply,
    )
    with pytest.raises(SendAuditCommitError) as caught:
        service.send(
            DRAFT_ID,
            "github",
            GITHUB_TARGET,
            approved=True,
            fingerprint=preview.fingerprint,
            transport=_success_transport(captured),
        )

    assert len(captured) == 1
    assert caught.value.remote_comment_id == GITHUB_COMMENT_ID
    assert caught.value.remote_comment_url == GITHUB_COMMENT_URL
    assert "do not resend" in str(caught.value)
    assert FICTIONAL_TOKEN not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert _workspace_snapshot(root) == before
