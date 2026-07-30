from __future__ import annotations

import re
import shutil
import unicodedata
from datetime import UTC, datetime
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any

import yaml

from workctx.errors import ContextAlreadyExistsError, ContextNotFoundError, InvalidContextError
from workctx.models.context import (
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

    target.mkdir(parents=True, exist_ok=True)
    resolved_id = slugify_context_id(context_id or name)
    resource = files("workctx.resources").joinpath("context_template")
    with as_file(resource) as template_path:
        shutil.copytree(template_path, target, dirs_exist_ok=True)

    now = datetime.now(UTC)
    config = ContextConfig(
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
    _write_context_config(target, config)
    _replace_template_context_id(target, resolved_id)
    return config


def _write_context_config(root: Path, config: ContextConfig) -> None:
    payload = config.model_dump(mode="json")
    rendered = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    (root / _CONTEXT_FILE).write_text(rendered, encoding="utf-8", newline="\n")


def _replace_template_context_id(root: Path, context_id: str) -> None:
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".md", ".yaml", ".yml", ".json"}:
            continue
        text = path.read_text(encoding="utf-8")
        replaced = text.replace("example-context", context_id)
        if replaced != text:
            path.write_text(replaced, encoding="utf-8", newline="\n")
