from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import workctx.transactions.ledger as ledger_module
from workctx.adapters.filesystem import ContextLock
from workctx.domain.transactions import (
    ZERO_REVISION,
    AuditCreateOperation,
    AuditEvent,
    AuditEventContent,
    HumanActor,
    SystemActor,
)
from workctx.services.contexts import initialize_context
from workctx.transactions.errors import LedgerIntegrityError
from workctx.transactions.ledger import (
    LEDGER_RELATIVE_PATH,
    append_event,
    audit_summary,
    compute_event_hash,
    encode_event_line,
    find_event_by_id,
    find_event_by_proposal_id,
    verify_ledger,
)


@pytest.fixture
def context_root(tmp_path: Path) -> Path:
    root = tmp_path / "ledger-context"
    initialize_context(root, name="Ledger Context", context_id="ledger-context")
    return root


def _event(
    sequence: int,
    prev_hash: str,
    *,
    context_id: str = "ledger-context",
    action: str = "apply",
    result: str = "committed",
) -> AuditEvent:
    timestamp = datetime(2026, 8, 1, 12, 0, sequence, tzinfo=UTC)
    suffix = f"20260801T1200{sequence:02d}Z-ledger-{sequence}"
    content = AuditEventContent.model_validate(
        {
            "schema_version": 1,
            "id": f"AUD-{suffix}",
            "proposal_id": f"TXP-{suffix}",
            "context_id": context_id,
            "timestamp": timestamp,
            "actor": (
                SystemActor(
                    type="system",
                    id="workctx-transaction-recovery",
                    agent=None,
                    model=None,
                )
                if action == "recovery"
                else HumanActor(
                    type="human",
                    id="fictional-operator",
                    agent=None,
                    model=None,
                )
            ),
            "action": action,
            "result": result,
            "base_revision": prev_hash,
            "source_refs": [],
            "operations": [
                AuditCreateOperation(
                    op="create",
                    target=f"03_work/TASK-2026-{sequence:03d}.md",
                    postimage_hash=f"sha256:{sequence:064x}",
                )
            ],
            "prev_hash": prev_hash,
        }
    )
    return AuditEvent.seal(content)


def _ledger_path(context_root: Path) -> Path:
    return context_root / Path(LEDGER_RELATIVE_PATH)


