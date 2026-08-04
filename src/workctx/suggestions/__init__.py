"""Public suggestion-record contracts and approved lifecycle APIs."""

from workctx.suggestions.errors import (
    SuggestionApprovalRequiredError,
    SuggestionContextError,
    SuggestionNotFoundError,
    SuggestionOperationError,
    SuggestionProposalError,
    SuggestionSequenceExhaustedError,
    SuggestionStateError,
)
from workctx.suggestions.models import (
    SUGGESTION_ID_PATTERN,
    SuggestionDocument,
    SuggestionMutationResult,
    SuggestionPayload,
    SuggestionRecord,
    SuggestionStatus,
    SuggestionType,
)
from workctx.suggestions.service import (
    SuggestionService,
    adopt_suggestion,
    create_suggestion,
    get_suggestion,
    list_suggestions,
    reject_suggestion,
)

__all__ = [
    "SUGGESTION_ID_PATTERN",
    "SuggestionApprovalRequiredError",
    "SuggestionContextError",
    "SuggestionDocument",
    "SuggestionMutationResult",
    "SuggestionNotFoundError",
    "SuggestionOperationError",
    "SuggestionPayload",
    "SuggestionProposalError",
    "SuggestionRecord",
    "SuggestionSequenceExhaustedError",
    "SuggestionService",
    "SuggestionStateError",
    "SuggestionStatus",
    "SuggestionType",
    "adopt_suggestion",
    "create_suggestion",
    "get_suggestion",
    "list_suggestions",
    "reject_suggestion",
]
