"""Byte-deterministic serialization for canonical Work Context documents."""

from __future__ import annotations

import json
import math
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import yaml
from pydantic import BaseModel

from workctx.domain.frontmatter import parse_frontmatter

_MISSING = object()


@dataclass(frozen=True, slots=True)
class MarkdownDocument[ModelT: BaseModel]:
    """A validated frontmatter model and its narrative Markdown body."""

    frontmatter: ModelT
    body: str


def canonical_model_data(model: BaseModel) -> dict[str, Any]:
    """Return JSON-compatible data in canonical model and mapping key order.

    Pydantic owns null-versus-omit behavior. In particular, the integrated domain
    models omit absent nested fields that their public JSON Schemas make
    non-nullable, while top-level nullable fields remain present. Free-form
    mappings and extra model fields have no declaration order and are sorted.
    """

    _preflight_model(model)
    serialized = model.model_dump(mode="json")
    normalized = _normalize_serialized(serialized, model)
    if not isinstance(normalized, dict):  # pragma: no cover - BaseModel invariant
        raise TypeError("A model must serialize to an object")
    return normalized


def canonical_mapping_data(mapping: Mapping[str, Any]) -> dict[str, Any]:
    """Return a recursively key-sorted JSON-compatible free-form mapping."""

    _preflight_free_form(mapping)
    normalized = _normalize_serialized(dict(mapping), mapping)
    if not isinstance(normalized, dict):  # pragma: no cover - Mapping invariant
        raise TypeError("A mapping must serialize to an object")
    return normalized


def dump_yaml(document: BaseModel | Mapping[str, Any]) -> str:
    """Serialize a model or free-form mapping using the ADR 0005 YAML format."""

    data = (
        canonical_model_data(document)
        if isinstance(document, BaseModel)
        else canonical_mapping_data(document)
    )
    rendered = yaml.safe_dump(
        data,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        indent=2,
        width=4096,
    )
    return _normalize_newlines(rendered)


def dump_yaml_bytes(document: BaseModel | Mapping[str, Any]) -> bytes:
    """Serialize canonical YAML as UTF-8 without a BOM."""

    return dump_yaml(document).encode("utf-8")


def dump_json(document: BaseModel | Mapping[str, Any]) -> str:
    """Serialize a model or mapping as deterministic, human-readable JSON."""

    data = (
        canonical_model_data(document)
        if isinstance(document, BaseModel)
        else canonical_mapping_data(document)
    )
    return json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def dump_json_bytes(document: BaseModel | Mapping[str, Any]) -> bytes:
    """Serialize deterministic JSON as UTF-8 without a BOM."""

    return dump_json(document).encode("utf-8")


def load_yaml_model[ModelT: BaseModel](data: str | bytes, model_type: type[ModelT]) -> ModelT:
    """Parse a canonical YAML object into a typed domain model."""

    text = _decode(data)
    loaded: Any = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise ValueError("Canonical YAML must contain an object")
    return model_type.model_validate(loaded)


def load_json_model[ModelT: BaseModel](data: str | bytes, model_type: type[ModelT]) -> ModelT:
    """Parse a canonical JSON object into a typed domain model."""

    loaded: Any = json.loads(_decode(data), object_pairs_hook=_reject_duplicate_object_keys)
    if not isinstance(loaded, dict):
        raise ValueError("Canonical JSON must contain an object")
    return model_type.model_validate(loaded)


def render_markdown(frontmatter: BaseModel, body: str = "") -> str:
    """Render canonical YAML frontmatter followed by one structural blank line."""

    normalized_body = _normalize_body(body)
    return f"---\n{dump_yaml(frontmatter)}---\n\n{normalized_body}"


def render_markdown_bytes(frontmatter: BaseModel, body: str = "") -> bytes:
    """Render a canonical Markdown entity as UTF-8 without a BOM."""

    return render_markdown(frontmatter, body).encode("utf-8")


def load_markdown_model[ModelT: BaseModel](
    data: str | bytes,
    model_type: type[ModelT],
) -> MarkdownDocument[ModelT]:
    """Parse Markdown with the shared domain frontmatter parser."""

    loaded, stored_body = parse_frontmatter(_decode(data))
    body = _remove_structural_blank_line(stored_body)
    return MarkdownDocument(frontmatter=model_type.model_validate(loaded), body=body)


def has_hand_edits_yaml[ModelT: BaseModel](data: str | bytes, model_type: type[ModelT]) -> bool:
    """Return whether YAML bytes differ from canonical re-serialization."""

    raw = _as_bytes(data)
    model = load_yaml_model(raw, model_type)
    return raw != dump_yaml_bytes(model)


