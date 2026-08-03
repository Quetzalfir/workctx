"""Context-bound MCP application service delegating to public engine APIs."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from workctx.adapters.filesystem import CanonicalStore
from workctx.adapters.sqlite import (
    Fts5UnavailableError,
    ProjectionError,
    SQLiteProjection,
    TaskQuery,
)
from workctx.adapters.sqlite.freshness import SqliteFreshnessProbe
from workctx.adapters.sqlite.projection import projection_database_path
from workctx.doctor import run_doctor
from workctx.domain import EntityType, RelationType, TaskStatus, WorkctxUri
from workctx.domain.tasks import TASK_ID_PATTERN
from workctx.domain.transactions import TransactionProposal
from workctx.drafting import DraftPayload, save_draft
from workctx.errors import (
    ConflictError,
    ContextBoundaryError,
    ContextNotFoundError,
    InvalidContextError,
    StaleDerivedStateError,
    UnavailableDependencyError,
    UsageConfigurationError,
    UserCorrectableError,
    WorkctxError,
)
from workctx.ingestion import RegisterRequest, list_inbox, register
from workctx.mcp.contracts import (
    TOOL_CONTRACT_BY_NAME,
    InputContractError,
    ToolContract,
    validate_tool_arguments,
)
from workctx.mcp.models import (
    DiagnosticSeverity,
    ErrorCategory,
    McpDiagnostic,
    McpEnvelope,
    ToolResponse,
)
from workctx.mcp.serialization import safe_diagnostic_text, safe_result_object
from workctx.presentation import ExitCode, exit_code_for
from workctx.retrieval import (
    TraversalDirection,
    build_pack,
    related,
    resolve,
    serialize_context_pack,
    trace,
)
from workctx.transactions import (
    LedgerIntegrityError,
    PostconditionRollbackError,
    PreimageChangedError,
    ProposalValidationError,
    TransactionConflictError,
    apply,
    audit_summary,
    dry_run,
    validate_proposal,
)
from workctx.transactions.models import (
    DiagnosticSeverity as TransactionDiagnosticSeverity,
)
from workctx.transactions.models import (
    ProjectionState,
    TransactionDiagnostic,
)
from workctx.validation import Severity, ValidationIssue, validate_workspace

_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_TASK_ID = re.compile(TASK_ID_PATTERN)

_Handler = Callable[[dict[str, Any]], ToolResponse]


class _BoundaryInputError(ContextBoundaryError):
    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        super().__init__(message)


class McpToolService:
    """Execute the frozen ADR 0012 tool surface inside one context boundary."""

    def __init__(self, context_root: Path) -> None:
        store = CanonicalStore(context_root)
        self._root = store.context_root
        self._context_id = store.context_id
        self._handlers: dict[str, _Handler] = {
            "context_info": self._context_info,
            "workspace_validate": self._workspace_validate,
            "search": self._search,
            "ref_show": self._ref_show,
            "ref_related": self._ref_related,
            "ref_trace": self._ref_trace,
            "context_pack": self._context_pack,
            "task_list": self._task_list,
            "task_show": self._task_show,
            "inbox_list": self._inbox_list,
            "audit_summary": self._audit_summary,
            "artifact_register": self._artifact_register,
            "proposal_validate": self._proposal_validate,
            "transaction_dry_run": self._transaction_dry_run,
            "transaction_apply": self._transaction_apply,
            "index_rebuild": self._index_rebuild,
            "draft_save": self._draft_save,
        }

    @property
    def context_root(self) -> Path:
        return self._root

    @property
    def context_id(self) -> str:
        return self._context_id

    def invoke(self, name: str, arguments: object) -> ToolResponse:
        """Validate, scope, dispatch, and sanitize one tool invocation."""

        contract = TOOL_CONTRACT_BY_NAME.get(name)
        if contract is None:
            return self._failure(
                name,
                self._diagnostic(
                    code="USAGE_CONFIGURATION",
                    category=ErrorCategory.USAGE_CONFIGURATION,
                    message="The requested MCP tool is not available.",
                ),
            )

        try:
            self._verify_bound_context()
            if contract.mutation and not self._has_runtime_approval(arguments):
                return self._failure(
                    name,
                    self._diagnostic(
                        code="APPROVAL_REQUIRED",
                        category=ErrorCategory.CONTEXT_BOUNDARY,
                        message="This MCP mutation requires explicit approved: true.",
                        path="$.approved",
                    ),
                )
            validated = validate_tool_arguments(contract, arguments)
            self._guard_boundaries(validated)
            return self._handlers[name](validated)
        except InputContractError as exc:
            return self._failure(
                name,
                self._diagnostic(
                    code="INVALID_INPUT",
                    category=ErrorCategory.USAGE_CONFIGURATION,
                    message=exc.safe_message,
                    path=exc.path,
                ),
            )
        except Exception as exc:  # one non-leaking boundary owns all engine failures
            return self._exception_response(name, exc)

    @staticmethod
    def contract(name: str) -> ToolContract | None:
        return TOOL_CONTRACT_BY_NAME.get(name)

    def _verify_bound_context(self) -> None:
        current = CanonicalStore(self._root)
        if current.context_root != self._root or current.context_id != self._context_id:
            raise _BoundaryInputError(
                "REF-CONTEXT-MISMATCH",
                "$",
                "The bound context changed while the MCP server was running.",
            )

    @staticmethod
    def _has_runtime_approval(arguments: object) -> bool:
        return isinstance(arguments, dict) and arguments.get("approved") is True

    def _guard_boundaries(self, arguments: Mapping[str, object]) -> None:
        self._guard_value(arguments, "$")

    def _guard_value(self, value: object, path: str) -> None:
        if isinstance(value, str):
            if value.startswith("workctx://"):
                try:
                    uri = WorkctxUri.parse(value)
                except ValueError as exc:
                    raise InputContractError(path, "Work Context URI input is invalid.") from exc
                if uri.context_id != self._context_id:
                    raise _BoundaryInputError(
                        "REF-CONTEXT-MISMATCH",
                        path,
                        "A Work Context URI belongs to another context.",
                    )
            if _looks_like_path_escape(value):
                raise _BoundaryInputError(
                    "CTX-PATH-ESCAPE",
                    path,
                    "Filesystem paths outside the bound context are not accepted.",
                )
            return
        if isinstance(value, Mapping):
            for key, item in value.items():
                self._guard_value(item, f"{path}.{key}")
            return
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for index, item in enumerate(value):
                self._guard_value(item, f"{path}[{index}]")

    def _projection(self) -> SQLiteProjection:
        return SQLiteProjection(self._root)

    def _context_info(self, arguments: dict[str, Any]) -> ToolResponse:
        del arguments
        config = CanonicalStore(self._root).read_context_config()
        checks = run_doctor()
        required_ok = all(check.status == "ok" for check in checks if check.required)
        warnings: tuple[McpDiagnostic, ...] = ()
        if not required_ok:
            warnings = (
                self._diagnostic(
                    code="DEPENDENCY_UNAVAILABLE",
                    category=ErrorCategory.UNAVAILABLE_DEPENDENCY,
                    severity=DiagnosticSeverity.WARNING,
                    message="One or more required local doctor checks failed.",
                ),
            )
        return self._success(
            "context_info",
            {
                "root": str(self._root),
                "context": config.model_dump(mode="json"),
                "doctor": {
                    "required_ok": required_ok,
                    "checks": [
                        {
                            "name": check.name,
                            "status": check.status,
                            "detail": check.detail,
                            "required": check.required,
                        }
                        for check in checks
                    ],
                },
            },
            warnings=warnings,
        )

    def _workspace_validate(self, arguments: dict[str, Any]) -> ToolResponse:
        strict = cast(bool, arguments.get("strict", False))
        probe = SqliteFreshnessProbe() if projection_database_path(self._root).is_file() else None
        report = validate_workspace(self._root, strict=strict, freshness_probe=probe)
        result = {
            "strict": strict,
            "valid": report.ok,
            "issues": [
                {
                    "severity": issue.severity.value,
                    "code": issue.code,
                    "message": _safe_validation_message(issue),
                    "path": issue.path,
                    "repair_action": issue.repair_action,
                }
                for issue in report.issues
            ],
        }
        errors = tuple(
            self._validation_diagnostic(issue)
            for issue in report.issues
            if issue.severity is Severity.ERROR
        )
        warnings = tuple(
            self._validation_diagnostic(issue)
            for issue in report.issues
            if issue.severity is not Severity.ERROR
        )
        if errors:
            return self._failure(
                "workspace_validate",
                *errors,
                result=result,
                warnings=warnings,
            )
        return self._success("workspace_validate", result, warnings=warnings)

    def _search(self, arguments: dict[str, Any]) -> ToolResponse:
        raw_types = cast(list[str] | None, arguments.get("entity_types"))
        entity_types = (
            None
            if raw_types is None
            else frozenset(EntityType(entity_type) for entity_type in raw_types)
        )
        hits = self._projection().search(
            cast(str, arguments["query"]),
            entity_types=entity_types,
            limit=cast(int, arguments.get("limit", 20)),
        )
        return self._success("search", {"hits": hits, "count": len(hits)})

    def _ref_show(self, arguments: dict[str, Any]) -> ToolResponse:
        outcome = resolve(self._projection(), cast(str, arguments["uri"]))
        result = {
            "reference": outcome.reference,
            "found": outcome.found,
            "resolution": outcome,
        }
        if not outcome.found:
            return self._failure(
                "ref_show",
                self._diagnostic(
                    code="REF-NOT-FOUND",
                    category=ErrorCategory.USER_CORRECTABLE,
                    message="Reference did not resolve in the bound context.",
                ),
                result=result,
            )
        return self._success("ref_show", result)

    def _ref_related(self, arguments: dict[str, Any]) -> ToolResponse:
        raw_relations = cast(list[str] | None, arguments.get("relations"))
        relations = (
            None
            if raw_relations is None
            else frozenset(RelationType(relation) for relation in raw_relations)
        )
        outcome = related(
            self._projection(),
            cast(str, arguments["uri"]),
            direction=TraversalDirection(cast(str, arguments.get("direction", "both"))),
            depth=cast(int, arguments.get("depth", 1)),
            relations=relations,
        )
        return self._success("ref_related", {"related": outcome})

    def _ref_trace(self, arguments: dict[str, Any]) -> ToolResponse:
        outcome = trace(
            self._projection(),
            cast(str, arguments["uri"]),
            include_history=cast(bool, arguments.get("include_history", False)),
        )
        return self._success("ref_trace", {"trace": outcome})

    def _context_pack(self, arguments: dict[str, Any]) -> ToolResponse:
        outcome = build_pack(
            self._projection(),
            cast(str, arguments["uri"]),
            budget=cast(int, arguments.get("budget", 12000)),
            query=cast(str | None, arguments.get("query")),
            include_history=cast(bool, arguments.get("include_history", False)),
            include_architecture=cast(bool, arguments.get("include_architecture", False)),
        )
        if not outcome.built or outcome.pack is None:
            return self._failure(
                "context_pack",
                self._diagnostic(
                    code="PACK-NOT-BUILT",
                    category=ErrorCategory.USER_CORRECTABLE,
                    message=outcome.message or "Context pack could not be built.",
                ),
                result={"reference": outcome.reference, "status": outcome.status.value},
            )
        payload = json.loads(serialize_context_pack(outcome.pack))
        if not isinstance(payload, dict):  # pragma: no cover - ContextPack contract
            raise TypeError("Context pack serialization must produce an object")
        return self._success("context_pack", {"pack": payload})

    def _task_list(self, arguments: dict[str, Any]) -> ToolResponse:
        statuses = cast(list[str] | None, arguments.get("statuses"))
        query = TaskQuery(
            statuses=(
                None if statuses is None else frozenset(TaskStatus(status) for status in statuses)
            ),
            owner=cast(str | None, arguments.get("owner")),
            waiting_on=cast(str | None, arguments.get("waiting_on")),
            root_task=cast(str | None, arguments.get("root_task")),
            parent_task=cast(str | None, arguments.get("parent_task")),
        )
        tasks = self._projection().query_tasks(query)
        return self._success("task_list", {"tasks": tasks, "count": len(tasks)})

    def _task_show(self, arguments: dict[str, Any]) -> ToolResponse:
        task = cast(str, arguments["task"])
        if task.startswith("workctx://"):
            uri = WorkctxUri.parse(task)
            if uri.entity_type != "task":
                raise ValueError("task must identify a task entity")
        elif _TASK_ID.fullmatch(task) is None:
            raise ValueError("task must use TASK-YYYY-NNN or TASK-YYYY-NNN-STNN")
        record = self._projection().get_task(task)
        if record is None:
            return self._failure(
                "task_show",
                self._diagnostic(
                    code="REF-NOT-FOUND",
                    category=ErrorCategory.USER_CORRECTABLE,
                    message="Task did not resolve in the bound context.",
                ),
                result={"task": task},
            )
        return self._success("task_show", {"task": record})

    def _inbox_list(self, arguments: dict[str, Any]) -> ToolResponse:
        del arguments
        listing = list_inbox(self._root)
        return self._success(
            "inbox_list",
            {"artifacts": listing.artifacts, "count": listing.count},
        )

    def _audit_summary(self, arguments: dict[str, Any]) -> ToolResponse:
        del arguments
        return self._success("audit_summary", {"audit": audit_summary(self._root)})

    def _artifact_register(self, arguments: dict[str, Any]) -> ToolResponse:
        request = RegisterRequest.model_validate(
            {
                "path": arguments["path"],
                "source_type": arguments["source_type"],
                "source_origin": arguments.get("origin"),
                "event_at": arguments.get("event_date"),
            }
        )
        registration = register(self._root, request)
        return self._success("artifact_register", {"registration": registration})

    def _proposal_validate(self, arguments: dict[str, Any]) -> ToolResponse:
        proposal = TransactionProposal.model_validate(arguments["proposal"])
        result = validate_proposal(self._root, proposal)
        if not result.valid:
            diagnostics = self._transaction_diagnostics(result.diagnostics)
            return self._failure(
                "proposal_validate",
                *(diagnostics or (self._generic_transaction_diagnostic(),)),
                result={"validation": result},
            )
        return self._success("proposal_validate", {"validation": result})

    def _transaction_dry_run(self, arguments: dict[str, Any]) -> ToolResponse:
        proposal = TransactionProposal.model_validate(arguments["proposal"])
        result = dry_run(self._root, proposal)
        if not result.valid:
            diagnostics = self._transaction_diagnostics(result.diagnostics)
            return self._failure(
                "transaction_dry_run",
                *(diagnostics or (self._generic_transaction_diagnostic(),)),
                result={"dry_run": result},
            )
        return self._success("transaction_dry_run", {"dry_run": result})

    def _transaction_apply(self, arguments: dict[str, Any]) -> ToolResponse:
        proposal = TransactionProposal.model_validate(arguments["proposal"])
        result = apply(self._root, proposal, approved=True)
        warnings: tuple[McpDiagnostic, ...] = ()
        if result.projection.state is ProjectionState.STALE:
            warnings = (
                self._diagnostic(
                    code=result.projection.diagnostic_code or "TXN-PROJECTION-STALE",
                    category=ErrorCategory.PARTIAL_SUCCESS,
                    severity=DiagnosticSeverity.WARNING,
                    message="The transaction committed but its derived projection is stale.",
                    repair_action=result.projection.repair_action,
                ),
            )
        return self._success("transaction_apply", {"receipt": result}, warnings=warnings)

    def _index_rebuild(self, arguments: dict[str, Any]) -> ToolResponse:
        del arguments
        report = self._projection().rebuild()
        return self._success("index_rebuild", {"rebuild": report})

    def _draft_save(self, arguments: dict[str, Any]) -> ToolResponse:
        payload = DraftPayload.model_validate(
            {key: value for key, value in arguments.items() if key != "approved"}
        )
        result = save_draft(self._root, payload, approved=True)
        return self._success(
            "draft_save",
            {
                "operation": result.operation,
                "draft": result.draft,
                "receipt": result.receipt,
            },
        )

    def _not_implemented(self, name: str, dependency: str) -> ToolResponse:
        return self._failure(
            name,
            self._diagnostic(
                code="NOT-IMPLEMENTED",
                category=ErrorCategory.UNAVAILABLE_DEPENDENCY,
                message=f"This tool is reserved for {dependency} and is not implemented yet.",
            ),
            result={"dependency": dependency, "implemented": False},
        )

    def _success(
        self,
        name: str,
        result: object,
        *,
        warnings: tuple[McpDiagnostic, ...] = (),
    ) -> ToolResponse:
        envelope = McpEnvelope(
            ok=True,
            context_id=self._context_id,
            result=safe_result_object(result),
            warnings=warnings,
        )
        return ToolResponse(
            envelope=envelope,
            summary=f"{name} completed for context {self._context_id}.",
        )

    def _failure(
        self,
        name: str,
        *errors: McpDiagnostic,
        result: object | None = None,
        warnings: tuple[McpDiagnostic, ...] = (),
    ) -> ToolResponse:
        envelope = McpEnvelope(
            ok=False,
            context_id=self._context_id,
            result=safe_result_object({} if result is None else result),
            warnings=warnings,
            errors=errors,
        )
        return ToolResponse(
            envelope=envelope,
            summary=(
                f"{safe_diagnostic_text(name, fallback='tool')} failed with {errors[0].code}."
            ),
        )

    def _exception_response(self, name: str, error: Exception) -> ToolResponse:
        if isinstance(error, ProposalValidationError):
            diagnostics = self._transaction_diagnostics(error.result.diagnostics)
            return self._failure(
                name,
                *(diagnostics or (self._generic_transaction_diagnostic(),)),
                result={"validation": error.result},
            )
        if isinstance(error, (PreimageChangedError, PostconditionRollbackError)):
            diagnostics = self._transaction_diagnostics(error.result.diagnostics)
            return self._failure(
                name,
                *(diagnostics or (self._generic_transaction_diagnostic(),)),
                result={"recovery": error.result},
            )
        if isinstance(error, TransactionConflictError):
            return self._failure(
                name,
                self._diagnostic(
                    code=error.code,
                    category=ErrorCategory.CONFLICT,
                    message="A transaction conflict prevented the operation.",
                ),
            )
        if isinstance(error, LedgerIntegrityError):
            return self._failure(
                name,
                self._diagnostic(
                    code="TXN-LEDGER-INTEGRITY",
                    category=ErrorCategory.USER_CORRECTABLE,
                    message="The transaction audit ledger failed integrity verification.",
                ),
            )
        if isinstance(error, _BoundaryInputError):
            return self._failure(
                name,
                self._diagnostic(
                    code=error.code,
                    category=ErrorCategory.CONTEXT_BOUNDARY,
                    message=error,
                    path=error.path,
                ),
            )
        if isinstance(error, ValidationError):
            return self._failure(
                name,
                self._diagnostic(
                    code="INVALID_INPUT",
                    category=ErrorCategory.USAGE_CONFIGURATION,
                    message="Tool input does not satisfy the typed engine contract.",
                ),
            )
        if isinstance(error, InvalidContextError):
            return self._failure(
                name,
                self._diagnostic(
                    code="INVALID_CONTEXT",
                    category=ErrorCategory.USER_CORRECTABLE,
                    message="Context configuration is invalid.",
                ),
            )
        if isinstance(error, ContextNotFoundError):
            return self._failure(
                name,
                self._diagnostic(
                    code="CONTEXT_NOT_FOUND",
                    category=ErrorCategory.USER_CORRECTABLE,
                    message="The bound context is unavailable.",
                ),
            )
        if isinstance(error, Fts5UnavailableError):
            return self._failure(
                name,
                self._diagnostic(
                    code="DEPENDENCY_UNAVAILABLE",
                    category=ErrorCategory.UNAVAILABLE_DEPENDENCY,
                    message="The required SQLite FTS5 capability is unavailable.",
                ),
            )
        if isinstance(error, ProjectionError):
            return self._failure(
                name,
                self._diagnostic(
                    code="USER_CORRECTABLE",
                    category=ErrorCategory.USER_CORRECTABLE,
                    message="The context projection could not complete the operation.",
                    repair_action="Run `workctx index rebuild` and retry.",
                ),
            )
        if isinstance(error, (ValueError, TypeError)):
            return self._failure(
                name,
                self._diagnostic(
                    code="INVALID_INPUT",
                    category=ErrorCategory.USAGE_CONFIGURATION,
                    message="Tool input is invalid for this operation.",
                ),
            )
        if isinstance(error, (WorkctxError, PermissionError)):
            category = _category_for_exit(exit_code_for(error))
            return self._failure(
                name,
                self._diagnostic(
                    code=_default_code(error),
                    category=category,
                    message=error,
                ),
            )
        return self._failure(
            name,
            self._diagnostic(
                code="INTERNAL_ERROR",
                category=ErrorCategory.INTERNAL_FAILURE,
                message="Unexpected internal failure.",
            ),
        )

    def _validation_diagnostic(self, issue: ValidationIssue) -> McpDiagnostic:
        severity = DiagnosticSeverity(issue.severity.value)
        return self._diagnostic(
            code=issue.code,
            category=ErrorCategory.USER_CORRECTABLE,
            severity=severity,
            message=_safe_validation_message(issue),
            path=issue.path,
            repair_action=issue.repair_action,
        )

    def _transaction_diagnostics(
        self,
        diagnostics: Sequence[TransactionDiagnostic],
    ) -> tuple[McpDiagnostic, ...]:
        return tuple(
            self._diagnostic(
                code=diagnostic.code,
                category=(
                    ErrorCategory.USER_CORRECTABLE
                    if diagnostic.severity is not TransactionDiagnosticSeverity.WARNING
                    else ErrorCategory.PARTIAL_SUCCESS
                ),
                severity=DiagnosticSeverity(diagnostic.severity.value),
                message=diagnostic.message,
                path=diagnostic.path,
                repair_action=diagnostic.repair_action,
            )
            for diagnostic in diagnostics
        )

    def _generic_transaction_diagnostic(self) -> McpDiagnostic:
        return self._diagnostic(
            code="USER_CORRECTABLE",
            category=ErrorCategory.USER_CORRECTABLE,
            message="The transaction engine rejected the operation.",
        )

    @staticmethod
    def _diagnostic(
        *,
        code: str,
        category: ErrorCategory,
        message: object,
        severity: DiagnosticSeverity = DiagnosticSeverity.ERROR,
        path: object | None = None,
        repair_action: object | None = None,
    ) -> McpDiagnostic:
        return McpDiagnostic(
            code=code,
            category=category,
            severity=severity,
            message=safe_diagnostic_text(message),
            path=(None if path is None else safe_diagnostic_text(path, fallback="")),
            repair_action=(
                None if repair_action is None else safe_diagnostic_text(repair_action, fallback="")
            ),
        )


def _looks_like_path_escape(value: str) -> bool:
    if value.casefold().startswith("file://"):
        return True
    if value.startswith(("/", "\\")) or _WINDOWS_ABSOLUTE_PATH.match(value):
        return True
    normalized = value.replace("\\", "/")
    return ".." in normalized.split("/")


def _safe_validation_message(issue: ValidationIssue) -> str:
    if issue.code == "CTX-CONFIG":
        return "Context configuration is invalid."
    return safe_diagnostic_text(issue.message)


def _category_for_exit(exit_code: int) -> ErrorCategory:
    return {
        ExitCode.USER_CORRECTABLE: ErrorCategory.USER_CORRECTABLE,
        ExitCode.USAGE_CONFIGURATION: ErrorCategory.USAGE_CONFIGURATION,
        ExitCode.CONTEXT_BOUNDARY: ErrorCategory.CONTEXT_BOUNDARY,
        ExitCode.CONFLICT: ErrorCategory.CONFLICT,
        ExitCode.UNAVAILABLE_DEPENDENCY: ErrorCategory.UNAVAILABLE_DEPENDENCY,
        ExitCode.PARTIAL_SUCCESS: ErrorCategory.PARTIAL_SUCCESS,
        ExitCode.INTERNAL_FAILURE: ErrorCategory.INTERNAL_FAILURE,
    }.get(ExitCode(exit_code), ErrorCategory.INTERNAL_FAILURE)


def _default_code(error: BaseException) -> str:
    if isinstance(error, ContextBoundaryError | PermissionError):
        return "CONTEXT_BOUNDARY"
    if isinstance(error, ConflictError):
        return "CONFLICT"
    if isinstance(error, UnavailableDependencyError):
        return "DEPENDENCY_UNAVAILABLE"
    if isinstance(error, StaleDerivedStateError):
        return "STALE_DERIVED_STATE"
    if isinstance(error, UsageConfigurationError):
        return "USAGE_CONFIGURATION"
    if isinstance(error, UserCorrectableError):
        return "USER_CORRECTABLE"
    return "INTERNAL_ERROR"


__all__ = ["McpToolService"]
