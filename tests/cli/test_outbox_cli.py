"""Acceptance coverage for preview-pinned outbox send CLI envelopes."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from drafting.support import (
    DRAFT_ID,
    FICTIONAL_TOKEN,
    GITHUB_COMMENT_ID,
    GITHUB_COMMENT_URL,
    GITHUB_TARGET,
    draft_payload,
    initialize_drafting_context,
)
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

import workctx.drafting.delivery as delivery_runtime
from workctx.cli import app
from workctx.drafting import get_draft, save_draft
from workctx.transactions import verify_ledger

ROOT = Path(__file__).parents[2]
ENVELOPE_VALIDATOR = Draft202012Validator(
    json.loads((ROOT / "schemas" / "cli-envelope.schema.json").read_text(encoding="utf-8"))
)
runner = CliRunner()


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    """Avoid pytest's sandbox-hostile 0700 temporary directories."""

    parent = Path(tempfile.gettempdir()) / "workctx-outbox-cli-tests"
    parent.mkdir(mode=0o755, exist_ok=True)
    root = parent / f"case-{uuid4().hex}"
    root.mkdir(mode=0o755)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(autouse=True)
def no_real_external_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable_name in tuple(os.environ):
        if variable_name.startswith("WORKCTX_SECRET_") or variable_name == "GITHUB_TOKEN":
            monkeypatch.delenv(variable_name, raising=False)
    monkeypatch.setenv("WORKCTX_SECRET_GITHUB_TOKEN", FICTIONAL_TOKEN)

    def refuse_real_network(
        _transport: httpx.HTTPTransport,
        _request: httpx.Request,
    ) -> httpx.Response:
        raise AssertionError("outbox CLI tests must mock GitHub HTTP")

    def refuse_real_gh(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("outbox CLI tests must not invoke a real gh process")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse_real_network)
    monkeypatch.setattr(delivery_runtime.subprocess, "run", refuse_real_gh)


def _saved_context(root: Path) -> Path:
    initialize_drafting_context(root)
    save_draft(root, draft_payload(), approved=True)
    return root


def _envelope(result: Any, *, exit_code: int) -> dict[str, Any]:
    assert result.exit_code == exit_code, result.output
    payload: dict[str, Any] = json.loads(result.stdout)
    ENVELOPE_VALIDATOR.validate(payload)
    assert payload["command"] == "outbox.send"
    assert payload["ok"] is (exit_code == 0)
    if exit_code == 0:
        assert result.stderr == ""
    else:
        assert result.stderr.startswith("Error:")
    return payload


def _arguments(root: Path, *, target: str = GITHUB_TARGET) -> list[str]:
    return [
        "outbox",
        "send",
        DRAFT_ID,
        "--via",
        "github",
        "--target",
        target,
        "--context",
        str(root),
    ]


def test_json_preview_and_fingerprint_required_failure_are_non_mutating(tmp_path: Path) -> None:
    root = _saved_context(tmp_path / "preview")

    previewed = _envelope(
        runner.invoke(app, [*_arguments(root), "--json"]),
        exit_code=0,
    )
    preview = previewed["result"]
    assert preview["operation"] == "preview"
    assert preview["draft_id"] == DRAFT_ID
    assert preview["channel"] == "github"
    assert preview["target"] == GITHUB_TARGET
    assert preview["body"] == draft_payload().body
    assert preview["fingerprint"].startswith("sha256:")
    assert verify_ledger(root).event_count == 1

    missing = _envelope(
        runner.invoke(app, [*_arguments(root), "--yes", "--json"]),
        exit_code=2,
    )
    assert missing["result"] == preview
    assert missing["errors"] == [
        {
            "code": "OUTBOX_FINGERPRINT_REQUIRED",
            "message": "JSON send approval requires the exact preview fingerprint.",
            "path": "$.fingerprint",
            "repair_action": None,
        }
    ]
    assert get_draft(root, DRAFT_ID).delivery_state == "unsent"
    assert verify_ledger(root).event_count == 1


