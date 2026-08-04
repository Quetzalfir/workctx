from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from workctx.cli import app
from workctx.secrets import env_var_name, store

from .conftest import MemoryKeyring

ROOT = Path(__file__).parents[2]
ENVELOPE_VALIDATOR = Draft202012Validator(
    json.loads((ROOT / "schemas" / "cli-envelope.schema.json").read_text(encoding="utf-8"))
)
MASKED_VALUE = "fictional-masked-cli-material"
OS_VALUE = "fictional-cli-os-material"
ENV_VALUE = "fictional-cli-env-material"
IMPORT_VALUE = "fictional-import-cli-material"
runner = CliRunner()


def _envelope(result: Any, *, exit_code: int, command: str) -> dict[str, Any]:
    assert result.exit_code == exit_code, result.stderr
    payload: dict[str, Any] = json.loads(result.stdout)
    ENVELOPE_VALIDATOR.validate(payload)
    assert payload["command"] == command
    return payload


def test_secret_set_uses_masked_prompt_and_never_prints_value(
    memory_keyring: MemoryKeyring,
) -> None:
    result = runner.invoke(
        app,
        ["secret", "set", "masked-token", "--json"],
        input=f"{MASKED_VALUE}\n",
    )

    payload = _envelope(result, exit_code=0, command="secret.set")
    assert payload["result"] == {
        "name": "masked-token",
        "stored": True,
        "backend": "os-store",
    }
    assert memory_keyring.reveal_for_test("masked-token") == MASKED_VALUE
    assert MASKED_VALUE not in result.stdout
    assert MASKED_VALUE not in result.stderr
    assert "Secret value" in result.stderr


def test_secret_set_rejects_value_argument_without_echoing_it() -> None:
    argv_value = "fictional-argv-material"

    result = runner.invoke(
        app,
        ["secret", "set", "argv-token", argv_value, "--json"],
    )

    payload = _envelope(result, exit_code=2, command="secret.set")
    assert payload["errors"][0]["code"] == "SECRET_VALUE_ON_ARGV"
    assert argv_value not in result.stdout
    assert argv_value not in result.stderr


