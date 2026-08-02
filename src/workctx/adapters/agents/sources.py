"""Validated canonical skill discovery with repository-first packaged fallback."""

from __future__ import annotations

import posixpath
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache, partial
from importlib import resources
from pathlib import Path
from typing import Any, get_args, get_type_hints
from urllib.parse import unquote, urlsplit

import yaml
from typer import Typer
from typer.models import OptionInfo

from ._safe_fs import (
    SafeFilesystemError,
    SafeRoot,
    UnsafePathError,
    collision_key,
    is_credential_capable_path,
    validate_relative_path,
)
from .errors import InvalidAdapterStateError
from .manifest import source_set_aggregate_hash
from .models import AgentClient, SourceOrigin
from .renderers import bridge_path, content_hash

_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SIDE_EFFECT_CLASSES = {
    "read_only",
    "local_proposal",
    "local_mutation",
    "external_read",
    "external_write",
}
_ASSIGNED_SECRET = re.compile(
    r"\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|private[_-]?key|password|passwd|secret|token)"
    r"\s*[:=]\s*(?P<value>'[^'\n]+'|\"[^\"\n]+\"|[^\s#;,]+)",
    re.IGNORECASE,
)
_PLACEHOLDER_VALUE = re.compile(
    r"(?:"
    r"<(?:redacted|placeholder)>|"
    r"\$\{[A-Z][A-Z0-9_]*\}|"
    r"YOUR_[A-Z0-9_]+|"
    r"(?:EXAMPLE|PLACEHOLDER|REPLACE_ME|CHANGEME)(?:[_-][A-Z0-9]+)*|"
    r"REPLACE-WITH-[A-Z0-9_-]+"
    r")",
    re.IGNORECASE,
)
_TOKEN_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(rb"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}\b", re.IGNORECASE),
)
_ABSOLUTE_PATH_PATTERNS = (
    ("Windows drive path", re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")),
    ("UNC path", re.compile(r"(?<!\\)\\\\[A-Za-z0-9.$_-]+[\\/][^\s`\"')]+")),
    (
        "forward-slash network path",
        re.compile(r"(?<![:/])//[A-Za-z0-9.$@_-]+/[^\s`\"')]+"),
    ),
    ("file URI", re.compile(r"\bfile:(?:/+|\\+)", re.IGNORECASE)),
    ("home-relative path", re.compile(r"(?<![A-Za-z0-9])~[\\/]")),
    (
        "single-segment POSIX path",
        re.compile(
            r"(?<![\w._:/-])/(?!/)[^\s/`\"'()<>{}\[\],.;:]+"
            r"(?=$|[\s`\"'()<>{}\[\],.;:])"
        ),
    ),
    (
        "POSIX absolute path",
        re.compile(
            r"(?<![\w._:/-])/(?!/)(?=[^\s`\"'()<>\[\]]*?/)"
            r"[^\s`\"'()<>\[\]]+"
        ),
    ),
)
_ALLOWED_SLASH_OPTIONS = frozenset({"/quiet"})
_HTML_CLOSING_TAG = re.compile(r"</[A-Za-z][A-Za-z0-9-]*\s*>")
_PORTABLE_RESOURCE_PATH = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
_REFERENCE_USAGE = re.compile(r"!?\[(?P<text>[^\]\n]+)\]\[(?P<label>[^\]\n]*)\]")
_REFERENCE_DEFINITION = re.compile(
    r"^[ \t]{0,3}\[(?P<label>[^\]\n]+)\]:[ \t]*"
    r"(?:<(?P<angle>[^>\n]+)>|(?P<plain>[^\s\n]+))",
    re.MULTILINE,
)
_INLINE_LINK = re.compile(
    r"!?\[(?P<text>[^\]\n]+)\]\([ \t]*"
    r"(?:<(?P<angle>[^>\n]*)>|(?P<plain>[^\s)]+))"
    r"(?:[ \t]+(?:\"[^\"\n]*\"|'[^'\n]*'|\([^()\n]*\)))?[ \t]*\)"
)
_WORKCTX_REFERENCE = re.compile(
    r"(?<![\w:/])workctx(?:[ \t]+(?:"
    r"(?!workctx\b)[a-z][a-z0-9-]*|"
    r"--?[a-z0-9][a-z0-9-]*(?:=[^\s`,;]+)?|"
    r"<[^>\n]+>"
    r"))+"
)
_NAMED_MCP_REFERENCES = (
    re.compile(r"\bmcp__[A-Za-z0-9_.-]+\b", re.IGNORECASE),
    re.compile(r"\bMCP[ \t]+tool[ \t]+`[^`\n]+`", re.IGNORECASE),
    re.compile(r"`[^`\n]+`[ \t]+MCP[ \t]+tool\b", re.IGNORECASE),
)


class CanonicalInputMissingError(InvalidAdapterStateError):
    """Base error for a safely verified absent canonical input."""

    def __init__(
        self,
        message: str,
        *,
        path: str,
        skill: str | None = None,
        missing_inputs: tuple[tuple[str, str | None], ...] | None = None,
    ) -> None:
        super().__init__(message)
        self.path = path
        self.skill = skill
        self.missing_inputs = missing_inputs or ((path, skill),)


class CanonicalRegistryMissingError(CanonicalInputMissingError):
    """The local canonical tree exists but its fixed registry is absent."""


class CanonicalRegistryInvalidError(InvalidAdapterStateError):
    """The canonical registry is unsafe or violates its complete contract."""


class CanonicalSkillMissingError(CanonicalInputMissingError):
    """A skill declared by a valid local registry is absent."""


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise ValueError("YAML mapping keys must be hashable scalars") from error
        if duplicate:
            raise ValueError(f"Duplicate YAML key: {key}")
        try:
            mapping[key] = loader.construct_object(value_node, deep=deep)
        except TypeError as error:
            raise ValueError("YAML mapping keys must be hashable scalars") from error
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


@dataclass(frozen=True, slots=True)
class CanonicalResource:
    """One safe auxiliary file owned by a canonical skill directory."""

    relative_path: str
    content: bytes
    content_hash: str


@dataclass(frozen=True, slots=True)
class CanonicalSkill:
    """One validated canonical skill and its registry classification."""

    name: str
    side_effect_class: str
    content: bytes
    content_hash: str
    resources: tuple[CanonicalResource, ...] = ()

    @property
    def path(self) -> str:
        return f".agents/skills/{self.name}/SKILL.md"

    @property
    def source_files(self) -> tuple[tuple[str, str], ...]:
        """Return every canonical skill file as sorted full-path/hash pairs."""

        return tuple(
            sorted(
                (
                    (self.path, self.content_hash),
                    *(
                        (
                            f".agents/skills/{self.name}/{resource.relative_path}",
                            resource.content_hash,
                        )
                        for resource in self.resources
                    ),
                ),
                key=lambda pair: pair[0],
            )
        )

    @property
    def source_set_hash(self) -> str:
        """Return the deterministic aggregate commitment for ``source_files``."""

        return source_set_aggregate_hash(self.source_files)


@dataclass(frozen=True, slots=True)
class CanonicalSourceSet:
    """Complete canonical inventory selected for one installation."""

    origin: SourceOrigin
    registry_content: bytes
    registry_hash: str
    skills: tuple[CanonicalSkill, ...]
    bridge_content: bytes
    bridge_hash: str
    bridge_path: str

    @property
    def fingerprint(self) -> str:
        joined = "\n".join(
            [
                self.registry_hash,
                *(
                    value
                    for skill in self.skills
                    for value in (
                        skill.name,
                        skill.content_hash,
                        *(
                            f"{resource.relative_path}\0{resource.content_hash}"
                            for resource in skill.resources
                        ),
                    )
                ),
                self.bridge_hash,
            ]
        )
        return content_hash(joined.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class _CommandSpec:
    path: tuple[str, ...]
    options: frozenset[str]
    is_group: bool


@dataclass(frozen=True, slots=True)
class _ProductReference:
    kind: str
    value: str
    start: int
    end: int


def _yaml_mapping(content: bytes, description: str) -> dict[str, Any]:
    try:
        value = yaml.load(content.decode("utf-8"), Loader=_UniqueKeyLoader)
    except (UnicodeDecodeError, yaml.YAMLError, ValueError) as error:
        raise InvalidAdapterStateError(f"{description} is not valid strict UTF-8 YAML") from error
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise InvalidAdapterStateError(f"{description} must be a string-keyed YAML mapping")
    return value


def _registry_entries(content: bytes) -> tuple[tuple[str, str], ...]:
    try:
        registry = _yaml_mapping(content, "Skill registry")
    except InvalidAdapterStateError as error:
        raise CanonicalRegistryInvalidError(str(error)) from error
    if (
        set(registry) != {"schema_version", "skills"}
        or type(registry["schema_version"]) is not int
        or registry["schema_version"] != 1
    ):
        raise CanonicalRegistryInvalidError("Skill registry has an unsupported object shape")
    raw_entries = registry["skills"]
    if not isinstance(raw_entries, list) or not raw_entries:
        raise CanonicalRegistryInvalidError("Skill registry must contain a nonempty skills list")
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in raw_entries:
        if not isinstance(raw, dict) or not all(isinstance(key, str) for key in raw):
            raise CanonicalRegistryInvalidError("Skill registry entries must be mappings")
        if not {"id", "side_effect_class"} <= set(raw) or set(raw) - {
            "id",
            "side_effect_class",
            "notes",
        }:
            raise CanonicalRegistryInvalidError(
                "Skill registry entry has an unsupported object shape"
            )
        name = raw["id"]
        side_effect = raw["side_effect_class"]
        notes = raw.get("notes")
        if (
            not isinstance(name, str)
            or _SKILL_NAME.fullmatch(name) is None
            or not 2 <= len(name) <= 80
        ):
            raise CanonicalRegistryInvalidError("Skill registry contains an invalid skill ID")
        if name in seen:
            raise CanonicalRegistryInvalidError(f"Skill registry contains duplicate ID: {name}")
        if not isinstance(side_effect, str) or side_effect not in _SIDE_EFFECT_CLASSES:
            raise CanonicalRegistryInvalidError(f"Skill {name} has an invalid side-effect class")
        if notes is not None and (not isinstance(notes, str) or not 1 <= len(notes) <= 500):
            raise CanonicalRegistryInvalidError(f"Skill {name} has invalid registry notes")
        if side_effect == "external_write" and (
            not isinstance(notes, str)
            or not re.match(
                r"^(?:requires? explicit approval|explicit approval (?:is )?required)\b",
                notes,
                re.IGNORECASE,
            )
        ):
            raise CanonicalRegistryInvalidError(
                f"External-write skill {name} lacks an explicit approval boundary"
            )
        seen.add(name)
        entries.append((name, side_effect))
    return tuple(sorted(entries))


def _callback_options(callback: Callable[..., object]) -> frozenset[str]:
    options = {"--help"}
    hints = get_type_hints(callback, include_extras=True)
    for parameter_name, annotation in hints.items():
        if parameter_name == "return":
            continue
        for metadata in get_args(annotation)[1:]:
            if not isinstance(metadata, OptionInfo):
                continue
            declarations = list(metadata.param_decls or ())
            if (
                not declarations
                and isinstance(metadata.default, str)
                and metadata.default.startswith("-")
            ):
                declarations.append(metadata.default)
            if not declarations:
                declarations.append("--" + parameter_name.replace("_", "-"))
            options.update(str(declaration) for declaration in declarations)
    return frozenset(options)


@cache
def _workctx_command_specs() -> dict[tuple[str, ...], _CommandSpec]:
    # Import lazily so using the adapter library does not initialize the CLI unless a
    # canonical skill actually contains a product command reference.
    from workctx.cli import app

    specs: dict[tuple[str, ...], _CommandSpec] = {}

    def visit(command_app: Typer, prefix: tuple[str, ...]) -> None:
        specs[prefix] = _CommandSpec(path=prefix, options=frozenset({"--help"}), is_group=True)
        for command in command_app.registered_commands:
            name = command.name
            if name is None and command.callback is not None:
                name = command.callback.__name__.replace("_", "-")
            if name is None or command.callback is None:
                continue
            path = (*prefix, name)
            specs[path] = _CommandSpec(
                path=path,
                options=_callback_options(command.callback),
                is_group=False,
            )
        for group in command_app.registered_groups:
            if group.name is not None and group.typer_instance is not None:
                visit(group.typer_instance, (*prefix, group.name))

    visit(app, ())
    return specs


def _is_implemented_command(reference: str) -> bool:
    try:
        tokens = shlex.split(reference)
    except ValueError:
        return False
    if not tokens or tokens[0] != "workctx":
        return False

    command_tokens = tokens[1:]
    candidates = [
        spec
        for path, spec in _workctx_command_specs().items()
        if command_tokens[: len(path)] == list(path)
    ]
    if not candidates:
        return False
    spec = max(candidates, key=lambda candidate: len(candidate.path))
    remaining = command_tokens[len(spec.path) :]
    if spec.is_group and any(not token.startswith("-") for token in remaining):
        return False
    for token in remaining:
        if token.startswith("-"):
            option = token.split("=", maxsplit=1)[0]
            if option not in spec.options:
                return False
    return True


def _line_product_references(line: str) -> tuple[_ProductReference, ...]:
    candidates = [
        _ProductReference("command", match.group(0), match.start(), match.end())
        for match in _WORKCTX_REFERENCE.finditer(line)
    ]
    for pattern in _NAMED_MCP_REFERENCES:
        candidates.extend(
            _ProductReference("mcp", match.group(0), match.start(), match.end())
            for match in pattern.finditer(line)
        )
    references: list[_ProductReference] = []
    for candidate in sorted(
        candidates,
        key=lambda reference: (reference.start, -(reference.end - reference.start)),
    ):
        if references and candidate.start < references[-1].end:
            continue
        references.append(candidate)
    return tuple(references)


def _product_reference_issues(text: str) -> tuple[str, ...]:
    issues: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        references = _line_product_references(line)
        for index, reference in enumerate(references):
            next_start = references[index + 1].start if index + 1 < len(references) else len(line)
            marker_scope = line[reference.end : next_start]
            if "(planned)" in marker_scope.casefold():
                continue
            if reference.kind == "command" and not _is_implemented_command(reference.value):
                issues.append(
                    f"unimplemented product command on line {line_number}: {reference.value}"
                )
            elif reference.kind == "mcp":
                issues.append(f"unimplemented MCP tool on line {line_number}: {reference.value}")
    return tuple(issues)


def _is_escaped(text: str, index: int) -> bool:
    backslashes = 0
    index -= 1
    while index >= 0 and text[index] == "\\":
        backslashes += 1
        index -= 1
    return backslashes % 2 == 1


def _mask_inline_code(text: str) -> str:
    characters = list(text)
    index = 0
    while index < len(text):
        if text[index] != "`" or _is_escaped(text, index):
            index += 1
            continue
        run_end = index
        while run_end < len(text) and text[run_end] == "`":
            run_end += 1
        delimiter = text[index:run_end]
        closing = text.find(delimiter, run_end)
        if closing == -1:
            index = run_end
            continue
        for masked_index in range(index, closing + len(delimiter)):
            characters[masked_index] = " "
        index = closing + len(delimiter)
    return "".join(characters)


def _mask_markdown_code(text: str) -> str:
    """Mask fenced and inline code while preserving character offsets and newlines."""

    masked_lines: list[str] = []
    fence_character: str | None = None
    fence_length = 0
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip(" \t")
        run_character = stripped[:1]
        run_length = 0
        if run_character in {"`", "~"}:
            while run_length < len(stripped) and stripped[run_length] == run_character:
                run_length += 1
        if fence_character is not None:
            masked_lines.append(
                "".join("\n" if char == "\n" else "\r" if char == "\r" else " " for char in line)
            )
            if run_character == fence_character and run_length >= fence_length:
                fence_character = None
                fence_length = 0
            continue
        if run_character in {"`", "~"} and run_length >= 3:
            fence_character = run_character
            fence_length = run_length
            masked_lines.append(
                "".join("\n" if char == "\n" else "\r" if char == "\r" else " " for char in line)
            )
            continue
        masked_lines.append(_mask_inline_code(line))
    return "".join(masked_lines)


def _internal_link_issues(text: str, link_exists: Callable[[str], bool]) -> tuple[str, ...]:
    issues: list[str] = []
    masked = _mask_markdown_code(text)
    destinations: list[str] = []

    reference_labels: set[str] = set()
    for match in _REFERENCE_DEFINITION.finditer(masked):
        if _is_escaped(masked, match.start()):
            continue
        label = re.sub(r"\s+", " ", match.group("label").strip()).casefold()
        reference_labels.add(label)
        destinations.append(match.group("angle") or match.group("plain"))

    for match in _INLINE_LINK.finditer(masked):
        bracket_index = match.start() + int(masked[match.start()] == "!")
        if not _is_escaped(masked, bracket_index):
            destinations.append(match.group("angle") or match.group("plain"))

    for match in _REFERENCE_USAGE.finditer(masked):
        bracket_index = match.start() + int(masked[match.start()] == "!")
        if _is_escaped(masked, bracket_index):
            continue
        raw_label = match.group("label") or match.group("text")
        label = re.sub(r"\s+", " ", raw_label.strip()).casefold()
        if label not in reference_labels:
            issues.append(f"undefined reference link: {raw_label}")

    for destination in destinations:
        try:
            parsed = urlsplit(destination)
        except ValueError:
            issues.append(f"broken or unsafe internal link: {destination}")
            continue
        if parsed.scheme.casefold() == "file":
            issues.append(f"machine-specific file link: {destination}")
            continue
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue
        relative_path = unquote(parsed.path)
        if not link_exists(relative_path):
            issues.append(f"broken or unsafe internal link: {destination}")
    return tuple(issues)


def _validate_skill(
    name: str,
    content: bytes,
    *,
    link_exists: Callable[[str], bool],
) -> None:
    if b"\x00" in content:
        raise InvalidAdapterStateError(f"Canonical skill {name} contains a NUL byte")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise InvalidAdapterStateError(f"Canonical skill {name} must be UTF-8") from error
    assigned_secret = any(
        len(value) >= 12 and _PLACEHOLDER_VALUE.fullmatch(value) is None
        for match in _ASSIGNED_SECRET.finditer(text)
        if (value := match.group("value").strip("'\""))
    )
    if assigned_secret or any(pattern.search(content) for pattern in _TOKEN_PATTERNS):
        raise InvalidAdapterStateError(f"Canonical skill {name} contains secret-like material")
    path_scan_text = _HTML_CLOSING_TAG.sub(lambda match: " " * len(match.group(0)), text)
    absolute_path = any(
        label != "single-segment POSIX path"
        or match.group(0).casefold() not in _ALLOWED_SLASH_OPTIONS
        for label, pattern in _ABSOLUTE_PATH_PATTERNS
        for match in pattern.finditer(path_scan_text)
    )
    if absolute_path:
        raise InvalidAdapterStateError(
            f"Canonical skill {name} contains a machine-specific absolute path"
        )
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise InvalidAdapterStateError(f"Canonical skill {name} lacks frontmatter")
    closing = next(
        (index for index, line in enumerate(lines[1:], 1) if line.rstrip("\r\n") == "---"),
        None,
    )
    if closing is None:
        raise InvalidAdapterStateError(f"Canonical skill {name} has unterminated frontmatter")
    metadata = _yaml_mapping("".join(lines[1:closing]).encode("utf-8"), f"Skill {name} frontmatter")
    if set(metadata) != {"name", "description"}:
        raise InvalidAdapterStateError(f"Canonical skill {name} frontmatter has unsupported fields")
    description = metadata["description"]
    if (
        metadata["name"] != name
        or not isinstance(description, str)
        or not 20 <= len(description) <= 600
    ):
        raise InvalidAdapterStateError(f"Canonical skill {name} frontmatter is invalid")
    link_issues = _internal_link_issues(text, link_exists)
    if link_issues:
        raise InvalidAdapterStateError(f"Canonical skill {name} has {link_issues[0]}")
    product_issues = _product_reference_issues(text)
    if product_issues:
        raise InvalidAdapterStateError(f"Canonical skill {name} has {product_issues[0]}")


def _packaged_file(*parts: str) -> bytes:
    traversable = resources.files("workctx.resources.agent_kit")
    for part in parts:
        traversable = traversable.joinpath(part)
    try:
        return traversable.read_bytes()
    except (FileNotFoundError, OSError) as error:
        raise InvalidAdapterStateError(
            f"Packaged agent kit file is missing: {'/'.join(parts)}"
        ) from error


def _resolve_internal_link(source_path: str, destination: str) -> str | None:
    if not destination or "\\" in destination or "\x00" in destination:
        return None
    if destination.startswith("/"):
        return None
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source_path), destination))
    if resolved == ".." or resolved.startswith("../") or resolved.startswith("/"):
        return None
    return resolved


