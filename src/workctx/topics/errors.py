"""Typed user-correctable failures for topic operations."""

from __future__ import annotations

from workctx.errors import UserCorrectableError


class TopicError(UserCorrectableError):
    """Base error for deterministic topic operations."""


class TopicRegistryError(TopicError):
    """Raised when the canonical topic registry is unavailable or invalid."""


class TopicNotFoundError(TopicError):
    """Raised when a topic selector cannot resolve to an active topic."""


class TopicDuplicateError(TopicError):
    """Raised when the duplicate guard refuses a topic creation."""


class TopicMutationError(TopicError):
    """Raised when a topic mutation cannot produce a valid audited transaction."""


__all__ = [
    "TopicDuplicateError",
    "TopicError",
    "TopicMutationError",
    "TopicNotFoundError",
    "TopicRegistryError",
]