def test_secret_set_from_env_does_not_echo_source_value(
    memory_keyring: MemoryKeyring,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FICTIONAL_SOURCE_VALUE", ENV_VALUE)

    result = runner.invoke(
        app,
        [
            "secret",
            "set",
            "source-token",
            "--from-env",
            "FICTIONAL_SOURCE_VALUE",
            "--json",
        ],
    )

    _envelope(result, exit_code=0, command="secret.set")
    assert memory_keyring.reveal_for_test("source-token") == ENV_VALUE
    assert ENV_VALUE not in result.stdout
    assert ENV_VALUE not in result.stderr


def test_check_list_and_unset_emit_value_free_envelopes(
    memory_keyring: MemoryKeyring,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store("os-token", OS_VALUE)
    monkeypatch.setenv(env_var_name("env-token"), ENV_VALUE)

    checked = runner.invoke(app, ["secret", "check", "os-token", "--json"])
    check_payload = _envelope(checked, exit_code=0, command="secret.check")
    assert check_payload["result"]["layer"] == "os-store"

    listed = runner.invoke(app, ["secret", "list", "--json"])
    list_payload = _envelope(listed, exit_code=0, command="secret.list")
    assert list_payload["result"]["count"] == 2
    assert list_payload["result"]["secrets"] == [
        {
            "name": "env-token",
            "environment": True,
            "os_store": False,
            "resolved_layer": "env",
        },
        {
            "name": "os-token",
            "environment": False,
            "os_store": True,
            "resolved_layer": "os-store",
        },
    ]

    unset = runner.invoke(app, ["secret", "unset", "os-token", "--json"])
    unset_payload = _envelope(unset, exit_code=0, command="secret.unset")
    assert unset_payload["result"]["deleted"] is True
    assert not memory_keyring.contains("os-token")

    missing = runner.invoke(app, ["secret", "check", "os-token", "--json"])
    missing_payload = _envelope(missing, exit_code=1, command="secret.check")
    assert missing_payload["errors"][0]["code"] == "SECRET_NOT_FOUND"

    combined_output = "".join(
        result.stdout + result.stderr for result in (checked, listed, unset, missing)
    )
    assert OS_VALUE not in combined_output
    assert ENV_VALUE not in combined_output


def test_secret_list_remains_env_only_with_backend_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from workctx.secrets import _backend

    monkeypatch.setenv(env_var_name("env-token"), ENV_VALUE)

    def missing_keyring(_name: str) -> object:
        raise ImportError

    monkeypatch.setattr(_backend, "import_module", missing_keyring)

    result = runner.invoke(app, ["secret", "list", "--json"])

    payload = _envelope(result, exit_code=0, command="secret.list")
    assert payload["result"]["os_store_available"] is False
    assert payload["result"]["secrets"][0]["resolved_layer"] == "env"
    assert payload["warnings"][0]["code"] == "SECRET_BACKEND_UNAVAILABLE"
    assert ENV_VALUE not in result.stdout
    assert ENV_VALUE not in result.stderr


def test_secret_check_reports_unavailable_backend_after_env_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from workctx.secrets import _backend

    backend_detail = "fictional unavailable backend detail"

    def missing_keyring(_name: str) -> object:
        raise ImportError(backend_detail)

    monkeypatch.setattr(_backend, "import_module", missing_keyring)

    result = runner.invoke(app, ["secret", "check", "missing-token", "--json"])

    payload = _envelope(result, exit_code=5, command="secret.check")
    assert payload["errors"][0]["code"] == "SECRET_BACKEND_UNAVAILABLE"
    assert backend_detail not in result.stdout
    assert backend_detail not in result.stderr


def test_secret_import_keep_parses_all_entries_before_storing(
    tmp_path: Path,
    memory_keyring: MemoryKeyring,
) -> None:
    source = tmp_path / "keep.env"
    second_value = "fictional quoted cli material"
    source.write_text(
        "\n".join(
            (
                "# fictional dotenv",
                f"FIRST_TOKEN={IMPORT_VALUE}",
                f'SECOND_TOKEN="{second_value}" # comment',
                "",
            )
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["secret", "import", str(source), "--keep", "--json"],
    )

    payload = _envelope(result, exit_code=0, command="secret.import")
    assert payload["result"] == {
        "count": 2,
        "names": ["first-token", "second-token"],
        "source_deleted": False,
    }
    assert source.exists()
    assert memory_keyring.reveal_for_test("first-token") == IMPORT_VALUE
    assert memory_keyring.reveal_for_test("second-token") == second_value
    assert IMPORT_VALUE not in result.stdout
    assert second_value not in result.stdout


def test_secret_import_shred_removes_source(tmp_path: Path) -> None:
    source = tmp_path / "shred.env"
    source.write_text(f"SHRED_TOKEN={IMPORT_VALUE}\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["secret", "import", str(source), "--shred", "--json"],
    )

    payload = _envelope(result, exit_code=0, command="secret.import")
    assert payload["result"]["source_deleted"] is True
    assert not source.exists()
    assert IMPORT_VALUE not in result.stdout
    assert IMPORT_VALUE not in result.stderr


def test_secret_import_human_mode_offers_source_deletion(tmp_path: Path) -> None:
    source = tmp_path / "prompt.env"
    source.write_text(f"PROMPT_TOKEN={IMPORT_VALUE}\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["secret", "import", str(source)],
        input="y\n",
    )

    assert result.exit_code == 0, result.stderr
    assert "Securely delete" in result.stdout
    assert not source.exists()
    assert IMPORT_VALUE not in result.stdout
    assert IMPORT_VALUE not in result.stderr


def test_secret_import_json_requires_explicit_disposition() -> None:
    source = Path("unused-dotenv-source.env")

    result = runner.invoke(app, ["secret", "import", str(source), "--json"])

    payload = _envelope(result, exit_code=2, command="secret.import")
    assert payload["errors"][0]["code"] == "SECRET_IMPORT_DISPOSITION_REQUIRED"


def test_malformed_import_refuses_whole_file_and_reports_line_only(
    tmp_path: Path,
    memory_keyring: MemoryKeyring,
) -> None:
    malformed_line = "this line has no assignment"
    source = tmp_path / "malformed.env"
    source.write_text(
        f"FIRST_TOKEN={IMPORT_VALUE}\n{malformed_line}\nTHIRD_TOKEN=unused\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["secret", "import", str(source), "--keep", "--json"],
    )

    payload = _envelope(result, exit_code=1, command="secret.import")
    assert payload["errors"][0]["code"] == "DOTENV_MALFORMED"
    assert payload["errors"][0]["message"].endswith("line 2.")
    assert not any(call[0] == "set" for call in memory_keyring.calls)
    assert malformed_line not in result.stdout
    assert malformed_line not in result.stderr
    assert IMPORT_VALUE not in result.stdout
    assert IMPORT_VALUE not in result.stderr


def test_cli_import_keeps_secrets_package_lazy() -> None:
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("WORKCTX_SECRET_")
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import workctx.cli; "
                "raise SystemExit(int('workctx.secrets' in sys.modules))"
            ),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
