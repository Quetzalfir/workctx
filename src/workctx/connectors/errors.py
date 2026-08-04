"""Typed, content-free connector runtime failures."""

from __future__ import annotations

from workctx.errors import UserCorrectableError, WorkctxError


class ConnectorError(WorkctxError):
    """Base class for expected connector failures."""


class ConnectorManifestError(UserCorrectableError, ConnectorError):
    """Raised when one declarative connector manifest cannot be trusted."""

    def __init__(self, path: str = "07_connectors") -> None:
        self.path = path
        super().__init__(f"Connector manifest configuration is invalid at {path}.")


class DuplicateConnectorNameError(ConnectorManifestError):
    """Raised when two files declare the same context-local connector name."""

    def __init__(self, connector_name: str) -> None:
        self.connector_name = connector_name
        self.path = "07_connectors"
        UserCorrectableError.__init__(
            self,
            f"Connector name '{connector_name}' is declared more than once in this context.",
        )


class ConnectorNotFoundError(UserCorrectableError, ConnectorError):
    def __init__(self, connector_name: str) -> None:
        self.connector_name = connector_name
        super().__init__(f"Connector '{connector_name}' was not found.")


class ConnectorSnapshotNotFoundError(UserCorrectableError, ConnectorError):
    def __init__(self, connector_name: str, snapshot_id: str) -> None:
        self.connector_name = connector_name
        self.snapshot_id = snapshot_id
        super().__init__(f"Connector '{connector_name}' has no snapshot named '{snapshot_id}'.")


class ConnectorSnapshotError(ConnectorError):
    """Base class for one content-free per-snapshot runtime failure."""

    def __init__(self, message: str, connector_name: str, snapshot_id: str) -> None:
        self.connector_name = connector_name
        self.snapshot_id = snapshot_id
        super().__init__(message)


class ConnectorSecretResolutionError(ConnectorSnapshotError):
    def __init__(self, connector_name: str, snapshot_id: str) -> None:
        super().__init__(
            f"Connector '{connector_name}' snapshot '{snapshot_id}' could not resolve "
            "its secret reference.",
            connector_name,
            snapshot_id,
        )


class ConnectorConnectionError(ConnectorSnapshotError):
    def __init__(self, connector_name: str, snapshot_id: str) -> None:
        super().__init__(
            f"Connector '{connector_name}' snapshot '{snapshot_id}' could not connect.",
            connector_name,
            snapshot_id,
        )


class ConnectorTimeoutError(ConnectorSnapshotError):
    def __init__(self, connector_name: str, snapshot_id: str) -> None:
        super().__init__(
            f"Connector '{connector_name}' snapshot '{snapshot_id}' timed out.",
            connector_name,
            snapshot_id,
        )


class ConnectorStatusError(ConnectorSnapshotError):
    def __init__(self, connector_name: str, snapshot_id: str, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(
            f"Connector '{connector_name}' snapshot '{snapshot_id}' returned HTTP {status_code}.",
            connector_name,
            snapshot_id,
        )


class ConnectorSizeLimitError(ConnectorSnapshotError):
    def __init__(self, connector_name: str, snapshot_id: str, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        super().__init__(
            f"Connector '{connector_name}' snapshot '{snapshot_id}' exceeded its "
            f"{max_bytes}-byte limit.",
            connector_name,
            snapshot_id,
        )


class ConnectorRedirectError(ConnectorSnapshotError):
    def __init__(self, connector_name: str, snapshot_id: str) -> None:
        super().__init__(
            f"Connector '{connector_name}' snapshot '{snapshot_id}' refused an unsafe redirect.",
            connector_name,
            snapshot_id,
        )


class ConnectorSecretExposureError(ConnectorSnapshotError):
    def __init__(self, connector_name: str, snapshot_id: str) -> None:
        super().__init__(
            f"Connector '{connector_name}' snapshot '{snapshot_id}' reflected "
            "authentication material.",
            connector_name,
            snapshot_id,
        )


class ConnectorWriteError(ConnectorSnapshotError):
    def __init__(self, connector_name: str, snapshot_id: str) -> None:
        super().__init__(
            f"Connector '{connector_name}' snapshot '{snapshot_id}' could not be written safely.",
            connector_name,
            snapshot_id,
        )


class ConnectorRegistrationError(ConnectorSnapshotError):
    def __init__(self, connector_name: str, snapshot_id: str) -> None:
        super().__init__(
            f"Connector '{connector_name}' snapshot '{snapshot_id}' could not be registered.",
            connector_name,
            snapshot_id,
        )


__all__ = [
    "ConnectorConnectionError",
    "ConnectorError",
    "ConnectorManifestError",
    "ConnectorNotFoundError",
    "ConnectorRedirectError",
    "ConnectorRegistrationError",
    "ConnectorSecretExposureError",
    "ConnectorSecretResolutionError",
    "ConnectorSizeLimitError",
    "ConnectorSnapshotError",
    "ConnectorSnapshotNotFoundError",
    "ConnectorStatusError",
    "ConnectorTimeoutError",
    "ConnectorWriteError",
    "DuplicateConnectorNameError",
]
