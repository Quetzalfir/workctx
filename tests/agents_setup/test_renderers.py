from __future__ import annotations

import hashlib

import pytest

from workctx.adapters.agents.errors import InvalidAdapterStateError
from workctx.adapters.agents.models import AgentClient
from workctx.adapters.agents.renderers import (
    bridge_path,
    content_hash,
    mcp_configuration_is_equivalent,
    render_mcp_configuration,
    render_skill,
    skill_target_path,
)

_SKILL = (
    b"---\n"
    b"name: trace-context\n"
    b"description: Use when a bounded context question needs traceable evidence.\n"
    b"---\n\n"
    b"# Trace context\n"
)


def test_codex_native_verifies_canonical_skill_without_generating_a_copy() -> None:
    rendered = render_skill(
        AgentClient.CODEX,
        name="trace-context",
        canonical_content=_SKILL,
        side_effect_class="read_only",
    )

    assert rendered.mode == "native-verified"
    assert rendered.canonical_path == ".agents/skills/trace-context/SKILL.md"
    assert rendered.target_path == rendered.canonical_path
    assert rendered.target_hash == rendered.canonical_hash == content_hash(_SKILL)
    assert rendered.content is None


@pytest.mark.parametrize(
    ("client", "target"),
    [
        (AgentClient.CLAUDE, ".claude/skills/trace-context/SKILL.md"),
        (AgentClient.GEMINI, ".gemini/skills/trace-context/SKILL.md"),
    ],
)
def test_generated_native_skill_adds_registry_advisory_without_changing_source(
    client: AgentClient, target: str
) -> None:
    canonical = bytearray(_SKILL)

    rendered = render_skill(
        client,
        name="trace-context",
        canonical_content=bytes(canonical),
        side_effect_class="read_only",
    )

    assert rendered.mode == "generated"
    assert rendered.target_path == target
    assert rendered.canonical_hash == content_hash(_SKILL)
    assert rendered.content == _SKILL.replace(
        b"---\n\n# Trace context",
        b"# workctx-side-effect-class: read_only\n---\n\n# Trace context",
        1,
    )
    assert rendered.target_hash == content_hash(rendered.content)
    assert bytes(canonical) == _SKILL


def test_renderer_preserves_crlf_line_endings() -> None:
    canonical = _SKILL.replace(b"\n", b"\r\n")

    rendered = render_skill(
        AgentClient.GEMINI,
        name="trace-context",
        canonical_content=canonical,
        side_effect_class="read_only",
    )

    assert rendered.content is not None
    assert b"# workctx-side-effect-class: read_only\r\n---\r\n" in rendered.content
    assert b"\n" not in rendered.content.replace(b"\r\n", b"")


@pytest.mark.parametrize(
    ("client", "expected"),
    [
        (AgentClient.CODEX, "AGENTS.md"),
        (AgentClient.CLAUDE, "CLAUDE.md"),
        (AgentClient.GEMINI, "GEMINI.md"),
    ],
)
def test_bridge_paths_are_project_local(client: AgentClient, expected: str) -> None:
    assert bridge_path(client) == expected


@pytest.mark.parametrize(
    ("client", "expected_path", "expected_content"),
    [
        (
            AgentClient.CODEX,
            ".codex/config.toml",
            b"[mcp_servers.workctx]\n"
            b'command = "workctx"\n'
            b'args = ["mcp", "serve", "--context", "."]\n',
        ),
        (
            AgentClient.CLAUDE,
            ".mcp.json",
            b'{\n  "mcpServers": {\n    "workctx": {\n      "command": "workctx",\n'
            b'      "args": [\n        "mcp",\n        "serve",\n        "--context",\n'
            b'        "."\n      ]\n    }\n  }\n}\n',
        ),
        (
            AgentClient.GEMINI,
            ".gemini/settings.json",
            b'{\n  "mcpServers": {\n    "workctx": {\n      "command": "workctx",\n'
            b'      "args": [\n        "mcp",\n        "serve",\n        "--context",\n'
            b'        "."\n      ]\n    }\n  }\n}\n',
        ),
    ],
)
def test_mcp_configuration_renderer_uses_fixed_project_scoped_server_identity(
    client: AgentClient,
    expected_path: str,
    expected_content: bytes,
) -> None:
    rendered = render_mcp_configuration(client)

    assert rendered.path == expected_path
    assert rendered.content == expected_content
    assert rendered.target_hash == content_hash(expected_content)
    assert mcp_configuration_is_equivalent(client, rendered.content)


@pytest.mark.parametrize(
    "name",
    [
        "",
        "a",
        "Trace-Context",
        "trace_context",
        "../trace",
        "-leading",
        "trailing-",
        "double--dash",
    ],
)
def test_target_path_rejects_nonportable_skill_names(name: str) -> None:
    with pytest.raises(ValueError, match="Invalid portable skill name"):
        skill_target_path(AgentClient.CLAUDE, name)


@pytest.mark.parametrize(
    "content",
    [
        b"not frontmatter\n",
        b"---\nname: trace-context\n",
        b"\xff\xfe\n",
    ],
)
def test_generated_renderer_rejects_invalid_canonical_bytes(content: bytes) -> None:
    with pytest.raises(InvalidAdapterStateError):
        render_skill(
            AgentClient.CLAUDE,
            name="trace-context",
            canonical_content=content,
            side_effect_class="read_only",
        )


def test_content_hash_is_exact_byte_sha256() -> None:
    expected = "sha256:" + hashlib.sha256(b"exact bytes\r\n").hexdigest()

    assert content_hash(b"exact bytes\r\n") == expected
