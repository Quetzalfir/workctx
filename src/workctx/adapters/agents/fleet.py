"""Failure-isolated fleet orchestration over registered contexts."""

from __future__ import annotations

import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from workctx.adapters.filesystem.registry import ContextRegistry, RegisteredContext
from workctx.errors import InvalidContextError
from workctx.services.contexts import load_context_config

from .models import (
    AdapterPlan,
    AgentClient,
    ClientAvailability,
    FileOperation,
    ManagedFileMerge,
    OperationResult,
    PlannedChange,
    TargetApproval,
)

if TYPE_CHECKING:
    from .service import AgentAdapterService

type ApprovalFactory = Callable[[AdapterPlan], tuple[TargetApproval, ...]]


class FleetApplicationState(StrEnum):
    """Application state exposed for one context or client plan."""

    PREVIEW = "preview"
    APPLIED = "applied"
    SKIPPED = "skipped"
    FAILED = "failed"


class FleetSkipCode(StrEnum):
    """Stable reasons why a registered context was not planned."""

    CONTEXT_ROOT_MISSING = "context_root_missing"
    CONTEXT_ID_MISMATCH = "context_id_mismatch"
    NO_AVAILABLE_CLIENTS = "no_available_clients"


class FleetFailureStage(StrEnum):
    """Stage at which one isolated context or client failed."""

    CONTEXT_VALIDATION = "context_validation"
    DETECT = "detect"
    PLAN = "plan"
    APPLY = "apply"


@dataclass(frozen=True, slots=True)
class FleetSkip:
    code: FleetSkipCode
    message: str


@dataclass(frozen=True, slots=True)
class FleetFailure:
    stage: FleetFailureStage
    error: Exception = field(repr=False)
    client: AgentClient | None = None


@dataclass(frozen=True, slots=True)
class FleetClientResult:
    client: AgentClient
    plan: AdapterPlan | None = None
    receipt: OperationResult | None = None
    failure: FleetFailure | None = None

    @property
    def application_state(self) -> FleetApplicationState:
        if self.failure is not None:
            return FleetApplicationState.FAILED
        if self.receipt is not None:
            return FleetApplicationState.APPLIED
        return FleetApplicationState.PREVIEW


@dataclass(frozen=True, slots=True)
class FleetPreservedEdit:
    client: AgentClient
    change: PlannedChange


@dataclass(frozen=True, slots=True)
class FleetMergeCandidate:
    client: AgentClient
    candidate: ManagedFileMerge


@dataclass(frozen=True, slots=True)
class FleetContextResult:
    context_id: str
    root: Path
    configured_context_id: str | None = None
    client_results: tuple[FleetClientResult, ...] = ()
    skipped_clients: tuple[AgentClient, ...] = ()
    skip: FleetSkip | None = None
    failure: FleetFailure | None = None

    @property
    def failures(self) -> tuple[FleetFailure, ...]:
        client_failures = tuple(
            result.failure for result in self.client_results if result.failure is not None
        )
        if self.failure is None:
            return client_failures
        return (self.failure, *client_failures)

    @property
    def application_state(self) -> FleetApplicationState:
        if self.skip is not None:
            return FleetApplicationState.SKIPPED
        if self.failures:
            return FleetApplicationState.FAILED
        if any(result.receipt is not None for result in self.client_results):
            return FleetApplicationState.APPLIED
        return FleetApplicationState.PREVIEW

    @property
    def clients(self) -> tuple[AgentClient, ...]:
        return tuple(result.client for result in self.client_results)

    @property
    def refreshed_clients(self) -> tuple[AgentClient, ...]:
        return tuple(
            result.client
            for result in self.client_results
            if result.receipt is not None and result.failure is None
        )

    @property
    def preserved_edits(self) -> tuple[FleetPreservedEdit, ...]:
        return tuple(
            FleetPreservedEdit(result.client, change)
            for result in self.client_results
            if result.plan is not None
            for change in result.plan.changes
            if change.operation is FileOperation.PRESERVE
        )

    @property
    def merge_candidates(self) -> tuple[FleetMergeCandidate, ...]:
        return tuple(
            FleetMergeCandidate(result.client, candidate)
            for result in self.client_results
            if result.plan is not None
            for candidate in result.plan.merge_candidates
        )


@dataclass(frozen=True, slots=True)
class FleetRefreshResult:
    apply_requested: bool
    selected_clients: tuple[AgentClient, ...]
    contexts: tuple[FleetContextResult, ...]

    @property
    def has_failures(self) -> bool:
        return any(context.failures for context in self.contexts)

    @property
    def failure_count(self) -> int:
        return sum(len(context.failures) for context in self.contexts)


