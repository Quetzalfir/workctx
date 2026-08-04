"""Deterministic native layouts and renderers for supported agent clients."""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass

from ._safe_fs import collision_key, validate_relative_path
from .errors import InvalidAdapterStateError
from .models import AgentClient, McpConfigurationPath

ADAPTER_VERSION = 3

_CLIENT_ROOTS: dict[AgentClient, str] = {
    AgentClient.CODEX: ".agents/skills",
    AgentClient.CLAUDE: ".claude/skills",
    AgentClient.GEMINI: ".gemini/skills",
}

_BRIDGE_PATHS: dict[AgentClient, str] = {
    AgentClient.CODEX: "AGENTS.md",
    AgentClient.CLAUDE: "CLAUDE.md",
    AgentClient.GEMINI: "GEMINI.md",
}

_MCP_CONFIGURATION_PATHS: dict[AgentClient, McpConfigurationPath] = {
    AgentClient.CODEX: ".codex/config.toml",
    AgentClient.CLAUDE: ".mcp.json",
    AgentClient.GEMINI: ".gemini/settings.json",
}

_MCP_SERVER_COMMAND = "workctx"
_MCP_SERVER_ARGS = ("mcp", "serve", "--context", ".")


@dataclass(frozen=True, slots=True)
class RenderedFile:
    """One exact client-local generated file."""

    path: str
    target_hash: str
    content: bytes


@dataclass(frozen=True, slots=True)
class RenderedSkill:
    """Desired native representation of one canonical skill."""

    name: str
    canonical_path: str
    canonical_hash: str
    mode: str
    target_path: str
    target_hash: str
    content: bytes | None
    auxiliary_files: tuple[RenderedFile, ...] = ()

    @property
    def generated_files(self) -> tuple[RenderedFile, ...]:
        if self.content is None:
            return ()
        return (
            RenderedFile(self.target_path, self.target_hash, self.content),
            *self.auxiliary_files,
        )


@dataclass(frozen=True, slots=True)
class RenderedMcpConfiguration:
    """One deterministic project-scoped MCP client configuration."""

    path: McpConfigurationPath
    target_hash: str
    content: bytes


def content_hash(content: bytes) -> str:
    """Return the manifest's exact-byte SHA-256 representation."""

    return "sha256:" + hashlib.sha256(content).hexdigest()


def bridge_path(client: AgentClient) -> str:
    """Return the client-native project instruction filename."""

    return _BRIDGE_PATHS[client]


def mcp_configuration_path(client: AgentClient) -> McpConfigurationPath:
    """Return the client-native project MCP configuration filename."""

    return _MCP_CONFIGURATION_PATHS[client]


def render_mcp_configuration(client: AgentClient) -> RenderedMcpConfiguration:
    """Render the deterministic Work Context stdio server entry for one client."""

    path = mcp_configuration_path(client)
    if client is AgentClient.CODEX:
        content = (
            "[mcp_servers.workctx]\n"
            f'command = "{_MCP_SERVER_COMMAND}"\n'
            'args = ["mcp", "serve", "--context", "."]\n'
        ).encode()
    else:
        content = (
            json.dumps(
                {
                    "mcpServers": {
                        "workctx": {
                            "command": _MCP_SERVER_COMMAND,
                            "args": list(_MCP_SERVER_ARGS),
                        }
                    }
                },
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    return RenderedMcpConfiguration(path, content_hash(content), content)


def mcp_configuration_is_equivalent(client: AgentClient, content: bytes) -> bool:
    """Return whether user-owned config already declares the Work Context server."""

    try:
        text = content.decode("utf-8")
        if client is AgentClient.CODEX:
            payload = tomllib.loads(text)
            servers = payload.get("mcp_servers")
        else:
            payload = json.loads(text)
            servers = payload.get("mcpServers") if isinstance(payload, dict) else None
    except (UnicodeDecodeError, json.JSONDecodeError, tomllib.TOMLDecodeError):
        return False
    if not isinstance(servers, dict):
        return False
    server = servers.get("workctx")
    return (
        isinstance(server, dict)
        and server.get("command") == _MCP_SERVER_COMMAND
        and server.get("args") == list(_MCP_SERVER_ARGS)
    )


def skill_target_path(client: AgentClient, name: str) -> str:
    """Return a portable project-relative native skill path."""

    if not 2 <= len(name) <= 80 or any(
        not part
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789" for character in part)
        for part in name.split("-")
    ):
        raise ValueError(f"Invalid portable skill name: {name!r}")
    return f"{_CLIENT_ROOTS[client]}/{name}/SKILL.md"


def _inject_advisory_comment(
    content: bytes,
    side_effect_class: str,
    resource_hashes: tuple[tuple[str, str], ...],
) -> bytes:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise InvalidAdapterStateError("Canonical skill must be UTF-8") from error
    lines = text.splitlines(keepends=True)
    if len(lines) < 4 or lines[0].rstrip("\r\n") != "---":
        raise InvalidAdapterStateError("Canonical skill must start with YAML frontmatter")
    delimiter_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.rstrip("\r\n") == "---"),
        None,
    )
    if delimiter_index is None:
        raise InvalidAdapterStateError("Canonical skill frontmatter is not closed")
    newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
    advisories = [f"# workctx-side-effect-class: {side_effect_class}{newline}"]
    advisories.extend(
        "# workctx-resource-sha256: "
        f"{resource_hash} "
        f"{json.dumps(relative_path, ensure_ascii=True)}{newline}"
        for relative_path, resource_hash in resource_hashes
    )
    lines[delimiter_index:delimiter_index] = advisories
    return "".join(lines).encode("utf-8")


