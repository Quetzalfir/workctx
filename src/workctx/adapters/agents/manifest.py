"""Typed contract and deterministic serialization for agent adapter manifests.

The JSON Schema remains the public structural contract.  These models additionally
enforce the producer invariants that require comparisons between manifest fields.
Legacy manifests may omit ``mode``, ``components``, and ``backups``; new bytes can be
emitted only after :func:`validate_producer_manifest` accepts the complete form.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from ._safe_fs import is_credential_capable_path

type AdapterName = Literal["codex", "claude", "gemini"]
type SkillMode = Literal["generated", "native-verified"]
type BridgeOwnership = Literal["generated", "user-owned"]

ContentHash = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
SkillName = Annotated[
    str,
    StringConstraints(
        min_length=2,
        max_length=80,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    ),
]

_UTC_TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z$"
)
_CANONICAL_PATH_PATTERN = re.compile(r"^\.agents/skills/[a-z0-9]+(?:-[a-z0-9]+)*/SKILL\.md$")
_NATIVE_SOURCE_PATH_PATTERN = re.compile(
    r"^\.agents/skills/[a-z0-9]+(?:-[a-z0-9]+)*/(?!\.\.?(?:/|$))"
    r"(?!.*(?:/\.\.?)(?:/|$))(?!.*//)[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$"
)
_GENERATED_PATH_PATTERN = re.compile(
    r"^\.(?:codex|claude|gemini)/(?!\.\.?(?:/|$))"
    r"(?!.*(?:/\.\.?)(?:/|$))(?!.*//)[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$"
)
_MANAGED_PATH_PATTERN = re.compile(
    r"^(?![A-Za-z]:)(?!/)(?!.*\\)(?!\.\.?(?:/|$))"
    r"(?!.*(?:/\.\.?)(?:/|$))(?!.*//)[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$"
)
_BACKUP_PATH_PATTERN = re.compile(
    r"^\.workctx/backups/([0-9]{8}T[0-9]{6}(?:\.[0-9]+)?Z)/"
    r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$"
)

_BRIDGE_BY_ADAPTER: dict[AdapterName, str] = {
    "codex": "AGENTS.md",
    "claude": "CLAUDE.md",
    "gemini": "GEMINI.md",
}
_NATIVE_SOURCE_SET_DOMAIN = b"workctx-native-source-set-v1\0"


class _StrictManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True)


def _validate_utc_timestamp(value: str) -> str:
    if _UTC_TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise ValueError("timestamp must be RFC 3339 UTC with the Z designator")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("timestamp must be a valid RFC 3339 date-time") from error
    if parsed.utcoffset() != timedelta(0):  # pragma: no cover - guarded by the Z pattern
        raise ValueError("timestamp must be UTC")
    return value


def _compact_timestamp(value: str) -> str:
    return value.replace("-", "").replace(":", "")


def collision_key(path: str) -> str:
    """Return the platform-neutral inventory collision key for a relative path."""

    return unicodedata.normalize("NFC", path).casefold()


def source_set_aggregate_hash(files: Iterable[tuple[str, str]]) -> str:
    """Hash a native source set using its deterministic, domain-separated framing."""

    digest = hashlib.sha256()
    digest.update(_NATIVE_SOURCE_SET_DOMAIN)
    for path, content_digest in sorted(files, key=lambda pair: pair[0]):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content_digest.encode("ascii"))
        digest.update(b"\n")
    return "sha256:" + digest.hexdigest()


class RegistrySource(_StrictManifestModel):
    path: Literal[".agents/skills/registry.yaml"]
    content_hash: ContentHash


class CanonicalSource(_StrictManifestModel):
    path: str
    content_hash: ContentHash

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if _CANONICAL_PATH_PATTERN.fullmatch(value) is None:
            raise ValueError("canonical source path is invalid")
        return value


class NativeSourceFile(_StrictManifestModel):
    path: str
    content_hash: ContentHash

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if _NATIVE_SOURCE_PATH_PATTERN.fullmatch(value) is None:
            raise ValueError("native source path must be safe and canonical-skill-local")
        relative_path = value.split("/", maxsplit=3)[-1]
        if is_credential_capable_path(relative_path):
            raise ValueError("native source path cannot be credential-capable")
        return value


class NativeSourceSet(_StrictManifestModel):
    files: list[NativeSourceFile] = Field(min_length=1)
    aggregate_hash: ContentHash

    @model_validator(mode="after")
    def validate_set(self) -> Self:
        path_keys: set[str] = set()
        pairs: list[tuple[str, str]] = []
        for source in self.files:
            key = collision_key(source.path)
            if key in path_keys:
                raise ValueError(f"native source path collision: {source.path}")
            path_keys.add(key)
            pairs.append((source.path, source.content_hash))
        if self.aggregate_hash != source_set_aggregate_hash(pairs):
            raise ValueError("native source aggregate hash does not match its files")
        return self


class GeneratedFile(_StrictManifestModel):
    path: str
    content_hash: ContentHash

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if _GENERATED_PATH_PATTERN.fullmatch(value) is None:
            raise ValueError("generated path must be safe and client-project-local")
        return value


class SkillAdapterEntry(_StrictManifestModel):
    name: SkillName
    mode: SkillMode | None = None
    canonical: CanonicalSource
    source_set: NativeSourceSet | None = None
    generated: list[GeneratedFile] | None = Field(default=None, min_length=1)

    @property
    def effective_mode(self) -> SkillMode:
        """Return the interpreted mode, including the legacy generated form."""

        return "generated" if self.mode is None else self.mode

    @model_validator(mode="after")
    def validate_mode_shape(self) -> Self:
        for optional_field in ("mode", "source_set", "generated"):
            if optional_field in self.model_fields_set and getattr(self, optional_field) is None:
                raise ValueError(f"{optional_field} cannot be null")

        if self.effective_mode == "generated":
            if self.generated is None:
                raise ValueError("generated entries require nonempty generated")
            if self.source_set is not None:
                raise ValueError("generated entries forbid source_set")
        else:
            if self.generated is not None:
                raise ValueError("native-verified entries forbid generated")
            if self.source_set is None:
                raise ValueError("native-verified entries require source_set")
        return self


class BridgeSource(_StrictManifestModel):
    path: Literal["AGENTS.md", "CLAUDE.md", "GEMINI.md"]
    content_hash: ContentHash


class BridgeTarget(_StrictManifestModel):
    path: Literal["AGENTS.md", "CLAUDE.md", "GEMINI.md"]
    content_hash: ContentHash


class InstructionBridgeComponent(_StrictManifestModel):
    ownership: BridgeOwnership
    source: BridgeSource
    target: BridgeTarget


class McpConfigurationComponent(_StrictManifestModel):
    state: Literal["not_implemented"]


class AdapterComponents(_StrictManifestModel):
    instruction_bridge: InstructionBridgeComponent
    mcp_configuration: McpConfigurationComponent


class BackupEntry(_StrictManifestModel):
    original_path: str
    path: str
    content_hash: ContentHash
    created_at: str

    @field_validator("original_path")
    @classmethod
    def validate_original_path(cls, value: str) -> str:
        if _MANAGED_PATH_PATTERN.fullmatch(value) is None:
            raise ValueError("backup original_path must be a safe project-relative path")
        return value

    @field_validator("path")
    @classmethod
    def validate_backup_path(cls, value: str) -> str:
        if _BACKUP_PATH_PATTERN.fullmatch(value) is None:
            raise ValueError("backup path must be under .workctx/backups/<UTC timestamp>/")
        return value

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: str) -> str:
        return _validate_utc_timestamp(value)

    @model_validator(mode="after")
    def validate_timestamp_directory(self) -> Self:
        match = _BACKUP_PATH_PATTERN.fullmatch(self.path)
        if match is None:  # pragma: no cover - field validator establishes this
            return self
        if match.group(1) != _compact_timestamp(self.created_at):
            raise ValueError("backup timestamp directory must correspond to created_at")
        return self


class AdapterManifest(_StrictManifestModel):
    """One client's skill, bridge, MCP-seam, and durable-backup inventory."""

    schema_version: Literal[1]
    adapter: AdapterName
    adapter_version: int = Field(ge=1)
    scope: Literal["project"]
    generated_at: str
    registry: RegistrySource
    skills: list[SkillAdapterEntry] = Field(min_length=1)
    components: AdapterComponents | None = None
    backups: list[BackupEntry] | None = None

    @field_validator("generated_at")
    @classmethod
    def validate_generated_at(cls, value: str) -> str:
        return _validate_utc_timestamp(value)

    @model_validator(mode="after")
    def validate_manifest_relations(self) -> Self:
        for optional_field in ("components", "backups"):
            if optional_field in self.model_fields_set and getattr(self, optional_field) is None:
                raise ValueError(f"{optional_field} cannot be null")

        names: set[str] = set()
        output_keys: set[str] = set()
        expected_prefix = f".{self.adapter}/"
        for skill in self.skills:
            if skill.name in names:
                raise ValueError(f"duplicate skill name: {skill.name}")
            names.add(skill.name)

            expected_canonical = f".agents/skills/{skill.name}/SKILL.md"
            if skill.canonical.path != expected_canonical:
                raise ValueError("canonical path must be derived from the skill name")

            if skill.effective_mode == "native-verified":
                assert skill.source_set is not None
                native_prefix = f".agents/skills/{skill.name}/"
                if any(
                    not source.path.startswith(native_prefix) for source in skill.source_set.files
                ):
                    raise ValueError("native source paths must belong to the named skill")
                canonical_members = [
                    source
                    for source in skill.source_set.files
                    if source.path == skill.canonical.path
                ]
                if len(canonical_members) != 1 or (
                    canonical_members[0].content_hash != skill.canonical.content_hash
                ):
                    raise ValueError(
                        "native source set must contain the canonical path/hash pair exactly once"
                    )
                continue

            for generated in skill.generated or ():
                if not generated.path.startswith(expected_prefix):
                    raise ValueError("generated path does not belong to the selected adapter")
                key = collision_key(generated.path)
                if key in output_keys:
                    raise ValueError(f"generated path collision: {generated.path}")
                output_keys.add(key)

        if self.components is not None:
            bridge = self.components.instruction_bridge
            expected_bridge = _BRIDGE_BY_ADAPTER[self.adapter]
            if bridge.source.path != expected_bridge or bridge.target.path != expected_bridge:
                raise ValueError("instruction bridge paths do not map to the selected adapter")
            if (
                bridge.ownership == "generated"
                and bridge.source.content_hash != bridge.target.content_hash
            ):
                raise ValueError("a generated bridge target hash must equal its source hash")
            if bridge.ownership == "generated":
                key = collision_key(bridge.target.path)
                if key in output_keys:
                    raise ValueError(f"generated path collision: {bridge.target.path}")
                output_keys.add(key)

        backup_keys: set[str] = set()
        for backup in self.backups or ():
            key = collision_key(backup.path)
            if key in backup_keys:
                raise ValueError(f"backup path collision: {backup.path}")
            backup_keys.add(key)
        return self

    def require_producer_contract(self) -> Self:
        """Reject legacy omissions that readers accept only for migration and status."""

        missing_modes = sorted(skill.name for skill in self.skills if skill.mode is None)
        if missing_modes:
            joined = ", ".join(missing_modes)
            raise ValueError(f"new manifests require explicit skill modes: {joined}")
        if self.components is None:
            raise ValueError("new manifests require components")
        if self.backups is None:
            raise ValueError("new manifests require backups, even when the list is empty")
        return self


