"""Bounded, inert discovery of user-owned per-context skill overrides."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from workctx.validation.engine import contains_possible_secret

from ._safe_fs import SafeFilesystemError, SafeRoot, UnsafePathError
from .errors import InvalidAdapterStateError
from .models import (
    SkillOverrideStatus,
    SkillOverrideWarning,
    SkillOverrideWarningCode,
)
from .personalization import PERSONALIZATION_LAYER_MAX_BYTES
from .renderers import content_hash

SKILL_OVERRIDE_ROOT = "06_overrides/skills"
SKILL_OVERRIDE_FILENAME = "SKILL.md"
SKILL_OVERRIDE_MAX_BYTES = PERSONALIZATION_LAYER_MAX_BYTES
SKILL_OVERRIDE_PROVENANCE_START = "<!-- workctx-skill-override:start -->"
SKILL_OVERRIDE_PROVENANCE_END = "<!-- workctx-skill-override:end -->"

_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_CONTENT_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")


class SkillOverrideError(InvalidAdapterStateError):
    """Base class for an override that cannot be observed safely."""

    def __init__(
        self,
        message: str,
        *,
        path: str,
        size_bytes: int | None = None,
    ) -> None:
        super().__init__(message)
        self.path = path
        self.size_bytes = size_bytes


class SkillOverrideLayoutError(SkillOverrideError):
    """Raised when the fixed override tree is unsafe or non-portable."""


class SkillOverrideTooLargeError(SkillOverrideError):
    """Raised before an override larger than the shared layer cap is consumed."""


class SkillOverrideEncodingError(SkillOverrideError):
    """Raised when a discovered override is not UTF-8 Markdown."""


class SkillOverrideProvenanceError(SkillOverrideError):
    """Raised when a known override lacks its exact adoption provenance."""


class SkillOverrideSecretError(SkillOverrideError):
    """Raised with only a portable file locator and line number."""

    def __init__(self, *, path: str, size_bytes: int, line_number: int) -> None:
        super().__init__(
            f"{path}, line {line_number}",
            path=path,
            size_bytes=size_bytes,
        )
        self.line_number = line_number


@dataclass(frozen=True, slots=True)
class SkillOverrideFile:
    """Exact safely read bytes for one fixed override file."""

    skill: str
    path: str
    content: bytes
    content_hash: str

    @property
    def size_bytes(self) -> int:
        return len(self.content)


@dataclass(frozen=True, slots=True)
class ResolvedSkillOverride:
    """One override classified against the live packaged skill catalog."""

    file: SkillOverrideFile
    packaged_at_adoption_hash: str | None
    packaged_now_hash: str | None

    @property
    def known(self) -> bool:
        return self.packaged_now_hash is not None

    def status(self) -> SkillOverrideStatus:
        return SkillOverrideStatus(
            skill=self.file.skill,
            path=self.file.path,
            size_bytes=self.file.size_bytes,
            override_hash=self.file.content_hash,
            known=self.known,
            packaged_at_adoption_hash=self.packaged_at_adoption_hash,
            packaged_now_hash=self.packaged_now_hash,
        )


@dataclass(frozen=True, slots=True)
class SkillOverrides:
    """Deterministically ordered overrides resolved against packaged hashes."""

    overrides: tuple[ResolvedSkillOverride, ...] = ()

    def __post_init__(self) -> None:
        names = tuple(item.file.skill for item in self.overrides)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise ValueError("Skill overrides must be unique and sorted by skill name")

    @property
    def by_name(self) -> dict[str, ResolvedSkillOverride]:
        return {item.file.skill: item for item in self.overrides if item.known}

    @property
    def statuses(self) -> tuple[SkillOverrideStatus, ...]:
        return tuple(item.status() for item in self.overrides)

    @property
    def warnings(self) -> tuple[SkillOverrideWarning, ...]:
        warnings: list[SkillOverrideWarning] = []
        for item in self.overrides:
            status = item.status()
            if not status.known:
                warnings.append(
                    SkillOverrideWarning(
                        code=SkillOverrideWarningCode.UNKNOWN_SKILL,
                        skill=status.skill,
                        path=status.path,
                        override_hash=status.override_hash,
                    )
                )
            elif status.stale:
                warnings.append(
                    SkillOverrideWarning(
                        code=SkillOverrideWarningCode.OLDER_PACKAGED_SKILL,
                        skill=status.skill,
                        path=status.path,
                        override_hash=status.override_hash,
                        packaged_at_adoption_hash=status.packaged_at_adoption_hash,
                        packaged_now_hash=status.packaged_now_hash,
                    )
                )
        return tuple(warnings)


def _override_path(skill: str) -> str:
    return f"{SKILL_OVERRIDE_ROOT}/{skill}/{SKILL_OVERRIDE_FILENAME}"


def _read_override(safe: SafeRoot, skill: str, size_bytes: int) -> SkillOverrideFile:
    path = _override_path(skill)
    if size_bytes > SKILL_OVERRIDE_MAX_BYTES:
        raise SkillOverrideTooLargeError(
            f"{path} exceeds {SKILL_OVERRIDE_MAX_BYTES} bytes",
            path=path,
            size_bytes=size_bytes,
        )
    try:
        snapshot = safe.inspect_file(path)
    except SafeFilesystemError as error:
        raise SkillOverrideLayoutError(
            f"Skill override must be a safe regular file: {path}",
            path=path,
        ) from error
    if not snapshot.exists or snapshot.content is None:
        raise SkillOverrideLayoutError(
            f"Skill override became unavailable: {path}",
            path=path,
        )
    content = snapshot.content
    size = len(content)
    if size > SKILL_OVERRIDE_MAX_BYTES:
        raise SkillOverrideTooLargeError(
            f"{path} exceeds {SKILL_OVERRIDE_MAX_BYTES} bytes",
            path=path,
            size_bytes=size,
        )
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SkillOverrideEncodingError(
            f"{path} must be UTF-8 Markdown",
            path=path,
            size_bytes=size,
        ) from error
    if contains_possible_secret(text):
        line_number = next(
            (
                number
                for number, line in enumerate(text.splitlines(), start=1)
                if contains_possible_secret(line)
            ),
            1,
        )
        raise SkillOverrideSecretError(
            path=path,
            size_bytes=size,
            line_number=line_number,
        )
    return SkillOverrideFile(
        skill=skill,
        path=path,
        content=content,
        content_hash=content_hash(content),
    )


def discover_skill_override_files(
    context_root: Path,
    *,
    include_context: bool = True,
) -> tuple[SkillOverrideFile, ...]:
    """Discover only fixed per-context override files without creating or executing them."""

    if not include_context:
        return ()
    safe = SafeRoot(context_root.resolve(strict=True))
    try:
        safe.require_directory(SKILL_OVERRIDE_ROOT)
    except FileNotFoundError:
        return ()
    except (SafeFilesystemError, UnsafePathError) as error:
        raise SkillOverrideLayoutError(
            f"Skill override directory is unsafe: {SKILL_OVERRIDE_ROOT}",
            path=SKILL_OVERRIDE_ROOT,
        ) from error
    try:
        entries = safe.list_directory(SKILL_OVERRIDE_ROOT)
    except (FileNotFoundError, SafeFilesystemError) as error:
        raise SkillOverrideLayoutError(
            f"Skill override directory is unavailable: {SKILL_OVERRIDE_ROOT}",
            path=SKILL_OVERRIDE_ROOT,
        ) from error

    discovered: list[SkillOverrideFile] = []
    for entry in entries:
        if not entry.is_directory:
            continue
        skill = entry.name
        if _SKILL_NAME.fullmatch(skill) is None or not 2 <= len(skill) <= 80:
            raise SkillOverrideLayoutError(
                f"Skill override directory has an invalid skill name: {entry.path}",
                path=entry.path,
            )
        try:
            children = safe.list_directory(entry.path)
        except (FileNotFoundError, SafeFilesystemError) as error:
            raise SkillOverrideLayoutError(
                f"Skill override directory is unsafe: {entry.path}",
                path=entry.path,
            ) from error
        candidates = [child for child in children if child.name.casefold() == "skill.md"]
        if not candidates:
            continue
        if len(candidates) != 1 or candidates[0].name != SKILL_OVERRIDE_FILENAME:
            raise SkillOverrideLayoutError(
                f"Skill override filename must be exactly {SKILL_OVERRIDE_FILENAME}: {entry.path}",
                path=entry.path,
            )
        candidate = candidates[0]
        if candidate.is_directory:
            raise SkillOverrideLayoutError(
                f"Skill override must be a regular file: {candidate.path}",
                path=candidate.path,
            )
        discovered.append(_read_override(safe, skill, candidate.size))
    return tuple(discovered)


def _provenance_lines(content: bytes, path: str) -> tuple[str, str]:
    text = content.decode("utf-8")
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise SkillOverrideProvenanceError(
            f"Skill override provenance must follow YAML frontmatter: {path}",
            path=path,
            size_bytes=len(content),
        )
    closing = next((index for index, line in enumerate(lines[1:], 1) if line == "---"), None)
    if closing is None:
        raise SkillOverrideProvenanceError(
            f"Skill override provenance must follow closed YAML frontmatter: {path}",
            path=path,
            size_bytes=len(content),
        )
    cursor = closing + 1
    while cursor < len(lines) and not lines[cursor].strip():
        cursor += 1
    expected = lines[cursor : cursor + 4]
    if len(expected) != 4 or expected[0] != SKILL_OVERRIDE_PROVENANCE_START:
        raise SkillOverrideProvenanceError(
            f"Skill override lacks its prefixed provenance header: {path}",
            path=path,
            size_bytes=len(content),
        )
    if expected[3] != SKILL_OVERRIDE_PROVENANCE_END:
        raise SkillOverrideProvenanceError(
            f"Skill override provenance header is malformed: {path}",
            path=path,
            size_bytes=len(content),
        )
    source_label, separator, source_path = expected[1].partition(": ")
    hash_label, hash_separator, adoption_hash = expected[2].partition(": ")
    if (
        source_label != "source"
        or not separator
        or source_path != path
        or hash_label != "packaged-at-adoption"
        or not hash_separator
        or _CONTENT_HASH.fullmatch(adoption_hash) is None
    ):
        raise SkillOverrideProvenanceError(
            f"Skill override provenance header is malformed: {path}",
            path=path,
            size_bytes=len(content),
        )
    if sum(line == SKILL_OVERRIDE_PROVENANCE_START for line in lines) != 1:
        raise SkillOverrideProvenanceError(
            f"Skill override must contain one provenance header: {path}",
            path=path,
            size_bytes=len(content),
        )
    return source_path, adoption_hash


def resolve_skill_overrides(
    files: tuple[SkillOverrideFile, ...],
    packaged_hashes: Mapping[str, str],
) -> SkillOverrides:
    """Classify files and parse provenance only for real packaged skill names."""

    resolved: list[ResolvedSkillOverride] = []
    for file in files:
        packaged_now = packaged_hashes.get(file.skill)
        if packaged_now is None:
            resolved.append(ResolvedSkillOverride(file, None, None))
            continue
        _source_path, adoption_hash = _provenance_lines(file.content, file.path)
        resolved.append(ResolvedSkillOverride(file, adoption_hash, packaged_now))
    return SkillOverrides(tuple(sorted(resolved, key=lambda item: item.file.skill)))


def skill_override_status_message(status: SkillOverrideStatus) -> str:
    """Render one content-free status line through the existing CLI warning seam."""

    if not status.known:
        return str(
            SkillOverrideWarning(
                code=SkillOverrideWarningCode.UNKNOWN_SKILL,
                skill=status.skill,
                path=status.path,
                override_hash=status.override_hash,
            )
        )
    if status.stale:
        return str(
            SkillOverrideWarning(
                code=SkillOverrideWarningCode.OLDER_PACKAGED_SKILL,
                skill=status.skill,
                path=status.path,
                override_hash=status.override_hash,
                packaged_at_adoption_hash=status.packaged_at_adoption_hash,
                packaged_now_hash=status.packaged_now_hash,
            )
        )
    return (
        f"Skill override: skill={status.skill}; path={status.path}; "
        f"packaged-at-adoption={status.packaged_at_adoption_hash}; "
        f"packaged-now={status.packaged_now_hash}; override={status.override_hash}"
    )


__all__ = [
    "SKILL_OVERRIDE_FILENAME",
    "SKILL_OVERRIDE_MAX_BYTES",
    "SKILL_OVERRIDE_PROVENANCE_END",
    "SKILL_OVERRIDE_PROVENANCE_START",
    "SKILL_OVERRIDE_ROOT",
    "ResolvedSkillOverride",
    "SkillOverrideEncodingError",
    "SkillOverrideError",
    "SkillOverrideFile",
    "SkillOverrideLayoutError",
    "SkillOverrideProvenanceError",
    "SkillOverrideSecretError",
    "SkillOverrideTooLargeError",
    "SkillOverrides",
    "discover_skill_override_files",
    "resolve_skill_overrides",
    "skill_override_status_message",
]