def _local_link_exists(
    safe: SafeRoot,
    source_path: str,
    destination: str,
) -> bool:
    resolved = _resolve_internal_link(source_path, destination)
    if resolved is None:
        return False
    skill_prefix = posixpath.dirname(source_path) + "/"
    if not resolved.startswith(skill_prefix) or resolved == source_path:
        return False
    try:
        entry = safe.inspect_entry(resolved)
    except (FileNotFoundError, SafeFilesystemError):
        return False
    return entry is not None and entry.is_file


def _packaged_link_exists(source_path: str, destination: str) -> bool:
    resolved = _resolve_internal_link(source_path, destination)
    if resolved is None:
        return False
    skill_prefix = posixpath.dirname(source_path) + "/"
    if not resolved.startswith(skill_prefix) or resolved == source_path:
        return False
    target = resources.files("workctx.resources.agent_kit")
    if resolved != ".":
        for part in resolved.split("/"):
            target = target.joinpath(part)
    try:
        return target.is_file()
    except OSError:
        return False


def _validate_resource_path(skill: str, relative_path: str) -> str:
    try:
        validated = validate_relative_path(relative_path)
    except ValueError as error:
        raise InvalidAdapterStateError(
            f"Canonical skill {skill} contains an unsafe resource path"
        ) from error
    if _PORTABLE_RESOURCE_PATH.fullmatch(validated) is None:
        raise InvalidAdapterStateError(
            f"Canonical skill {skill} contains a non-portable resource path"
        )
    if is_credential_capable_path(validated):
        raise InvalidAdapterStateError(
            f"Canonical skill {skill} contains a credential-capable resource path"
        )
    return validated