def has_hand_edits_json[ModelT: BaseModel](data: str | bytes, model_type: type[ModelT]) -> bool:
    """Return whether JSON bytes differ from canonical re-serialization."""

    raw = _as_bytes(data)
    model = load_json_model(raw, model_type)
    return raw != dump_json_bytes(model)


def has_hand_edits_markdown[ModelT: BaseModel](
    data: str | bytes,
    model_type: type[ModelT],
) -> bool:
    """Return whether Markdown bytes differ from canonical re-serialization."""

    raw = _as_bytes(data)
    document = load_markdown_model(raw, model_type)
    expected = render_markdown_bytes(document.frontmatter, document.body)
    return raw != expected


def _normalize_serialized(serialized: Any, source: object = _MISSING) -> Any:
    if isinstance(serialized, Mapping):
        if isinstance(source, BaseModel):
            declared = source.__class__.model_fields
            ordered_keys = [key for key in declared if key in serialized]
            ordered_keys.extend(sorted(key for key in serialized if key not in declared))
            result: dict[str, Any] = {}
            extras = source.__pydantic_extra__ or {}
            for key in ordered_keys:
                source_value = getattr(source, key, extras.get(key, _MISSING))
                result[key] = _normalize_serialized(serialized[key], source_value)
            return result

        if not all(isinstance(key, str) for key in serialized):
            raise TypeError("Canonical mapping keys must be strings")
        keys = sorted(serialized)
        source_mapping = source if isinstance(source, Mapping) else {}
        return {
            key: _normalize_serialized(serialized[key], source_mapping.get(key, _MISSING))
            for key in keys
        }

    if isinstance(serialized, list):
        source_items: Sequence[object]
        if isinstance(source, Sequence) and not isinstance(source, (str, bytes, bytearray)):
            source_items = source
        else:
            source_items = ()
        return [
            _normalize_serialized(
                item,
                source_items[index] if index < len(source_items) else _MISSING,
            )
            for index, item in enumerate(serialized)
        ]

    return serialized


def _preflight_model(model: BaseModel) -> None:
    declared = model.__class__.model_fields
    for field_name in declared:
        _preflight_declared(getattr(model, field_name))
    extras = model.__pydantic_extra__ or {}
    if not all(isinstance(key, str) for key in extras):
        raise TypeError("Canonical mapping keys must be strings")
    for extra_value in extras.values():
        _preflight_free_form(extra_value)


def _preflight_declared(value: object) -> None:
    if isinstance(value, BaseModel):
        _preflight_model(value)
        return
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("Canonical mapping keys must be strings")
        for item in value.values():
            _preflight_declared(item)
        return
    if isinstance(value, (set, frozenset)):
        raise TypeError("Canonical documents cannot contain unordered set values")
    if isinstance(value, float) and not math.isfinite(value):
        raise TypeError("Canonical documents cannot contain non-finite numbers")
    if isinstance(value, (list, tuple)):
        for item in value:
            _preflight_declared(item)
        return
    if isinstance(value, Iterator):
        raise TypeError("Canonical documents cannot contain one-shot iterator values")


def _preflight_free_form(value: object) -> None:
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError("Free-form canonical numbers must be finite")
        return
    if isinstance(value, list):
        for item in value:
            _preflight_free_form(item)
        return
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("Canonical mapping keys must be strings")
        for item in value.values():
            _preflight_free_form(item)
        return
    if isinstance(value, (set, frozenset)):
        raise TypeError("Canonical documents cannot contain unordered set values")
    if isinstance(value, Iterator):
        raise TypeError("Canonical documents cannot contain one-shot iterator values")
    raise TypeError("Free-form canonical values must use JSON-native types")


def _normalize_body(body: str) -> str:
    normalized = _normalize_newlines(body).lstrip("\n")
    if not normalized:
        return ""
    return normalized.rstrip("\n") + "\n"


def _remove_structural_blank_line(body: str) -> str:
    normalized = _normalize_newlines(body)
    if normalized.startswith("\n"):
        normalized = normalized[1:]
    return normalized


def _normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _decode(data: str | bytes) -> str:
    if isinstance(data, str):
        return data
    return data.decode("utf-8")


def _as_bytes(data: str | bytes) -> bytes:
    if isinstance(data, bytes):
        return data
    return data.encode("utf-8")


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Canonical JSON contains a duplicate object key: {key}")
        result[key] = value
    return result
