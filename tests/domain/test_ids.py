from collections.abc import Callable

import pytest

from workctx.domain.ids import (
    ArtifactId,
    ClaimId,
    DecisionId,
    EvidenceId,
    ObservationId,
    PersonId,
    QuestionId,
    RiskId,
    StableId,
    SubtaskId,
    SystemId,
    TaskId,
    format_stable_id,
    parse_stable_id,
)

ID_CASES: tuple[tuple[type[StableId], str], ...] = (
    (ArtifactId, "ART-20260730-auth-review-01"),
    (EvidenceId, "EVD-20260730-auth-review-01"),
    (ObservationId, "EVD-20260730-auth-review-01#OBS-004"),
    (TaskId, "TASK-2026-014"),
    (SubtaskId, "TASK-2026-014-ST03"),
    (DecisionId, "DEC-2026-005"),
    (RiskId, "RISK-2026-006"),
    (QuestionId, "Q-2026-021"),
    (ClaimId, "CLM-2026-00421"),
    (PersonId, "PER-alex-rivera"),
    (SystemId, "SYS-customer-portal"),
)


@pytest.mark.parametrize(("id_type", "value"), ID_CASES)
def test_id_families_parse_validate_and_format(id_type: type[StableId], value: str) -> None:
    identifier = id_type.parse(value)

    assert str(identifier) == value
    assert format_stable_id(identifier) == value
    assert parse_stable_id(value) == identifier
    assert type(parse_stable_id(value)) is id_type


@pytest.mark.parametrize(
    ("id_type", "value"),
    (
        (ArtifactId, "ART-2026073-auth-review-01"),
        (ArtifactId, "ART-20260730-auth-review-1"),
        (EvidenceId, "EVD-20260730-Auth-review-01"),
        (EvidenceId, "EVD-20260730-auth--review-01"),
        (ObservationId, "EVD-20260730-auth-review-01#OBS-04"),
        (ObservationId, "EVD-20260730-auth-review-01/../OBS-004"),
        (TaskId, "TASK-26-014"),
        (SubtaskId, "TASK-2026-014-ST3"),
        (DecisionId, "DEC-2026-0005"),
        (RiskId, "RISK-2026-6"),
        (QuestionId, "QUESTION-2026-021"),
        (ClaimId, "CLM-2026-0421"),
        (PersonId, "PER-alex_rivera"),
        (PersonId, "PER-álvaro"),
        (SystemId, "SYS-../portal"),
        (SystemId, "SYS-customer-portal-"),
    ),
)
def test_id_families_reject_malformed_values(
    id_type: Callable[[str], StableId], value: str
) -> None:
    with pytest.raises(ValueError, match="Invalid"):
        id_type(value)


def test_stable_id_dispatch_rejects_unknown_family() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        parse_stable_id("UNKNOWN-2026-001")