def _validate_resource_content(skill: str, relative_path: str, content: bytes) -> None:
    if any(pattern.search(content) for pattern in _TOKEN_PATTERNS):
        raise InvalidAdapterStateError(
            f"Canonical skill {skill} resource {relative_path} contains secret-like material"
        )
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return
    assigned_secret = any(
        len(value) >= 12 and _PLACEHOLDER_VALUE.fullmatch(value) is None
        for match in _ASSIGNED_SECRET.finditer(text)
        if (value := match.group("value").strip("'\""))
    )
    if assigned_secret:
        raise InvalidAdapterStateError(
            f"Canonical skill {skill} resource {relative_path} contains secret-like material"
        )


def _sorted_resource_inventory(
    skill: str,
    resources_found: list[CanonicalResource],
) -> tuple[CanonicalResource, ...]:
    """Reject cross-platform path collisions and return canonical ordering."""

    seen = {collision_key("SKILL.md")}
    ordered = sorted(resources_found, key=lambda resource: resource.relative_path)
    for resource in ordered:
        key = collision_key(resource.relative_path)
        if key in seen:
            raise InvalidAdapterStateError(
                f"Canonical skill {skill} contains colliding source paths"
            )
        seen.add(key)
    return tuple(ordered)


def _local_skill_resources(safe: SafeRoot, name: str) -> tuple[CanonicalResource, ...]:
    skill_root = f".agents/skills/{name}"
    pending = [skill_root]
    resources_found: list[CanonicalResource] = []
    while pending:
        directory = pending.pop()
        try:
            entries = safe.list_directory(directory)
        except (FileNotFoundError, UnsafePathError) as error:
            raise InvalidAdapterStateError(
                f"Local canonical skill resource tree is unsafe: {name}"
            ) from error
        for entry in entries:
            if entry.is_directory:
                pending.append(entry.path)
                continue
            relative_path = entry.path.removeprefix(skill_root + "/")
            if relative_path == "SKILL.md":
                continue
            validated = _validate_resource_path(name, relative_path)
            snapshot = safe.inspect_file(entry.path)
            if snapshot.content is None:
                raise InvalidAdapterStateError(
                    f"Canonical skill {name} resource content is unavailable: {validated}"
                )
            _validate_resource_content(name, validated, snapshot.content)
            resources_found.append(
                CanonicalResource(validated, snapshot.content, content_hash(snapshot.content))
            )
    return _sorted_resource_inventory(name, resources_found)


