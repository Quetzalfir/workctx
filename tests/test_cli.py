import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from workctx.cli import app
from workctx.doctor import DoctorCheck
from workctx.services.contexts import initialize_context

ROOT = Path(__file__).parents[1]
ENVELOPE_VALIDATOR = Draft202012Validator(
    json.loads((ROOT / "schemas" / "cli-envelope.schema.json").read_text(encoding="utf-8"))
)
runner = CliRunner()


def _json_result(result: Any, *, exit_code: int, stderr: bool) -> dict[str, Any]:
    assert result.exit_code == exit_code, result.stderr
    payload: dict[str, Any] = json.loads(result.stdout)
    ENVELOPE_VALIDATOR.validate(payload)
    assert (result.stderr != "") is stderr
    return payload


def test_version_command_stays_plain_text() -> None:
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip()
    assert result.stderr == ""


def test_doctor_json_uses_object_result_and_clean_stdout(monkeypatch) -> None:
    monkeypatch.setattr(
        "workctx.cli.run_doctor",
        lambda: [DoctorCheck("python", "ok", "3.13.0", True)],
    )

    result = runner.invoke(app, ["doctor", "--json"])

    payload = _json_result(result, exit_code=0, stderr=False)
    assert payload["ok"] is True
    assert payload["command"] == "doctor"
    assert payload["context_id"] is None
    assert payload["result"]["checks"][0]["name"] == "python"
    assert isinstance(payload["result"], dict)
    assert payload["meta"]["schema_version"] == 1
    assert payload["meta"]["duration_ms"] >= 0


def test_doctor_required_failure_uses_exit_5_and_stderr(monkeypatch) -> None:
    monkeypatch.setattr(
        "workctx.cli.run_doctor",
        lambda: [DoctorCheck("git", "error", "not found on PATH", True)],
    )

    result = runner.invoke(app, ["doctor", "--json"])

    payload = _json_result(result, exit_code=5, stderr=True)
    assert payload["ok"] is False
    assert payload["result"]["checks"][0]["name"] == "git"
    assert payload["errors"][0]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert "required doctor checks failed" in result.stderr


def test_doctor_failure_sanitizes_secret_looking_check_detail(monkeypatch) -> None:
    secret = "api_key=abcdefghijklmnopqrstuv"
    monkeypatch.setattr(
        "workctx.cli.run_doctor",
        lambda: [DoctorCheck("git", "error", secret, True)],
    )

    result = runner.invoke(app, ["doctor", "--json"])

    payload = _json_result(result, exit_code=5, stderr=True)
    assert payload["result"]["checks"][0]["detail"] == "api_key=[REDACTED]"
    assert secret not in result.stdout
    assert secret not in result.stderr


def test_unexpected_json_failure_uses_exit_10_without_leaking_secret(monkeypatch) -> None:
    secret = "api_key=abcdefghijklmnopqrstuv"

    def fail_doctor() -> list[DoctorCheck]:
        raise RuntimeError(f"boom {secret}\nsecond line")

    monkeypatch.setattr("workctx.cli.run_doctor", fail_doctor)

    result = runner.invoke(app, ["doctor", "--json"])

    payload = _json_result(result, exit_code=10, stderr=True)
    assert payload["errors"] == [
        {
            "code": "INTERNAL_ERROR",
            "message": "Unexpected internal failure.",
            "path": None,
            "repair_action": None,
        }
    ]
    assert secret not in result.stdout
    assert secret not in result.stderr
    assert "second line" not in result.stdout
    assert "second line" not in result.stderr


def test_context_init_json_returns_resolved_target(tmp_path: Path) -> None:
    target = tmp_path / "json-context"

    result = runner.invoke(
        app,
        [
            "context",
            "init",
            str(target),
            "--name",
            "JSON Context",
            "--id",
            "json-context",
            "--json",
        ],
    )

    payload = _json_result(result, exit_code=0, stderr=False)
    assert payload["command"] == "context.init"
    assert payload["context_id"] == "json-context"
    assert payload["result"]["root"] == str(target.resolve())
    assert payload["result"]["context"]["id"] == "json-context"


def test_context_init_human_output_prints_resolved_target(tmp_path: Path) -> None:
    target = tmp_path / "human-context"

    result = runner.invoke(
        app,
        ["context", "init", str(target), "--name", "Human Context"],
    )

    assert result.exit_code == 0
    assert str(target.resolve()) in result.stdout
    assert result.stderr == ""


def test_context_init_non_empty_directory_is_user_correctable(tmp_path: Path) -> None:
    target = tmp_path / "not-empty"
    target.mkdir()
    (target / "keep.txt").write_text("keep", encoding="utf-8")

    result = runner.invoke(
        app,
        ["context", "init", str(target), "--name", "Existing", "--json"],
    )

    payload = _json_result(result, exit_code=1, stderr=True)
    assert payload["command"] == "context.init"
    assert payload["result"] == {}
    assert payload["errors"][0]["code"] == "CONTEXT_ALREADY_EXISTS"
    assert "not an empty directory" in result.stderr


def test_context_init_validation_error_does_not_leak_input(tmp_path: Path) -> None:
    secret = "aws_secret_access_key-ABCDEFGHIJKLMNOPQRSTUV"

    result = runner.invoke(
        app,
        [
            "context",
            "init",
            str(tmp_path / "bad-options"),
            "--name",
            "Bad Options",
            "--user-language",
            secret,
            "--json",
        ],
    )

    payload = _json_result(result, exit_code=1, stderr=True)
    assert payload["errors"][0]["message"] == "Context options are invalid."
    assert secret not in result.stdout
    assert secret not in result.stderr