def _render_skill_primary(
    canonical_content: bytes,
    side_effect_class: str,
    resource_hashes: tuple[tuple[str, str], ...],
) -> bytes:
    """Render a generated SKILL.md from an exact sorted resource commitment."""

    return _inject_advisory_comment(
        canonical_content,
        side_effect_class,
        tuple(sorted(resource_hashes)),
    )


def _parse_rendered_skill_primary(
    content: bytes,
) -> tuple[bytes, str, tuple[tuple[str, str], ...]] | None:
    """Recover and validate the exact producer commitment from generated SKILL.md bytes."""

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None
    lines = text.splitlines(keepends=True)
    if len(lines) < 4 or lines[0].rstrip("\r\n") != "---":
        return None
    delimiter_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.rstrip("\r\n") == "---"),
        None,
    )
    if delimiter_index is None:
        return None
    resource_prefix = "# workctx-resource-sha256: "
    resource_index = delimiter_index - 1
    resources: list[tuple[str, str]] = []
    while resource_index >= 1:
        line = lines[resource_index].rstrip("\r\n")
        if not line.startswith(resource_prefix):
            break
        digest, separator, encoded_path = line.removeprefix(resource_prefix).partition(" ")
        if (
            not separator
            or len(digest) != 71
            or not digest.startswith("sha256:")
            or any(character not in "0123456789abcdef" for character in digest[7:])
        ):
            return None
        try:
            relative_path = json.loads(encoded_path)
        except json.JSONDecodeError:
            return None
        if not isinstance(relative_path, str):
            return None
        try:
            relative_path = validate_relative_path(relative_path)
        except ValueError:
            return None
        resources.append((relative_path, digest))
        resource_index -= 1
    side_effect_prefix = "# workctx-side-effect-class: "
    if resource_index < 1:
        return None
    side_effect_line = lines[resource_index].rstrip("\r\n")
    if not side_effect_line.startswith(side_effect_prefix):
        return None
    side_effect_class = side_effect_line.removeprefix(side_effect_prefix)
    if side_effect_class not in {
        "read_only",
        "local_proposal",
        "local_mutation",
        "external_read",
        "external_write",
    }:
        return None
    resources.reverse()
    if tuple(resources) != tuple(sorted(set(resources))):
        return None
    canonical_lines = [*lines[:resource_index], *lines[delimiter_index:]]
    return "".join(canonical_lines).encode("utf-8"), side_effect_class, tuple(resources)


def render_skill(
    client: AgentClient,
    *,
    name: str,
    canonical_content: bytes,
    side_effect_class: str,
    resource_contents: tuple[tuple[str, bytes], ...] = (),
    force_generated: bool = False,
) -> RenderedSkill:
    """Render or native-verify one canonical skill for the selected client."""

    canonical_path = f".agents/skills/{name}/SKILL.md"
    canonical_digest = content_hash(canonical_content)
    target_path = skill_target_path(client, name)
    if client is AgentClient.CODEX and not force_generated:
        return RenderedSkill(
            name=name,
            canonical_path=canonical_path,
            canonical_hash=canonical_digest,
            mode="native-verified",
            target_path=canonical_path,
            target_hash=canonical_digest,
            content=None,
        )
    if client is AgentClient.CODEX:
        # A per-context override replaces an installer-owned packaged seed at
        # the native Codex path. Keeping it as a generated primary gives
        # removal a precise ownership record without claiming packaged
        # auxiliary resources as adapter-owned files.
        return RenderedSkill(
            name=name,
            canonical_path=canonical_path,
            canonical_hash=canonical_digest,
            mode="generated",
            target_path=target_path,
            target_hash=canonical_digest,
            content=canonical_content,
        )
    rendered = _render_skill_primary(
        canonical_content,
        side_effect_class,
        tuple(
            (relative_path, content_hash(resource_content))
            for relative_path, resource_content in resource_contents
        ),
    )
    auxiliary: list[RenderedFile] = []
    resource_keys = {collision_key("SKILL.md")}
    for relative_path, resource_content in resource_contents:
        validated = validate_relative_path(relative_path)
        if collision_key(validated) == collision_key("SKILL.md"):
            raise InvalidAdapterStateError("Auxiliary resources cannot replace SKILL.md")
        key = collision_key(validated)
        if key in resource_keys:
            raise InvalidAdapterStateError("Auxiliary resource paths collide")
        resource_keys.add(key)
        target = f"{_CLIENT_ROOTS[client]}/{name}/{validated}"
        auxiliary.append(
            RenderedFile(
                path=target,
                target_hash=content_hash(resource_content),
                content=resource_content,
            )
        )
    return RenderedSkill(
        name=name,
        canonical_path=canonical_path,
        canonical_hash=canonical_digest,
        mode="generated",
        target_path=target_path,
        target_hash=content_hash(rendered),
        content=rendered,
        auxiliary_files=tuple(sorted(auxiliary, key=lambda item: item.path)),
    )