def _packaged_skill_resources(name: str) -> tuple[CanonicalResource, ...]:
    root = resources.files("workctx.resources.agent_kit").joinpath("skills", name)
    pending: list[tuple[Any, str]] = [(root, "")]
    resources_found: list[CanonicalResource] = []
    while pending:
        directory, prefix = pending.pop()
        try:
            children = sorted(directory.iterdir(), key=lambda child: child.name)
        except (FileNotFoundError, OSError) as error:
            raise InvalidAdapterStateError(
                f"Packaged skill resource tree is unavailable: {name}"
            ) from error
        for child in children:
            relative_path = f"{prefix}/{child.name}".lstrip("/")
            validated = _validate_resource_path(name, relative_path)
            try:
                if child.is_dir():
                    pending.append((child, validated))
                    continue
                if not child.is_file():
                    raise InvalidAdapterStateError(
                        f"Packaged skill resource is not a regular file: {name}/{validated}"
                    )
                if validated == "SKILL.md":
                    continue
                content = child.read_bytes()
            except OSError as error:
                raise InvalidAdapterStateError(
                    f"Packaged skill resource is unavailable: {name}/{validated}"
                ) from error
            _validate_resource_content(name, validated, content)
            resources_found.append(CanonicalResource(validated, content, content_hash(content)))
    return _sorted_resource_inventory(name, resources_found)


