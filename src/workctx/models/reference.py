from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlparse

_CONTEXT_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ENTITY_TYPE_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_CONTEXT_PATTERN_HELP = "context IDs use lowercase ASCII letters, digits, and single hyphens"


@dataclass(frozen=True, slots=True)
class WorkctxUri:
    context_id: str
    entity_type: str
    entity_id: str

    def __post_init__(self) -> None:
        if not _CONTEXT_ID_PATTERN.fullmatch(self.context_id):
            raise ValueError(f"Invalid context ID: {_CONTEXT_PATTERN_HELP}")
        if not _ENTITY_TYPE_PATTERN.fullmatch(self.entity_type):
            raise ValueError("Entity types use lowercase ASCII letters, digits, and single hyphens")
        if not self.entity_id or self.entity_id in {".", ".."}:
            raise ValueError("Entity ID is required and cannot be a path traversal segment")

    @classmethod
    def parse(cls, value: str) -> WorkctxUri:
        parsed = urlparse(value)
        if parsed.scheme != "workctx":
            raise ValueError("URI scheme must be 'workctx'")
        if parsed.params or parsed.query or parsed.fragment:
            raise ValueError("Work Context URIs cannot contain params, query, or fragment")

        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 2:
            raise ValueError("Work Context URI must contain entity type and entity ID")
        entity_type, encoded_entity_id = parts
        return cls(
            context_id=parsed.netloc,
            entity_type=entity_type,
            entity_id=unquote(encoded_entity_id),
        )

    def __str__(self) -> str:
        encoded_id = quote(self.entity_id, safe="-._~")
        return f"workctx://{self.context_id}/{self.entity_type}/{encoded_id}"

    def require_context(self, active_context_id: str) -> None:
        if self.context_id != active_context_id:
            raise ValueError(
                f"Reference belongs to context '{self.context_id}', not '{active_context_id}'"
            )
