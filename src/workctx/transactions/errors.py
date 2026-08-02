"""Expected, sanitized failures raised by the transaction engine."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from workctx.errors import ConflictError, UserCorrectableError, WorkctxError

if TYPE_CHECKING:
    from workctx.transactions.models import ProposalValidationResult, RecoveryResult

_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_-]{0,63}$")


class TransactionError(WorkctxError):
    """Base class for expected transaction-engine failures."""


class ProposalValidationError(UserCorrectableError, TransactionError):
    """Raised when a proposal is rejected before canonical mutation."""

    def __init__(self, result: ProposalValidationResult) -> None:
        self.result = result
        super().__init__("Transaction proposal validation failed.")


class TransactionConflictError(ConflictError, TransactionError):
    """Stable-code conflict that never includes source content in its message."""

    def __init__(self, code: str) -> None:
        self.code = code if _ERROR_CODE.fullmatch(code) is not None else "TXN-CONFLICT"
        super().__init__(f"Transaction conflict ({self.code}).")


class StaleRevisionError(TransactionConflictError):
    def __init__(self) -> None:
        super().__init__("TXN-STALE-REVISION")


class DuplicateProposalError(TransactionConflictError):
    def __init__(self) -> None:
        super().__init__("TXN-DUPLICATE-PROPOSAL")


class PreimageChangedError(TransactionConflictError):
    """A preimage changed after validation; rollback was durably audited."""

    def __init__(self, result: RecoveryResult) -> None:
        self.result = result
        super().__init__("TXN-PREIMAGE-CHANGED")


class RecoveryPendingError(TransactionConflictError):
    """Raised when an unresolved WP-200 intent must be recovered first."""

    def __init__(self, inspection: object) -> None:
        self.inspection = inspection
        super().__init__("TXN-RECOVERY-PENDING")


class LedgerIntegrityError(UserCorrectableError, TransactionError):
    def __init__(self) -> None:
        super().__init__("The transaction audit ledger failed integrity verification.")


class PostconditionRollbackError(UserCorrectableError, TransactionError):
    """Raised after a failed postcondition has been durably rolled back and audited."""

    def __init__(self, result: RecoveryResult) -> None:
        self.result = result
        super().__init__(
            "Transaction postcondition validation failed and changes were rolled back."
        )


__all__ = [
    "DuplicateProposalError",
    "LedgerIntegrityError",
    "PostconditionRollbackError",
    "PreimageChangedError",
    "ProposalValidationError",
    "RecoveryPendingError",
    "StaleRevisionError",
    "TransactionConflictError",
    "TransactionError",
]
