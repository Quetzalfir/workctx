"""Canonical hash-chained audit ledger operations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from workctx.adapters.filesystem import (
    CanonicalStore,
    ContextLock,
    ContextZone,
    atomic_append_line_bytes,
    dump_json_bytes,
)
from workctx.domain.transactions import ZERO_REVISION, AuditEvent
from workctx.errors import ContextBoundaryError
from workctx.transactions.errors import LedgerIntegrityError

LEDGER_RELATIVE_PATH = "99_meta/audit/ledger.jsonl"


@dataclass(frozen=True, slots=True)
class LedgerVerification:
    """Successful verification metadata for the canonical audit ledger."""

    context_id: str
    event_count: int
    head_hash: str
    first_event_id: str | None
    last_event_id: str | None
    valid: bool = True


@dataclass(frozen=True, slots=True)
class AuditSummary:
    """Compact, verified audit-ledger state for callers and diagnostics."""

    context_id: str
    event_count: int
    head_hash: str
    first_event_id: str | None
    last_event_id: str | None
    last_proposal_id: str | None
    last_timestamp: datetime | None


def compute_event_hash(event: AuditEvent) -> str:
    """Return the ADR 0010 SHA-256 digest for ``event``.

    The hash covers the compact canonical JSON object without a line delimiter and
    with ``event_hash`` represented by an empty string.
    """

    unhashed = event.model_copy(update={"event_hash": ""})
    return hashlib.sha256(_canonical_event_bytes(unhashed)).hexdigest()


def encode_event_line(event: AuditEvent) -> bytes:
    """Encode one event as its compact canonical UTF-8 JSONL line."""

    return _canonical_event_bytes(event) + b"\n"


def verify_ledger(context_root: Path) -> LedgerVerification:
    """Strictly parse and verify the complete ledger, raising on any mismatch."""

    store = CanonicalStore(context_root)
    events = _read_verified_events(store)
    return _verification(store, events)


def audit_summary(context_root: Path) -> AuditSummary:
    """Return a compact summary only after verifying the entire ledger."""

    store = CanonicalStore(context_root)
    events = _read_verified_events(store)
    last = events[-1] if events else None
    return AuditSummary(
        context_id=store.context_id,
        event_count=len(events),
        head_hash=last.event_hash if last is not None else ZERO_REVISION,
        first_event_id=events[0].id if events else None,
        last_event_id=last.id if last is not None else None,
        last_proposal_id=last.proposal_id if last is not None else None,
        last_timestamp=last.timestamp if last is not None else None,
    )


def find_event_by_proposal_id(context_root: Path, proposal_id: str) -> AuditEvent | None:
    """Return the unique event for a proposal after full-ledger verification."""

    if not proposal_id:
        raise ValueError("proposal_id must not be empty")
    store = CanonicalStore(context_root)
    events = _read_verified_events(store)
    return _find_unique(events, lambda event: event.proposal_id == proposal_id, "proposal id")


def find_event_by_id(context_root: Path, event_id: str) -> AuditEvent | None:
    """Return the unique event with ``event_id`` after full-ledger verification."""

    if not event_id:
        raise ValueError("event_id must not be empty")
    store = CanonicalStore(context_root)
    events = _read_verified_events(store)
    return _find_unique(events, lambda event: event.id == event_id, "event id")


def append_event(
    context_root: Path,
    event: AuditEvent,
    *,
    lock: ContextLock,
) -> AuditEvent:
    """Idempotently append one fully sealed event under an active context lock.

    Exact replay of an already committed event succeeds. Reuse of either an event
    id or proposal id for different bytes is an integrity error. The ledger is
    verified immediately before and after the atomic append; an ambiguous adapter
    failure is treated as success only when that post-read finds the exact event.
    """

    store = CanonicalStore(context_root)
    if lock.context_root != store.context_root:
        raise LedgerIntegrityError()
    lock.verify_fence()

    before = _read_verified_events(store)
    replay = _matching_replay(before, event)
    if replay is not None:
        return replay

    expected_head = before[-1].event_hash if before else ZERO_REVISION
    if event.context_id != store.context_id:
        raise LedgerIntegrityError()
    if event.prev_hash != expected_head:
        raise LedgerIntegrityError()
    expected_hash = compute_event_hash(event)
    if event.event_hash != expected_hash:
        raise LedgerIntegrityError()

    append_error: Exception | None = None
    try:
        atomic_append_line_bytes(
            store.context_root,
            LEDGER_RELATIVE_PATH,
            encode_event_line(event),
            nonce=lock.nonce,
            lock=lock,
        )
    except Exception as exc:  # the post-read resolves an ambiguous commit outcome
        append_error = exc

    after = _read_verified_events(store)
    committed = _matching_replay(after, event)
    if committed is not None:
        return committed
    if append_error is not None:
        raise append_error
    raise LedgerIntegrityError()


def _canonical_event_bytes(event: AuditEvent) -> bytes:
    try:
        # Use the WP-200 public serializer to apply ADR 0005 model ordering and
        # null/omit rules, then remove presentation whitespace for ADR 0010 JSONL.
        data = json.loads(dump_json_bytes(event))
        return json.dumps(
            data,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LedgerIntegrityError() from exc


def _read_verified_events(store: CanonicalStore) -> tuple[AuditEvent, ...]:
    try:
        path = store.resolve_path(LEDGER_RELATIVE_PATH, zones=(ContextZone.META,))
    except ContextBoundaryError as exc:
        raise LedgerIntegrityError() from exc
    raw = _read_ledger_bytes(path)
    if raw is None:
        return ()
    if not raw:
        raise LedgerIntegrityError()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise LedgerIntegrityError()
    if b"\r" in raw:
        raise LedgerIntegrityError()
    if not raw.endswith(b"\n"):
        raise LedgerIntegrityError()
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LedgerIntegrityError() from exc

    lines = raw[:-1].split(b"\n")
    if any(not line for line in lines):
        raise LedgerIntegrityError()

    events: list[AuditEvent] = []
    event_ids: set[str] = set()
    proposal_ids: set[str] = set()
    expected_prev_hash = ZERO_REVISION
    for line_number, line in enumerate(lines, start=1):
        event = _decode_event(line, line_number=line_number)
        if event.context_id != store.context_id:
            raise LedgerIntegrityError()
        if encode_event_line(event)[:-1] != line:
            raise LedgerIntegrityError()
        if event.id in event_ids:
            raise LedgerIntegrityError()
        if event.proposal_id in proposal_ids:
            raise LedgerIntegrityError()
        if event.prev_hash != expected_prev_hash:
            raise LedgerIntegrityError()
        if event.event_hash != compute_event_hash(event):
            raise LedgerIntegrityError()

        events.append(event)
        event_ids.add(event.id)
        proposal_ids.add(event.proposal_id)
        expected_prev_hash = event.event_hash
    return tuple(events)


def _read_ledger_bytes(path: Path) -> bytes | None:
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise LedgerIntegrityError()
    if not path.exists():
        return None
    if not path.is_file():
        raise LedgerIntegrityError()
    try:
        with path.open("rb") as stream:
            return stream.read()
    except OSError as exc:
        raise LedgerIntegrityError() from exc


def _decode_event(line: bytes, *, line_number: int) -> AuditEvent:
    try:
        loaded: Any = json.loads(
            line.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_object_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise LedgerIntegrityError() from exc
    if not isinstance(loaded, dict):
        raise LedgerIntegrityError()
    try:
        return AuditEvent.model_validate(loaded)
    except ValidationError as exc:
        raise LedgerIntegrityError() from exc


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Audit ledger JSON contains a duplicate object key")
        result[key] = value
    return result


def _verification(
    store: CanonicalStore,
    events: tuple[AuditEvent, ...],
) -> LedgerVerification:
    return LedgerVerification(
        context_id=store.context_id,
        event_count=len(events),
        head_hash=events[-1].event_hash if events else ZERO_REVISION,
        first_event_id=events[0].id if events else None,
        last_event_id=events[-1].id if events else None,
    )


def _find_unique(
    events: tuple[AuditEvent, ...],
    predicate: Callable[[AuditEvent], bool],
    identity_name: str,
) -> AuditEvent | None:
    matches = tuple(event for event in events if predicate(event))
    if len(matches) > 1:
        raise LedgerIntegrityError()
    return matches[0] if matches else None


def _matching_replay(
    events: tuple[AuditEvent, ...],
    candidate: AuditEvent,
) -> AuditEvent | None:
    matching_id = _find_unique(events, lambda event: event.id == candidate.id, "event id")
    matching_proposal = _find_unique(
        events,
        lambda event: event.proposal_id == candidate.proposal_id,
        "proposal id",
    )
    if matching_id is None and matching_proposal is None:
        return None
    if matching_id != matching_proposal or matching_id != candidate:
        raise LedgerIntegrityError()
    return matching_id