def test_json_send_success_and_target_swap_failure_have_canonical_envelopes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mismatch_root = _saved_context(tmp_path / "mismatch")
    mismatch_preview = _envelope(
        runner.invoke(app, [*_arguments(mismatch_root), "--json"]),
        exit_code=0,
    )["result"]
    mismatch = _envelope(
        runner.invoke(
            app,
            [
                *_arguments(mismatch_root, target="fictional-org/rollout-tracker#18"),
                "--yes",
                "--fingerprint",
                mismatch_preview["fingerprint"],
                "--json",
            ],
        ),
        exit_code=4,
    )
    assert mismatch["errors"][0]["code"] == "OUTBOX_FINGERPRINT_MISMATCH"
    assert get_draft(mismatch_root, DRAFT_ID).delivery_state == "unsent"

    root = _saved_context(tmp_path / "success")
    preview = _envelope(
        runner.invoke(app, [*_arguments(root), "--json"]),
        exit_code=0,
    )["result"]
    captured: list[httpx.Request] = []

    def handler(
        _transport: httpx.HTTPTransport,
        request: httpx.Request,
    ) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            201,
            json={"id": int(GITHUB_COMMENT_ID), "html_url": GITHUB_COMMENT_URL},
            request=request,
        )

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handler)
    sent = _envelope(
        runner.invoke(
            app,
            [
                *_arguments(root),
                "--yes",
                "--fingerprint",
                preview["fingerprint"],
                "--json",
            ],
        ),
        exit_code=0,
    )
    assert sent["result"]["operation"] == "sent"
    assert sent["result"]["draft"]["delivery_state"] == "sent"
    assert sent["result"]["delivery"]["remote_comment_url"] == GITHUB_COMMENT_URL
    assert sent["result"]["receipt"]["committed"] is True
    assert len(captured) == 1
    assert captured[0].headers["Authorization"] == f"Bearer {FICTIONAL_TOKEN}"
    assert get_draft(root, DRAFT_ID).delivery_state == "sent"
    assert verify_ledger(root).event_count == 2


def test_json_delivery_failure_is_content_free_and_preserves_unsent_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _saved_context(tmp_path / "failure")
    preview = _envelope(
        runner.invoke(app, [*_arguments(root), "--json"]),
        exit_code=0,
    )["result"]

    def handler(
        _transport: httpx.HTTPTransport,
        request: httpx.Request,
    ) -> httpx.Response:
        raise httpx.ReadTimeout(
            f"hostile transport detail {FICTIONAL_TOKEN}",
            request=request,
        )

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handler)
    result = runner.invoke(
        app,
        [
            *_arguments(root),
            "--yes",
            "--fingerprint",
            preview["fingerprint"],
            "--json",
        ],
    )
    failed = _envelope(result, exit_code=1)
    assert failed["errors"][0]["code"] == "OUTBOX_DELIVERY_FAILED"
    assert failed["result"] == {
        "draft_id": DRAFT_ID,
        "channel": "github",
        "target": GITHUB_TARGET,
    }
    assert FICTIONAL_TOKEN not in result.stdout
    assert FICTIONAL_TOKEN not in result.stderr
    assert get_draft(root, DRAFT_ID).delivery_state == "unsent"
    assert verify_ledger(root).event_count == 1


def test_human_yes_flow_renders_preview_and_confirms_before_send(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _saved_context(tmp_path / "human")
    captured: list[httpx.Request] = []

    def handler(
        _transport: httpx.HTTPTransport,
        request: httpx.Request,
    ) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            201,
            json={"id": int(GITHUB_COMMENT_ID), "html_url": GITHUB_COMMENT_URL},
            request=request,
        )

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handler)
    result = runner.invoke(app, [*_arguments(root), "--yes"], input="y\n")

    assert result.exit_code == 0, result.output
    assert f"Draft: {DRAFT_ID}" in result.stdout
    assert f"Recipient: {GITHUB_TARGET} (GitHub issue or pull request)" in result.stdout
    assert "Send fingerprint:" in result.stdout
    assert "sha256:" in result.stdout
    assert draft_payload().body in result.stdout
    assert "Send this exact body to this exact GitHub target?" in result.stdout
    assert GITHUB_COMMENT_URL in result.stdout
    assert len(captured) == 1
    assert get_draft(root, DRAFT_ID).delivery_state == "sent"
