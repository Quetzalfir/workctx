from __future__ import annotations

import json

from workctx.retrieval.models import ContextPack


def serialize_context_pack(pack: ContextPack, *, indent: int | None = None) -> str:
    """Serialize a validated pack with stable object-key ordering."""

    if indent is not None and indent < 0:
        raise ValueError("indent must be non-negative")
    payload = pack.model_dump(mode="json")
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=indent,
        separators=(",", ":") if indent is None else None,
        sort_keys=True,
    )
