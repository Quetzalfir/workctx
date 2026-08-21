"""Doctor must surface app-container redirection of the user-config directory."""

from __future__ import annotations

from pathlib import Path

import pytest

import workctx.doctor as doctor_module
from workctx.doctor import run_doctor


def _user_config_check(checks: list[doctor_module.DoctorCheck]) -> doctor_module.DoctorCheck:
    matches = [check for check in checks if check.name == "user-config-path"]
    assert len(matches) == 1
    return matches[0]


def _fictional_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    literal = tmp_path / "AppData" / "Local" / "workctx"
    literal.mkdir(parents=True)
    monkeypatch.setattr(doctor_module, "user_config_path", lambda *_args, **_kwargs: literal)
    return literal


def test_unredirected_user_config_reports_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    literal = _fictional_config(tmp_path, monkeypatch)
    (literal / "contexts.json").write_text("{}", encoding="utf-8")

    check = _user_config_check(run_doctor())

    assert check.status == "ok"
    assert check.detail == str(literal)
    assert not check.required


def test_container_shadowed_state_file_reports_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    literal = _fictional_config(tmp_path, monkeypatch)
    (literal / "contexts.json").write_text("{}", encoding="utf-8")
    (literal / "secret-names.json").write_text("{}", encoding="utf-8")
    shadow_root = tmp_path / "Packages" / "Fictional.App_abc" / "LocalCache" / "Local"

    def fake_resolve(path: Path) -> Path | None:
        if not path.exists():
            return None
        if path.name == "secret-names.json":
            return shadow_root / "workctx" / path.name
        return path

    monkeypatch.setattr(doctor_module, "_resolve_existing", fake_resolve)

    check = _user_config_check(run_doctor())

    assert check.status == "warning"
    assert "secret-names.json" in check.detail
    assert "contexts.json" not in check.detail.split("(")[1].split(")")[0]
    assert "non-containerized shell" in check.detail
    assert not check.required, "the virtualization warning must never fail doctor"