def load_manifest(data: str | bytes | bytearray) -> AdapterManifest:
    """Parse a current or backward-compatible legacy manifest."""

    return AdapterManifest.model_validate_json(data)


def validate_producer_manifest(manifest: AdapterManifest | object) -> AdapterManifest:
    """Validate the complete contract required before emitting new manifest bytes."""

    if isinstance(manifest, AdapterManifest):
        # Rebuild from primitives so mutable list contents cannot bypass parent validators.
        manifest = AdapterManifest.model_validate(
            manifest.model_dump(mode="python", exclude_none=True)
        )
    else:
        manifest = AdapterManifest.model_validate(manifest)
    return manifest.require_producer_contract()


def dump_manifest(manifest: AdapterManifest) -> str:
    """Serialize a producer-valid manifest deterministically as canonical UTF-8 JSON text."""

    validated = validate_producer_manifest(manifest)
    payload = validated.model_dump(mode="json", exclude_none=True)
    skills = payload["skills"]
    skills.sort(key=lambda skill: skill["name"])
    for skill in skills:
        source_set = skill.get("source_set")
        if source_set is not None:
            source_set["files"].sort(key=lambda item: item["path"])
        generated = skill.get("generated")
        if generated is not None:
            generated.sort(key=lambda item: item["path"])
    payload["backups"].sort(key=lambda backup: (backup["path"], backup["original_path"]))
    return json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def dump_manifest_bytes(manifest: AdapterManifest) -> bytes:
    """Serialize a producer-valid manifest to deterministic UTF-8 bytes."""

    return dump_manifest(manifest).encode("utf-8")


__all__ = [
    "AdapterComponents",
    "AdapterManifest",
    "AdapterName",
    "BackupEntry",
    "BridgeOwnership",
    "BridgeSource",
    "BridgeTarget",
    "CanonicalSource",
    "GeneratedFile",
    "InstructionBridgeComponent",
    "McpConfigurationComponent",
    "NativeSourceFile",
    "NativeSourceSet",
    "RegistrySource",
    "SkillAdapterEntry",
    "SkillMode",
    "collision_key",
    "dump_manifest",
    "dump_manifest_bytes",
    "load_manifest",
    "source_set_aggregate_hash",
    "validate_producer_manifest",
]
