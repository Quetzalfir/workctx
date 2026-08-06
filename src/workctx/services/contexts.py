from __future__ import annotations

import re
import shutil
import unicodedata
import warnings
from contextlib import suppress
from datetime import UTC, datetime
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any

import yaml

from workctx.adapters.filesystem.registry import register_context, register_context_if_changed
from workctx.adapters.filesystem.serialization import dump_yaml_bytes
from workctx.errors import ContextAlreadyExistsError, ContextNotFoundError, InvalidContextError
from workctx.models.context import (
    CURRENT_SCHEMA_VERSION,
    ContextConfig,
    ContextKind,
    ContextLanguages,
    ContextPolicies,
    ContextProfile,
    DataClassification,
    EvidenceRetentionPolicy,
    ExternalWritePolicy,
    LocalMutationPolicy,
)

_CONTEXT_FILE = "context.yaml"
_TEMPLATE_CONTEXT_ID = "example-context"
_TEMPLATE_TIMESTAMP = "2000-01-01T00:00:00Z"


def slugify_context_id(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if len(slug) < 2:
        raise ValueError("Context ID must contain at least two lowercase letters or digits")
    return slug[:64].rstrip("-")


def resolve_context_root(start: Path) -> Path:
    candidate = start.expanduser().resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for current in (candidate, *candidate.parents):
        if (current / _CONTEXT_FILE).is_file():
            return current
    raise ContextNotFoundError(f"No {_CONTEXT_FILE} found from {start}")


def load_context_config(root: Path) -> ContextConfig:
    config_path = root / _CONTEXT_FILE
    if not config_path.is_file():
        raise ContextNotFoundError(f"Missing {config_path}")
    try:
        raw: Any = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise InvalidContextError(f"{config_path} must contain a YAML object")
        return ContextConfig.model_validate(raw)
    except InvalidContextError:
        raise
    except (OSError, yaml.YAMLError, ValueError) as exc:
        raise InvalidContextError(f"Unable to load {config_path}: {exc}") from exc


def register_resolved_context(root: Path) -> None:
    """Best-effort, non-blocking advisory registration at the CLI boundary."""

    try:
        config = load_context_config(root)
        register_context_if_changed(config.id, root, replace=True)
    except Exception:
        return


def initialize_context(
    target: Path,
    *,
    name: str,
    context_id: str | None = None,
    kind: ContextKind = ContextKind.PROJECT,
    profile: ContextProfile = ContextProfile.HYBRID,
    user_language: str = "en",
    timezone: str = "UTC",
) -> ContextConfig:
    target = target.expanduser().resolve()
    if target.exists() and not target.is_dir():
        raise ContextAlreadyExistsError(f"Target path is not a directory: {target}")
    if target.exists() and any(target.iterdir()):
        raise ContextAlreadyExistsError(f"Target directory is not empty: {target}")

    resolved_id = slugify_context_id(context_id or name)
    now = _utc_now()
    config = ContextConfig(
        schema_version=CURRENT_SCHEMA_VERSION,
        id=resolved_id,
        name=name,
        kind=kind,
        profile=profile,
        languages=ContextLanguages(repository="en", user_interaction=user_language),
        timezone=timezone,
        classification=DataClassification.CONFIDENTIAL,
        security_boundary="isolated",
        policies=ContextPolicies(
            local_mutations=LocalMutationPolicy.REVIEW_REQUIRED,
            external_writes=ExternalWritePolicy.APPROVAL_REQUIRED,
            raw_evidence_retention=EvidenceRetentionPolicy.PRESERVE,
            federated_search=False,
        ),
        created_at=now,
        updated_at=now,
    )

    target.mkdir(parents=True, exist_ok=True)
    resource = files("workctx.resources").joinpath("context_template")
    with as_file(resource) as template_path:
        shutil.copytree(template_path, target, dirs_exist_ok=True)

    _parameterize_template_files(
        target,
        context_id=resolved_id,
        timestamp=_format_utc_timestamp(now),
    )
    _write_context_config(target, config)
    _register_initialized_context(config.id, target)
    return config


def _write_context_config(root: Path, config: ContextConfig) -> None:
    (root / _CONTEXT_FILE).write_bytes(dump_yaml_bytes(config))


def _register_initialized_context(context_id: str, root: Path) -> None:
    try:
        register_context(context_id, root, replace=True)
    except Exception:
        with suppress(Exception):
            warnings.warn(
                "Context creation succeeded, but the advisory user registry could not be updated.",
                RuntimeWarning,
                stacklevel=3,
            )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _format_utc_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parameterize_template_files(root: Path, *, context_id: str, timestamp: str) -> None:
    replacements = {
        _TEMPLATE_CONTEXT_ID: context_id,
        _TEMPLATE_TIMESTAMP: timestamp,
    }
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".md", ".yaml", ".yml", ".json"}:
            continue
        text = path.read_text(encoding="utf-8")
        replaced = text
        for placeholder, value in replacements.items():
            replaced = replaced.replace(placeholder, value)
        if replaced != text:
            path.write_text(replaced, encoding="utf-8", newline="\n")
