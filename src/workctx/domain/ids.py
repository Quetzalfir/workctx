from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar, Self, cast

_SLUG = r"[a-z0-9]+(?:-[a-z0-9]+)*"
_DATED_SLUG_SEQUENCE = rf"[0-9]{{8}}-{_SLUG}-[0-9]{{2}}"


@dataclass(frozen=True, slots=True)
class StableId:
    """Immutable, lexically validated Work Context identifier."""

    value: str

    _pattern: ClassVar[re.Pattern[str]] = re.compile(r"(?!)")
    _family: ClassVar[str] = "stable"

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self._pattern.fullmatch(self.value):
            raise ValueError(f"Invalid {self._family} ID: {self.value!r}")

    @classmethod
    def parse(cls, value: str) -> Self:
        return cls(value)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ArtifactId(StableId):
    _pattern: ClassVar[re.Pattern[str]] = re.compile(rf"ART-{_DATED_SLUG_SEQUENCE}")
    _family: ClassVar[str] = "artifact"


@dataclass(frozen=True, slots=True)
class EvidenceId(StableId):
    _pattern: ClassVar[re.Pattern[str]] = re.compile(rf"EVD-{_DATED_SLUG_SEQUENCE}")
    _family: ClassVar[str] = "evidence"


@dataclass(frozen=True, slots=True)
class ObservationId(StableId):
    _pattern: ClassVar[re.Pattern[str]] = re.compile(rf"EVD-{_DATED_SLUG_SEQUENCE}#OBS-[0-9]{{3}}")
    _family: ClassVar[str] = "observation"


@dataclass(frozen=True, slots=True)
class TaskId(StableId):
    _pattern: ClassVar[re.Pattern[str]] = re.compile(r"TASK-[0-9]{4}-[0-9]{3}")
    _family: ClassVar[str] = "task"


@dataclass(frozen=True, slots=True)
class SubtaskId(StableId):
    _pattern: ClassVar[re.Pattern[str]] = re.compile(r"TASK-[0-9]{4}-[0-9]{3}-ST[0-9]{2}")
    _family: ClassVar[str] = "subtask"


@dataclass(frozen=True, slots=True)
class DecisionId(StableId):
    _pattern: ClassVar[re.Pattern[str]] = re.compile(r"DEC-[0-9]{4}-[0-9]{3}")
    _family: ClassVar[str] = "decision"


@dataclass(frozen=True, slots=True)
class RiskId(StableId):
    _pattern: ClassVar[re.Pattern[str]] = re.compile(r"RISK-[0-9]{4}-[0-9]{3}")
    _family: ClassVar[str] = "risk"


@dataclass(frozen=True, slots=True)
class QuestionId(StableId):
    _pattern: ClassVar[re.Pattern[str]] = re.compile(r"Q-[0-9]{4}-[0-9]{3}")
    _family: ClassVar[str] = "question"


@dataclass(frozen=True, slots=True)
class ClaimId(StableId):
    _pattern: ClassVar[re.Pattern[str]] = re.compile(r"CLM-[0-9]{4}-[0-9]{5}")
    _family: ClassVar[str] = "claim"


@dataclass(frozen=True, slots=True)
class PersonId(StableId):
    _pattern: ClassVar[re.Pattern[str]] = re.compile(rf"PER-{_SLUG}")
    _family: ClassVar[str] = "person"


@dataclass(frozen=True, slots=True)
class SystemId(StableId):
    _pattern: ClassVar[re.Pattern[str]] = re.compile(rf"SYS-{_SLUG}")
    _family: ClassVar[str] = "system"


EvidenceNoteId = EvidenceId

type AnyStableId = (
    ArtifactId
    | EvidenceId
    | ObservationId
    | TaskId
    | SubtaskId
    | DecisionId
    | RiskId
    | QuestionId
    | ClaimId
    | PersonId
    | SystemId
)

STABLE_ID_TYPES: tuple[type[StableId], ...] = (
    ArtifactId,
    EvidenceId,
    ObservationId,
    TaskId,
    SubtaskId,
    DecisionId,
    RiskId,
    QuestionId,
    ClaimId,
    PersonId,
    SystemId,
)


def parse_stable_id(value: str) -> AnyStableId:
    """Parse a value into its concrete stable-ID family."""

    for id_type in STABLE_ID_TYPES:
        if id_type._pattern.fullmatch(value):
            return cast(AnyStableId, id_type.parse(value))
    raise ValueError(f"Unsupported Work Context ID: {value!r}")


def format_stable_id(value: StableId) -> str:
    return str(value)
