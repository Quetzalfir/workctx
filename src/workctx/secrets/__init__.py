"""Machine-global secret references backed by environment and OS keyring layers."""

from workctx.secrets.dotenv import DotenvEntry, parse_dotenv, shred_dotenv
from workctx.secrets.errors import (
    DotenvParseError,
    InvalidSecretRefError,
    InvalidSecretValueError,
    SecretBackendUnavailableError,
    SecretError,
    SecretImportError,
    SecretIndexError,
    SecretNotFoundError,
)
from workctx.secrets.models import (
    REDACTED_SECRET,
    SecretLayer,
    SecretPresence,
    SecretRef,
    SecretValue,
)
from workctx.secrets.service import (
    delete,
    env_var_name,
    environment_contains,
    exists,
    inspect_presence,
    list_names,
    os_store_available,
    resolution_layer,
    resolve,
    store,
)

__all__ = [
    "REDACTED_SECRET",
    "DotenvEntry",
    "DotenvParseError",
    "InvalidSecretRefError",
    "InvalidSecretValueError",
    "SecretBackendUnavailableError",
    "SecretError",
    "SecretImportError",
    "SecretIndexError",
    "SecretLayer",
    "SecretNotFoundError",
    "SecretPresence",
    "SecretRef",
    "SecretValue",
    "delete",
    "env_var_name",
    "environment_contains",
    "exists",
    "inspect_presence",
    "list_names",
    "os_store_available",
    "parse_dotenv",
    "resolution_layer",
    "resolve",
    "shred_dotenv",
    "store",
]