def _local_sources(
    root: Path,
) -> tuple[bytes, dict[str, bytes], dict[str, tuple[CanonicalResource, ...]]] | None:
    safe = SafeRoot(root)
    try:
        safe.require_directory(".agents/skills")
    except FileNotFoundError:
        return None
    except UnsafePathError as error:
        raise CanonicalRegistryInvalidError(
            "Local .agents/skills must be a safe directory"
        ) from error
    try:
        registry_snapshot = safe.inspect_file(".agents/skills/registry.yaml")
    except UnsafePathError as error:
        raise CanonicalRegistryInvalidError("Local skill registry is unsafe") from error
    if not registry_snapshot.exists or registry_snapshot.content is None:
        raise CanonicalRegistryMissingError(
            "Local .agents/skills exists without registry.yaml",
            path=".agents/skills/registry.yaml",
        )
    entries = _registry_entries(registry_snapshot.content)
    declared = {name for name, _side_effect in entries}
    try:
        directory_entries = safe.list_directory(".agents/skills")
    except (FileNotFoundError, UnsafePathError) as error:
        raise CanonicalRegistryInvalidError("Local canonical skill directory is unsafe") from error
    discovered = {entry.name for entry in directory_entries if entry.is_directory}
    unexpected_files = sorted(
        entry.name
        for entry in directory_entries
        if entry.is_file and entry.name not in {"README.md", "registry.yaml"}
    )
    if discovered != declared or unexpected_files:
        raise CanonicalRegistryInvalidError(
            "Local canonical skill tree and registry inventory do not match"
        )
    contents: dict[str, bytes] = {}
    resource_sets: dict[str, tuple[CanonicalResource, ...]] = {}
    missing_inputs: list[tuple[str, str | None]] = []
    for name, _side_effect in entries:
        try:
            snapshot = safe.inspect_file(f".agents/skills/{name}/SKILL.md")
        except UnsafePathError as error:
            raise InvalidAdapterStateError(f"Local canonical skill is unsafe: {name}") from error
        if not snapshot.exists or snapshot.content is None:
            path = f".agents/skills/{name}/SKILL.md"
            missing_inputs.append((path, name))
            continue
        contents[name] = snapshot.content
        resource_sets[name] = _local_skill_resources(safe, name)
    if missing_inputs:
        first_path, first_skill = missing_inputs[0]
        raise CanonicalSkillMissingError(
            "Local canonical skills are missing: "
            + ", ".join(skill or path for path, skill in missing_inputs),
            path=first_path,
            skill=first_skill,
            missing_inputs=tuple(missing_inputs),
        )
    return registry_snapshot.content, contents, resource_sets