def refresh_registered_contexts(
    *,
    service: AgentAdapterService,
    clients: tuple[AgentClient, ...],
    apply: bool,
    approvals_for: ApprovalFactory,
    registry: ContextRegistry | None = None,
) -> FleetRefreshResult:
    """Plan or apply installs across every registration without cross-context aborts."""

    selected_registry = ContextRegistry() if registry is None else registry
    outcomes = tuple(
        _refresh_registered_context(
            registered,
            service=service,
            clients=clients,
            apply=apply,
            approvals_for=approvals_for,
        )
        for registered in selected_registry.list()
    )
    return FleetRefreshResult(
        apply_requested=apply,
        selected_clients=clients,
        contexts=outcomes,
    )


def _refresh_registered_context(
    registered: RegisteredContext,
    *,
    service: AgentAdapterService,
    clients: tuple[AgentClient, ...],
    apply: bool,
    approvals_for: ApprovalFactory,
) -> FleetContextResult:
    configured_id, skip, failure = _inspect_registration(registered)
    if skip is not None or failure is not None:
        return FleetContextResult(
            context_id=registered.context_id,
            root=registered.root,
            configured_context_id=configured_id,
            skip=skip,
            failure=failure,
        )

    try:
        availability = {item.client: item.availability for item in service.detect(registered.root)}
    except Exception as error:
        return FleetContextResult(
            context_id=registered.context_id,
            root=registered.root,
            configured_context_id=configured_id,
            failure=FleetFailure(FleetFailureStage.DETECT, error),
        )

    available_clients = tuple(
        client for client in clients if availability.get(client) is ClientAvailability.AVAILABLE
    )
    skipped_clients = tuple(client for client in clients if client not in available_clients)
    if not available_clients:
        return FleetContextResult(
            context_id=registered.context_id,
            root=registered.root,
            configured_context_id=configured_id,
            skipped_clients=skipped_clients,
            skip=FleetSkip(
                FleetSkipCode.NO_AVAILABLE_CLIENTS,
                "No selected agent client is available on this machine; skipped.",
            ),
        )

    client_results = tuple(
        _refresh_client(
            registered.root,
            client,
            service=service,
            apply=apply,
            approvals_for=approvals_for,
        )
        for client in available_clients
    )
    return FleetContextResult(
        context_id=registered.context_id,
        root=registered.root,
        configured_context_id=configured_id,
        client_results=client_results,
        skipped_clients=skipped_clients,
    )


def _refresh_client(
    root: Path,
    client: AgentClient,
    *,
    service: AgentAdapterService,
    apply: bool,
    approvals_for: ApprovalFactory,
) -> FleetClientResult:
    try:
        plan = service.plan_install(root, client)
    except Exception as error:
        return FleetClientResult(
            client=client,
            failure=FleetFailure(FleetFailureStage.PLAN, error, client),
        )

    if not apply:
        return FleetClientResult(client=client, plan=plan)

    try:
        receipt = service.install(plan, approvals=approvals_for(plan))
    except Exception as error:
        return FleetClientResult(
            client=client,
            plan=plan,
            failure=FleetFailure(FleetFailureStage.APPLY, error, client),
        )
    return FleetClientResult(client=client, plan=plan, receipt=receipt)


def _inspect_registration(
    registered: RegisteredContext,
) -> tuple[str | None, FleetSkip | None, FleetFailure | None]:
    try:
        root_metadata = registered.root.lstat()
    except FileNotFoundError:
        return None, _missing_root_skip(registered), None
    except OSError as error:
        return None, None, FleetFailure(FleetFailureStage.CONTEXT_VALIDATION, error)
    if not stat.S_ISDIR(root_metadata.st_mode):
        return None, _missing_root_skip(registered), None

    config_path = registered.root / "context.yaml"
    try:
        config_metadata = config_path.lstat()
    except FileNotFoundError:
        return None, _missing_root_skip(registered), None
    except OSError as error:
        return None, None, FleetFailure(FleetFailureStage.CONTEXT_VALIDATION, error)
    if not stat.S_ISREG(config_metadata.st_mode):
        invalid_config = InvalidContextError("Registered context.yaml is not a regular file.")
        return None, None, FleetFailure(FleetFailureStage.CONTEXT_VALIDATION, invalid_config)

    try:
        configured_id = load_context_config(registered.root).id
    except Exception as error:
        return None, None, FleetFailure(FleetFailureStage.CONTEXT_VALIDATION, error)
    if configured_id != registered.context_id:
        return (
            configured_id,
            FleetSkip(
                FleetSkipCode.CONTEXT_ID_MISMATCH,
                (
                    f"Registered ID '{registered.context_id}' does not match "
                    f"context.yaml ID '{configured_id}'; skipped."
                ),
            ),
            None,
        )
    return configured_id, None, None


def _missing_root_skip(registered: RegisteredContext) -> FleetSkip:
    return FleetSkip(
        FleetSkipCode.CONTEXT_ROOT_MISSING,
        (
            f"Registered context '{registered.context_id}' has no available root or "
            "regular context.yaml; skipped."
        ),
    )