def _write_ledger(context_root: Path, content: bytes) -> None:
    path = _ledger_path(context_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_missing_ledger_is_a_valid_genesis(context_root: Path) -> None:
    verification = verify_ledger(context_root)
    summary = audit_summary(context_root)

    assert verification.valid
    assert verification.context_id == "ledger-context"
    assert verification.event_count == 0
    assert verification.head_hash == ZERO_REVISION
    assert verification.first_event_id is None
    assert verification.last_event_id is None
    assert summary.event_count == 0
    assert summary.head_hash == ZERO_REVISION
    assert summary.last_proposal_id is None
    assert summary.last_timestamp is None
    assert find_event_by_proposal_id(context_root, "TXP-20260801T120001Z-missing") is None
    assert find_event_by_id(context_root, "AUD-20260801T120001Z-missing") is None


def test_encoding_and_hash_match_the_domain_contract() -> None:
    event = _event(1, ZERO_REVISION)
    line = encode_event_line(event)

    assert line == event.canonical_line_bytes()
    assert compute_event_hash(event) == event.event_hash
    assert line.endswith(b"\n")
    assert line.count(b"\n") == 1
    assert b"\r" not in line
    assert line.startswith(b'{"schema_version":1,"id":"AUD-')
    assert line.endswith(f'"event_hash":"{event.event_hash}"}}\n'.encode())


def test_append_verifies_chain_lookups_summary_and_exact_replay(context_root: Path) -> None:
    first = _event(1, ZERO_REVISION)
    second = _event(2, first.event_hash)

    with ContextLock.acquire(context_root, session_id="ledger", tool_version="test") as lock:
        assert append_event(context_root, first, lock=lock) == first
        first_bytes = _ledger_path(context_root).read_bytes()
        assert append_event(context_root, first, lock=lock) == first
        assert _ledger_path(context_root).read_bytes() == first_bytes
        assert append_event(context_root, second, lock=lock) == second

    verification = verify_ledger(context_root)
    summary = audit_summary(context_root)
    assert verification.event_count == 2
    assert verification.head_hash == second.event_hash
    assert verification.first_event_id == first.id
    assert verification.last_event_id == second.id
    assert summary.last_event_id == second.id
    assert summary.last_proposal_id == second.proposal_id
    assert summary.last_timestamp == second.timestamp
    assert find_event_by_proposal_id(context_root, first.proposal_id) == first
    assert find_event_by_id(context_root, second.id) == second
    assert _ledger_path(context_root).read_bytes() == encode_event_line(first) + encode_event_line(
        second
    )


def test_append_recovers_an_ambiguous_adapter_error_after_commit(
    context_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = _event(1, ZERO_REVISION)
    real_append = ledger_module.atomic_append_line_bytes
    calls = 0

    def append_then_raise(*args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        real_append(*args, **kwargs)  # type: ignore[arg-type]
        raise OSError("simulated ambiguous adapter result")

    monkeypatch.setattr(ledger_module, "atomic_append_line_bytes", append_then_raise)
    with ContextLock.acquire(context_root, session_id="ambiguous", tool_version="test") as lock:
        assert append_event(context_root, event, lock=lock) == event

    assert calls == 1
    assert _ledger_path(context_root).read_bytes() == encode_event_line(event)
    assert verify_ledger(context_root).head_hash == event.event_hash


def test_append_refuses_reused_identity_with_different_content(context_root: Path) -> None:
    committed = _event(1, ZERO_REVISION)
    rolled_back = _event(
        1,
        ZERO_REVISION,
        action="recovery",
        result="rolled_back",
    )

    with ContextLock.acquire(context_root, session_id="collision", tool_version="test") as lock:
        append_event(context_root, committed, lock=lock)
        before = _ledger_path(context_root).read_bytes()
        with pytest.raises(LedgerIntegrityError):
            append_event(context_root, rolled_back, lock=lock)

    assert _ledger_path(context_root).read_bytes() == before


def test_append_refuses_wrong_context_head_or_event_hash(context_root: Path) -> None:
    wrong_context = _event(1, ZERO_REVISION, context_id="other-context")
    wrong_head = _event(2, "1" * 64)
    valid = _event(3, ZERO_REVISION)
    wrong_hash = valid.model_copy(update={"event_hash": "f" * 64})

    with ContextLock.acquire(context_root, session_id="reject", tool_version="test") as lock:
        for candidate in (wrong_context, wrong_head, wrong_hash):
            with pytest.raises(LedgerIntegrityError):
                append_event(context_root, candidate, lock=lock)

    assert not _ledger_path(context_root).exists()


@pytest.mark.parametrize(
    "case",
    [
        "empty",
        "bom",
        "crlf",
        "blank",
        "truncated",
        "invalid-utf8",
        "noncanonical",
        "duplicate-key",
    ],
)
def test_verify_rejects_noncanonical_or_malformed_ledger(
    context_root: Path,
    case: str,
) -> None:
    canonical = encode_event_line(_event(1, ZERO_REVISION))
    if case == "empty":
        raw = b""
    elif case == "bom":
        raw = b"\xef\xbb\xbf" + canonical
    elif case == "crlf":
        raw = canonical[:-1] + b"\r\n"
    elif case == "blank":
        raw = canonical + b"\n"
    elif case == "truncated":
        raw = canonical[:-1]
    elif case == "invalid-utf8":
        raw = b"\xff\n"
    elif case == "noncanonical":
        parsed = json.loads(canonical)
        raw = json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        assert raw != canonical
    else:
        raw = canonical.replace(
            b'{"schema_version":1,',
            b'{"schema_version":1,"schema_version":1,',
            1,
        )
    _write_ledger(context_root, raw)

    with pytest.raises(LedgerIntegrityError):
        verify_ledger(context_root)


def test_tampered_middle_event_invalidates_verification_and_lookups(context_root: Path) -> None:
    first = _event(1, ZERO_REVISION)
    second = _event(2, first.event_hash)
    with ContextLock.acquire(context_root, session_id="tamper", tool_version="test") as lock:
        append_event(context_root, first, lock=lock)
        append_event(context_root, second, lock=lock)

    ledger_path = _ledger_path(context_root)
    tampered = ledger_path.read_bytes().replace(
        b'"result":"committed"',
        b'"result":"rolled_back"',
        1,
    )
    ledger_path.write_bytes(tampered)

    with pytest.raises(LedgerIntegrityError):
        verify_ledger(context_root)
    with pytest.raises(LedgerIntegrityError):
        audit_summary(context_root)
    with pytest.raises(LedgerIntegrityError):
        find_event_by_proposal_id(context_root, second.proposal_id)


def test_duplicate_event_and_proposal_identity_is_rejected(context_root: Path) -> None:
    line = encode_event_line(_event(1, ZERO_REVISION))
    _write_ledger(context_root, line + line)

    with pytest.raises(LedgerIntegrityError):
        verify_ledger(context_root)
