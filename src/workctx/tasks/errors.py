"""Expected, content-safe failures raised by task operations."""

from __future__ import annotations

from workctx.errors import ConflictError, UserCorrectableError, WorkctxError


class TaskOperationError(WorkctxError):
    """Base class for deterministic task-operation failures."""


class TaskNotFoundError(UserCorrectableError, TaskOperationError):
    """Raised when a requested task is not present in the active context."""

    def __init__(self) -> None:
        super().__init__("The requested task was not found in the active context.")


class TaskEvidenceRequiredError(UserCorrectableError, TaskOperationError):
    """Raised when a material task mutation has no supporting observation."""

    def __init__(self) -> None:
        super().__init__("Task mutations require at least one supporting observation.")


class TaskStateError(ConflictError, TaskOperationError):
    """Raised when current task or claim state cannot be changed safely."""

    def __init__(
        self,
        message: str = "Task state is inconsistent with the requested change.",
    ) -> None:
        super().__init__(message)


class ClaimSequenceExhaustedError(ConflictError, TaskOperationError):
    """Raised when the yearly claim identifier range is exhausted."""

    def __init__(self, year: int) -> None:
        self.year = year
        super().__init__(f"The claim identifier sequence for {year} is exhausted.")


__all__ = [
    "ClaimSequenceExhaustedError",
    "TaskEvidenceRequiredError",
    "TaskNotFoundError",
    "TaskOperationError",
    "TaskStateError",
]
