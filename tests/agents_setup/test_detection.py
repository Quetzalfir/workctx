from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from workctx.adapters.agents.detection import (
    SUPPORTED_VERSION_RANGES,
    ClientDetector,
    _default_version_probe,
    detect_client,
    parse_client_version,
)
from workctx.adapters.agents.models import (
    AgentClient,
    ClientAvailability,
    SemanticVersion,
)


def _finder(executables: dict[str, str]) -> Callable[[str], str | None]:
    return executables.get


def test_detection_reports_clients_independently_with_fake_discovery(tmp_path: Path) -> None:
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    probed: list[tuple[str, Path]] = []

    def probe(executable: str, root: Path) -> str:
        probed.append((executable, root))
        return {
            "fake-codex": "codex-cli 0.42.1",
            "fake-gemini": "gemini version 2.0.0",
        }[executable]

    capabilities = ClientDetector(
        executable_finder=_finder({"codex": "fake-codex", "gemini": "fake-gemini"}),
        version_probe=probe,
    ).detect(tmp_path)

    by_client = {capability.client: capability for capability in capabilities}
    assert by_client[AgentClient.CODEX].availability is ClientAvailability.AVAILABLE
    assert by_client[AgentClient.CODEX].version == SemanticVersion(0, 42, 1)
    assert by_client[AgentClient.CLAUDE].availability is ClientAvailability.CONFIGURED_ONLY
    assert by_client[AgentClient.CLAUDE].project_markers == (".claude/skills",)
    assert by_client[AgentClient.GEMINI].availability is ClientAvailability.UNSUPPORTED
    assert by_client[AgentClient.GEMINI].version == SemanticVersion(2, 0, 0)
    assert probed == [("fake-codex", tmp_path.resolve()), ("fake-gemini", tmp_path.resolve())]


def test_detection_uses_metadata_handles_without_path_or_content_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "AGENTS.md").write_bytes(b"marker contents must not be read")
    (tmp_path / ".agents" / "skills").mkdir(parents=True)

    def forbid_path_access(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("ordinary Path marker access must not be used")

    monkeypatch.setattr(Path, "lstat", forbid_path_access)
    monkeypatch.setattr(Path, "read_bytes", forbid_path_access)

    capability = detect_client(
        tmp_path,
        AgentClient.CODEX,
        executable_finder=lambda _name: None,
    )

    assert capability.availability is ClientAvailability.CONFIGURED_ONLY
    assert capability.project_markers == ("AGENTS.md", ".agents/skills")


def test_detection_rejects_linked_marker_ancestor_before_executable_discovery(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "skills").mkdir()
    linked = tmp_path / ".claude"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("directory link creation is unavailable")
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(linked), str(outside)],
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode != 0:
            pytest.skip("directory link creation is unavailable")
    discovery_called = False

    def finder(_name: str) -> str | None:
        nonlocal discovery_called
        discovery_called = True
        return None

    capability = detect_client(
        tmp_path,
        AgentClient.CLAUDE,
        executable_finder=finder,
    )

    assert capability.availability is ClientAvailability.INVALID
    assert capability.project_markers == ()
    assert capability.detail == "Unsafe project marker path(s): .claude/skills"
    assert not discovery_called


def test_detection_does_not_require_other_clients(tmp_path: Path) -> None:
    capability = detect_client(
        tmp_path,
        AgentClient.CLAUDE,
        executable_finder=_finder({"claude": "fake-claude"}),
        version_probe=lambda _executable, _root: "Claude Code 2.3.4",
    )

    assert capability.availability is ClientAvailability.AVAILABLE
    assert capability.executable == "fake-claude"
    assert capability.project_markers == ()


def test_detection_reports_missing_client_without_running_a_probe(tmp_path: Path) -> None:
    probe_called = False

    def probe(_executable: str, _root: Path) -> str:
        nonlocal probe_called
        probe_called = True
        return "0.1.0"

    capability = detect_client(
        tmp_path,
        AgentClient.GEMINI,
        executable_finder=lambda _name: None,
        version_probe=probe,
    )

    assert capability.availability is ClientAvailability.MISSING
    assert capability.executable is None
    assert capability.version is None
    assert not probe_called


@pytest.mark.parametrize(
    ("failure", "expected_detail"),
    [
        (OSError("cannot execute"), "OSError"),
        (subprocess.TimeoutExpired("fake-client", 5), "TimeoutExpired"),
        (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"), "UnicodeDecodeError"),
    ],
)
def test_version_probe_failure_is_fail_safe(
    tmp_path: Path, failure: BaseException, expected_detail: str
) -> None:
    def fail(_executable: str, _root: Path) -> str:
        raise failure

    capability = detect_client(
        tmp_path,
        AgentClient.CODEX,
        executable_finder=_finder({"codex": "fake-codex"}),
        version_probe=fail,
    )

    assert capability.availability is ClientAvailability.UNSUPPORTED
    assert capability.version is None
    assert capability.detail is not None
    assert expected_detail in capability.detail


def test_unparseable_version_is_unsupported(tmp_path: Path) -> None:
    capability = detect_client(
        tmp_path,
        AgentClient.GEMINI,
        executable_finder=_finder({"gemini": "fake-gemini"}),
        version_probe=lambda _executable, _root: "development build",
    )

    assert capability.availability is ClientAvailability.UNSUPPORTED
    assert capability.version is None
    assert capability.detail == (
        "Client version output did not contain a supported semantic version."
    )


def test_default_version_probe_rejects_nonzero_exit_even_with_version_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def completed(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            ["fake-client", "--version"],
            returncode=2,
            stdout="fake-client 1.2.3",
            stderr="probe failed",
        )

    monkeypatch.setattr(subprocess, "run", completed)

    with pytest.raises(subprocess.CalledProcessError):
        _default_version_probe("fake-client", tmp_path)


def test_supported_ranges_are_explicit_and_upper_bounds_are_exclusive() -> None:
    assert str(SUPPORTED_VERSION_RANGES[AgentClient.CODEX]) == ">=0.1.0,<1.0.0"
    assert str(SUPPORTED_VERSION_RANGES[AgentClient.CLAUDE]) == ">=1.0.0,<3.0.0"
    assert str(SUPPORTED_VERSION_RANGES[AgentClient.GEMINI]) == ">=0.1.0,<1.0.0"
    assert SUPPORTED_VERSION_RANGES[AgentClient.CODEX].contains(SemanticVersion(0, 99, 0))
    assert not SUPPORTED_VERSION_RANGES[AgentClient.CODEX].contains(SemanticVersion(1, 0, 0))


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("codex-cli 0.42.1", SemanticVersion(0, 42, 1)),
        ("Gemini v0.9.0 (preview)", SemanticVersion(0, 9, 0)),
        ("version 12.3", None),
        ("version 1234567890.2.3", None),
        ("no version here", None),
    ],
)
def test_client_version_parser_requires_three_parts(
    output: str, expected: SemanticVersion | None
) -> None:
    assert parse_client_version(output) == expected
