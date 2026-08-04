"""Value-safe models for secret references and resolver metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self, SupportsIndex

from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema

from workctx.secrets.errors import InvalidSecretRefError, InvalidSecretValueError

REDACTED_SECRET = "[REDACTED]"
_SECRET_REF_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True, slots=True)
class SecretRef:
    """A validated, machine-global secret name."""

    name: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or len(self.name) > 64
            or _SECRET_REF_PATTERN.fullmatch(self.name) is None
        ):
            raise InvalidSecretRefError

    @classmethod
    def parse(cls, value: str | Self) -> Self:
        """Return a validated reference without changing an existing instance."""

        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise InvalidSecretRefError
        return cls(value)

    @property
    def env_var(self) -> str:
        """Return the documented environment-variable resolver key."""

        return f"WORKCTX_SECRET_{self.name.replace('-', '_').upper()}"

    def __str__(self) -> str:
        return self.name


class SecretValue:
    """Opaque secret text whose only value-returning API is :meth:`reveal`."""

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str):
            raise InvalidSecretValueError
        self.__value = value

    def reveal(self) -> str:
        """Return the wrapped value to an explicitly authorized consumer."""

        return self.__value

    def __repr__(self) -> str:
        return "SecretValue([REDACTED])"

    def __str__(self) -> str:
        return REDACTED_SECRET

    def __format__(self, format_spec: str) -> str:
        return format(REDACTED_SECRET, format_spec)

    def __reduce__(self) -> str | tuple[Any, ...]:
        raise TypeError("SecretValue cannot be pickled.")

    def __reduce_ex__(self, protocol: SupportsIndex) -> str | tuple[Any, ...]:
        del protocol
        raise TypeError("SecretValue cannot be pickled.")

    @classmethod
    def _validate_for_pydantic(cls, value: object) -> SecretValue:
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(value)
        raise InvalidSecretValueError

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        _source_type: Any,
        _handler: GetCoreSchemaHandler,
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_plain_validator_function(
            cls._validate_for_pydantic,
            json_schema_input_schema=core_schema.str_schema(),
            serialization=core_schema.plain_serializer_function_ser_schema(
                _redact_for_serialization,
                info_arg=False,
                return_schema=core_schema.str_schema(),
            ),
        )


class SecretLayer(StrEnum):
    """Resolver layer that currently satisfies a secret reference."""

    ENVIRONMENT = "env"
    OS_STORE = "os-store"


@dataclass(frozen=True, slots=True)
class SecretPresence:
    """Names-and-booleans-only metadata suitable for diagnostics and envelopes."""

    name: str
    environment: bool
    os_store: bool | None

    @property
    def resolved_layer(self) -> SecretLayer | None:
        if self.environment:
            return SecretLayer.ENVIRONMENT
        if self.os_store:
            return SecretLayer.OS_STORE
        return None


def _redact_for_serialization(_value: object) -> str:
    return REDACTED_SECRET
