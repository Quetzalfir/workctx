from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import PurePosixPath

PACKAGED_META_SCHEMA_PATHS = ("99_meta/schemas/transaction-proposal.schema.json",)
REFRESH_META_REPAIR_ACTION = "Run `workctx context refresh-meta`."


@dataclass(frozen=True, slots=True)
class PackagedMetaSchema:
    relative_path: str
    content: bytes


def packaged_meta_schemas() -> tuple[PackagedMetaSchema, ...]:
    """Read the reference schemas shipped inside the context-template package."""

    root = files("workctx.resources.context_template")
    packaged: list[PackagedMetaSchema] = []
    for relative_path in PACKAGED_META_SCHEMA_PATHS:
        resource = root
        for part in PurePosixPath(relative_path).parts:
            resource = resource.joinpath(part)
        packaged.append(PackagedMetaSchema(relative_path, resource.read_bytes()))
    return tuple(packaged)


__all__ = [
    "PACKAGED_META_SCHEMA_PATHS",
    "REFRESH_META_REPAIR_ACTION",
    "PackagedMetaSchema",
    "packaged_meta_schemas",
]
