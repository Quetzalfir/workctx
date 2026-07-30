from enum import StrEnum


class EntityType(StrEnum):
    """Canonical entity-type vocabulary fixed by lead decision D-018."""

    EVIDENCE = "evidence"
    PERSON = "person"
    TEAM = "team"
    PROJECT = "project"
    SYSTEM = "system"
    SERVICE = "service"
    MODULE = "module"
    FLOW = "flow"
    INTEGRATION = "integration"
    DECISION = "decision"
    RISK = "risk"
    QUESTION = "question"
    TASK = "task"
    CLAIM = "claim"
    DRAFT = "draft"
    INVESTIGATION = "investigation"
    INCIDENT = "incident"
    OBSERVATION = "observation"
    ARTIFACT = "artifact"
