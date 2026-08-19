from __future__ import annotations

import json
import re
from dataclasses import dataclass

from pydantic import ValidationError

_ATTRIBUTE_SEGMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class ModelValidationDetail:
    """Value-free field detail from one Pydantic validation error."""

    path: str
    message: str


def model_validation_details(
    error: ValidationError,
    *,
    prefix: str = "$",
) -> tuple[ModelValidationDetail, ...]:
    """Extract field paths and messages without retaining rejected inputs."""

    details: list[ModelValidationDetail] = []
    for item in error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    ):
        location = item.get("loc", ())
        message = item.get("msg", "Model validation failed.")
        details.append(
            ModelValidationDetail(
                path=_field_path(prefix, location),
                message=(message if isinstance(message, str) else "Model validation failed."),
            )
        )
    return tuple(details)


def _field_path(prefix: str, location: tuple[int | str, ...]) -> str:
    path = prefix
    for segment in location:
        if isinstance(segment, int):
            path = f"{path}[{segment}]"
        elif _ATTRIBUTE_SEGMENT.fullmatch(segment):
            path = f"{path}.{segment}"
        else:
            path = f"{path}[{json.dumps(segment, ensure_ascii=False)}]"
    return path


__all__ = ["ModelValidationDetail", "model_validation_details"]
