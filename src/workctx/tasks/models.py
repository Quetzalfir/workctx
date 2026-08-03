"""Typed application records returned by task operations."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from workctx.transactions import ApplyResult


class _TaskOperationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaskMutationResult(_TaskOperationRecord):
    """One committed task mutation and the claims created with it."""

    schema_version: Literal[1] = 1
    task_id: str
    claim_ids: tuple[str, ...] = ()
    receipt: ApplyResult


__all__ = ["TaskMutationResult"]