def load_canonical_sources(root: Path, client: AgentClient) -> CanonicalSourceSet:
    """Prefer a complete local canonical inventory, otherwise use the packaged kit."""

    physical_root = root.resolve(strict=True)
    local = _local_sources(physical_root)
    if local is None:
        origin = SourceOrigin.PACKAGED
        registry_content = _packaged_file("skills", "registry.yaml")
        entries = _registry_entries(registry_content)
        contents = {name: _packaged_file("skills", name, "SKILL.md") for name, _ in entries}
        resource_sets = {name: _packaged_skill_resources(name) for name, _ in entries}
    else:
        origin = SourceOrigin.LOCAL
        registry_content, contents, resource_sets = local
        entries = _registry_entries(registry_content)
    skills: list[CanonicalSkill] = []
    local_safe = SafeRoot(physical_root) if origin is SourceOrigin.LOCAL else None
    for name, side_effect in entries:
        content = contents[name]
        if origin is SourceOrigin.LOCAL:
            assert local_safe is not None
            source_path = f".agents/skills/{name}/SKILL.md"
            link_exists: Callable[[str], bool] = partial(
                _local_link_exists, local_safe, source_path
            )
        else:
            source_path = f"skills/{name}/SKILL.md"
            link_exists = partial(_packaged_link_exists, source_path)
        _validate_skill(name, content, link_exists=link_exists)
        skills.append(
            CanonicalSkill(
                name=name,
                side_effect_class=side_effect,
                content=content,
                content_hash=content_hash(content),
                resources=resource_sets[name],
            )
        )
    selected_bridge = bridge_path(client)
    bridge_content = _packaged_file("bridges", selected_bridge)
    return CanonicalSourceSet(
        origin=origin,
        registry_content=registry_content,
        registry_hash=content_hash(registry_content),
        skills=tuple(skills),
        bridge_content=bridge_content,
        bridge_hash=content_hash(bridge_content),
        bridge_path=selected_bridge,
    )