def test_context_inspect_discovers_nearest_ancestor(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "discovered"
    initialize_context(root, name="Discovered", context_id="discovered")
    nested = root / "02_knowledge" / "systems"
    nested.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(nested)

    result = runner.invoke(app, ["context", "inspect", "--json"])

    payload = _json_result(result, exit_code=0, stderr=False)
    assert payload["context_id"] == "discovered"
    assert payload["result"]["root"] == str(root.resolve())


def test_context_option_overrides_positional_path_and_ancestor(tmp_path: Path, monkeypatch) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    initialize_context(first, name="First", context_id="first")
    initialize_context(second, name="Second", context_id="second")
    nested = first / "03_work" / "tasks"
    nested.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(nested)

    result = runner.invoke(
        app,
        ["context", "inspect", str(first), "--context", str(second), "--json"],
    )

    payload = _json_result(result, exit_code=0, stderr=False)
    assert payload["context_id"] == "second"
    assert payload["result"]["root"] == str(second.resolve())


def test_invalid_explicit_context_does_not_fall_back_to_ancestor(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "valid"
    initialize_context(root, name="Valid", context_id="valid")
    nested = root / "03_work"
    monkeypatch.chdir(nested)

    result = runner.invoke(
        app,
        ["context", "inspect", "--context", str(tmp_path / "missing"), "--json"],
    )

    payload = _json_result(result, exit_code=1, stderr=True)
    assert payload["context_id"] is None
    assert payload["errors"][0]["code"] == "CONTEXT_NOT_FOUND"
    assert "Pass --context PATH" in payload["errors"][0]["message"]


def test_missing_context_has_clear_step_4_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["context", "inspect", "--json"])

    payload = _json_result(result, exit_code=1, stderr=True)
    assert payload["command"] == "context.inspect"
    assert payload["result"] == {}
    assert "directory containing context.yaml" in payload["errors"][0]["message"]
    assert "directory containing context.yaml" in result.stderr


def test_context_inspect_invalid_yaml_is_sanitized(tmp_path: Path) -> None:
    root = tmp_path / "invalid"
    root.mkdir()
    secret = "api_key=abcdefghijklmnopqrstuv"
    (root / "context.yaml").write_text(f"name: [\n{secret}\n", encoding="utf-8")

    result = runner.invoke(app, ["context", "inspect", str(root), "--json"])

    payload = _json_result(result, exit_code=1, stderr=True)
    assert payload["errors"][0]["code"] == "INVALID_CONTEXT"
    assert secret not in result.stdout
    assert secret not in result.stderr
    assert "\n" not in payload["errors"][0]["message"]


def test_context_validate_json_maps_errors_to_envelope(tmp_path: Path) -> None:
    root = tmp_path / "invalid-structure"
    initialize_context(root, name="Invalid Structure", context_id="invalid-structure")
    (root / "03_work").rename(root / "03_work_missing")

    result = runner.invoke(app, ["context", "validate", str(root), "--json"])

    payload = _json_result(result, exit_code=1, stderr=True)
    assert payload["command"] == "context.validate"
    assert payload["context_id"] == "invalid-structure"
    assert any(error["code"] == "CTX-MISSING-DIRECTORY" for error in payload["errors"])
    assert "Context validation failed" in result.stderr


def test_context_validate_sanitizes_aggregated_parser_errors(tmp_path: Path) -> None:
    root = tmp_path / "invalid-config"
    root.mkdir()
    secret = "ABCDEFGHIJKLMNOPQRSTUV"
    (root / "context.yaml").write_text(
        f"aws_secret_access_key: {secret}: invalid\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["context", "validate", str(root), "--json"])

    payload = _json_result(result, exit_code=1, stderr=True)
    assert payload["errors"]
    assert any(
        error["code"] == "CTX-CONFIG" and error["message"] == "Context configuration is invalid."
        for error in payload["errors"]
    )
    assert secret not in result.stdout
    assert secret not in result.stderr
    assert all("\n" not in error["message"] for error in payload["errors"])


def test_context_validate_json_maps_warnings_to_envelope(tmp_path: Path) -> None:
    root = tmp_path / "warning-context"
    initialize_context(root, name="Warning Context", context_id="warning-context")
    note = root / "02_knowledge" / "systems" / "note.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("/machine/specific/path\n", encoding="utf-8")

    result = runner.invoke(app, ["context", "validate", str(root), "--json"])

    payload = _json_result(result, exit_code=0, stderr=False)
    assert payload["ok"] is True
    assert any(warning["code"] == "CTX-ABSOLUTE-PATH" for warning in payload["warnings"])


def test_validate_alias_uses_canonical_command_identity(tmp_path: Path) -> None:
    root = tmp_path / "alias-context"
    initialize_context(root, name="Alias Context", context_id="alias-context")

    result = runner.invoke(app, ["validate", "--context", str(root), "--json"])

    payload = _json_result(result, exit_code=0, stderr=False)
    assert payload["command"] == "context.validate"
    assert payload["context_id"] == "alias-context"


def test_usage_error_preserves_typer_exit_2_and_stderr(tmp_path: Path) -> None:
    result = runner.invoke(app, ["context", "init", str(tmp_path / "missing-name")])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Missing option" in result.stderr
