from __future__ import annotations

import math
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from workctx.domain.references import validate_commit_sha, validate_repo_id, validate_repo_path
from workctx.domain.relations import ContractDateTime


def _validate_json_integer(value: object) -> object:
    if type(value) is int:
        return value
    if type(value) is float and math.isfinite(value) and value.is_integer():
        return int(value)
    raise ValueError("Value must be a JSON integer")


def _validate_json_number(value: object) -> object:
    if not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value):
        return value
    raise ValueError("Value must be a finite JSON number")


type JsonInteger = Annotated[int, BeforeValidator(_validate_json_integer)]
type JsonNumber = Annotated[float, BeforeValidator(_validate_json_number)]

StrictPositiveInt = Annotated[JsonInteger, Field(ge=1)]
StrictNonNegativeInt = Annotated[JsonInteger, Field(ge=0)]
NormalizedCoordinate = Annotated[
    JsonNumber,
    Field(ge=0, le=1, allow_inf_nan=False),
]
NormalizedExtent = Annotated[
    JsonNumber,
    Field(gt=0, le=1, allow_inf_nan=False),
]


class _LocatorModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LineRangeLocator(_LocatorModel):
    type: Literal["line_range"]
    start_line: StrictPositiveInt
    end_line: StrictPositiveInt

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        return self


class PageRangeLocator(_LocatorModel):
    type: Literal["page_range"]
    start_page: StrictPositiveInt
    end_page: StrictPositiveInt

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.end_page < self.start_page:
            raise ValueError("end_page must be greater than or equal to start_page")
        return self


class TimeRangeLocator(_LocatorModel):
    type: Literal["time_range"]
    start_ms: StrictNonNegativeInt
    end_ms: StrictNonNegativeInt
    speaker: str | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator("speaker", mode="before")
    @classmethod
    def reject_explicit_null_speaker(cls, value: object) -> object:
        if value is None:
            raise ValueError("speaker must be omitted rather than null")
        return value

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms must be greater than or equal to start_ms")
        return self


class MessageLocator(_LocatorModel):
    type: Literal["message"]
    channel_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    thread_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    message_id: str
    timestamp: ContractDateTime | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator("channel_id", "thread_id", "timestamp", mode="before")
    @classmethod
    def reject_explicit_null_optional_fields(cls, value: object) -> object:
        if value is None:
            raise ValueError("Optional message fields must be omitted rather than null")
        return value


class ImageRegionLocator(_LocatorModel):
    type: Literal["image_region"]
    page_or_frame: StrictNonNegativeInt | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    x: NormalizedCoordinate
    y: NormalizedCoordinate
    width: NormalizedExtent
    height: NormalizedExtent

    @field_validator("page_or_frame", mode="before")
    @classmethod
    def reject_explicit_null_page_or_frame(cls, value: object) -> object:
        if value is None:
            raise ValueError("page_or_frame must be omitted rather than null")
        return value


class JsonPointerLocator(_LocatorModel):
    type: Literal["json_pointer"]
    pointer: str

    @field_validator("pointer")
    @classmethod
    def validate_pointer(cls, value: str) -> str:
        has_invalid_escape = any(
            character == "~" and (index + 1 == len(value) or value[index + 1] not in "01")
            for index, character in enumerate(value)
        )
        if (not value or value.startswith("/")) and not has_invalid_escape:
            return value
        raise ValueError("JSON pointer must be empty or start with '/' and use only ~0/~1 escapes")


class TableRangeLocator(_LocatorModel):
    type: Literal["table_range"]
    sheet: str
    range: str


class RepoRangeLocator(_LocatorModel):
    type: Literal["repo_range"]
    repo_id: str
    commit: str
    path: str
    start_line: StrictPositiveInt
    end_line: StrictPositiveInt

    @field_validator("repo_id")
    @classmethod
    def validate_repository_id(cls, value: str) -> str:
        return validate_repo_id(value)

    @field_validator("commit")
    @classmethod
    def validate_commit(cls, value: str) -> str:
        return validate_commit_sha(value)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return validate_repo_path(value)

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        return self


class WholeArtifactLocator(_LocatorModel):
    type: Literal["whole_artifact"]
    justification: str = Field(min_length=1)


type SourceLocator = Annotated[
    LineRangeLocator
    | PageRangeLocator
    | TimeRangeLocator
    | MessageLocator
    | ImageRegionLocator
    | JsonPointerLocator
    | TableRangeLocator
    | RepoRangeLocator
    | WholeArtifactLocator,
    Field(discriminator="type"),
]

SOURCE_LOCATOR_ADAPTER: TypeAdapter[SourceLocator] = TypeAdapter(SourceLocator)


def parse_source_locator(value: object) -> SourceLocator:
    return SOURCE_LOCATOR_ADAPTER.validate_python(value)
