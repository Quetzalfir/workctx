"""Bounded, inert loading and rendering of user-owned personalization layers."""

from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path

from workctx.adapters.filesystem import ContextRegistry, RegistryError
from workctx.validation.engine import contains_possible_secret

from ._safe_fs import SafeFilesystemError, SafeRoot
from .errors import InvalidAdapterStateError
from .models import PersonalizationLayerName, PersonalizationLayerStatus
from .renderers import content_hash

PERSONALIZATION_FILENAME = "instructions.md"
PERSONALIZATION_LAYER_MAX_BYTES = 64 * 1024
PERSONALIZATION_START_MARKER = "<!-- workctx-personalization:start -->"
PERSONALIZATION_END_MARKER = "<!-- workctx-personalization:end -->"


class PersonalizationLayerError(InvalidAdapterStateError):
    """Base class for a personalization layer that cannot be merged safely."""

    def __init__(
        self,
        message: str,
        *,
        layer: PersonalizationLayerName,
        path: Path,
        size_bytes: int | None = None,
    ) -> None:
        super().__init__(message)
        self.layer = layer
        self.path = path
        self.size_bytes = size_bytes


class PersonalizationLayerTooLargeError(PersonalizationLayerError):
    """Raised when one layer exceeds the fixed per-layer byte cap."""


class PersonalizationSecretError(PersonalizationLayerError):
    """Raised when the public secret detector refuses one layer."""

    def __init__(
        self,
        *,
        layer: PersonalizationLayerName,
        path: Path,
        size_bytes: int,
        line_number: int,
    ) -> None:
        # Keep this diagnostic deliberately content-free and path-free.
        super().__init__(
            f"{layer.value} layer, line {line_number}",
            layer=layer,
            path=path,
            size_bytes=size_bytes,
        )
        self.line_number = line_number


class PersonalizationEncodingError(PersonalizationLayerError):
    """Raised when a present layer is not UTF-8 Markdown."""


@dataclass(frozen=True, slots=True)
class PersonalizationLayer:
    """One safely observed optional user-owned Markdown layer."""

    layer: PersonalizationLayerName
    path: Path
    content: bytes | None = None
    content_hash: str | None = None

    def __post_init__(self) -> None:
        if (self.content is None) != (self.content_hash is None):
            raise ValueError("Personalization layer content and hash must be present together")

    @property
    def present(self) -> bool:
        return self.content is not None

    @property
    def size_bytes(self) -> int | None:
        return None if self.content is None else len(self.content)

    def status(self, *, merged: bool) -> PersonalizationLayerStatus:
        return PersonalizationLayerStatus(
            layer=self.layer,
            path=str(self.path),
            present=self.present,
            size_bytes=self.size_bytes,
            merged=self.present and merged,
        )


@dataclass(frozen=True, slots=True)
class PersonalizationLayers:
    """The fixed user-before-context layer sequence for one selected root."""

    user: PersonalizationLayer
    context: PersonalizationLayer

    def __post_init__(self) -> None:
        if self.user.layer is not PersonalizationLayerName.USER:
            raise ValueError("The user personalization slot must contain the user layer")
        if self.context.layer is not PersonalizationLayerName.CONTEXT:
            raise ValueError("The context personalization slot must contain the context layer")

    @property
    def ordered(self) -> tuple[PersonalizationLayer, PersonalizationLayer]:
        return (self.user, self.context)

    @property
    def present(self) -> tuple[PersonalizationLayer, ...]:
        return tuple(layer for layer in self.ordered if layer.present)

    def statuses(self, *, merged: bool) -> tuple[PersonalizationLayerStatus, ...]:
        return tuple(layer.status(merged=merged) for layer in self.ordered)


def user_personalization_path() -> Path:
    """Return ``instructions.md`` beside the public user context registry path."""

    return ContextRegistry().path.with_name(PERSONALIZATION_FILENAME)


def _missing_layer(
    layer: PersonalizationLayerName,
    path: Path,
) -> PersonalizationLayer:
    return PersonalizationLayer(layer=layer, path=path)


def _read_layer(
    layer: PersonalizationLayerName,
    path: Path,
) -> PersonalizationLayer:
    try:
        path.parent.lstat()
    except FileNotFoundError:
        return _missing_layer(layer, path)
    except OSError as error:
        raise PersonalizationLayerError(
            f"{layer.value} personalization layer directory is unavailable",
            layer=layer,
            path=path,
        ) from error

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return _missing_layer(layer, path)
    except OSError as error:
        raise PersonalizationLayerError(
            f"{layer.value} personalization layer is unavailable",
            layer=layer,
            path=path,
        ) from error
    if stat.S_ISREG(metadata.st_mode) and metadata.st_size > PERSONALIZATION_LAYER_MAX_BYTES:
        raise PersonalizationLayerTooLargeError(
            f"{layer.value} personalization layer exceeds {PERSONALIZATION_LAYER_MAX_BYTES} bytes",
            layer=layer,
            path=path,
            size_bytes=metadata.st_size,
        )

    try:
        snapshot = SafeRoot(path.parent).inspect_file(path.name)
    except SafeFilesystemError as error:
        raise PersonalizationLayerError(
            f"{layer.value} personalization layer at {path} is not a safe regular file "
            f"({error}); move it to a plain local path outside links, redirected, or "
            f"synchronized folders, or remove it to install without personalization",
            layer=layer,
            path=path,
        ) from error
    if not snapshot.exists:
        return _missing_layer(layer, path)
    if snapshot.content is None:
        raise PersonalizationLayerError(
            f"{layer.value} personalization layer content is unavailable",
            layer=layer,
            path=path,
        )

    content = snapshot.content
    size = len(content)
    if size > PERSONALIZATION_LAYER_MAX_BYTES:
        raise PersonalizationLayerTooLargeError(
            f"{layer.value} personalization layer exceeds {PERSONALIZATION_LAYER_MAX_BYTES} bytes",
            layer=layer,
            path=path,
            size_bytes=size,
        )
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PersonalizationEncodingError(
            f"{layer.value} personalization layer must be UTF-8 Markdown",
            layer=layer,
            path=path,
            size_bytes=size,
        ) from error
    if contains_possible_secret(text):
        line_number = next(
            (
                number
                for number, line in enumerate(text.splitlines(), start=1)
                if contains_possible_secret(line)
            ),
            1,
        )
        raise PersonalizationSecretError(
            layer=layer,
            path=path,
            size_bytes=size,
            line_number=line_number,
        )
    return PersonalizationLayer(
        layer=layer,
        path=path,
        content=content,
        content_hash=content_hash(content),
    )


def load_personalization_layers(
    context_root: Path,
    *,
    include_context: bool = True,
) -> PersonalizationLayers:
    """Read both optional layers without creating, modifying, or interpreting either."""

    physical_root = context_root.resolve(strict=True)
    try:
        user_path = user_personalization_path()
    except RegistryError as error:
        raise PersonalizationLayerError(
            "user personalization layer location is unavailable",
            layer=PersonalizationLayerName.USER,
            path=Path(PERSONALIZATION_FILENAME),
        ) from error
    return PersonalizationLayers(
        user=_read_layer(
            PersonalizationLayerName.USER,
            user_path,
        ),
        context=(
            _read_layer(
                PersonalizationLayerName.CONTEXT,
                physical_root / PERSONALIZATION_FILENAME,
            )
            if include_context
            else _missing_layer(
                PersonalizationLayerName.CONTEXT,
                physical_root / PERSONALIZATION_FILENAME,
            )
        ),
    )


def _display_path(path: Path) -> str:
    """Keep provenance on one Markdown line even for unusual POSIX path names."""

    return str(path).replace("\r", r"\r").replace("\n", r"\n")


def render_personalization_section(layers: PersonalizationLayers) -> bytes:
    """Render present layers verbatim in fixed user-before-context precedence order."""

    if not layers.present:
        return b""
    output = bytearray(
        (
            f"{PERSONALIZATION_START_MARKER}\n"
            "## Work Context personalization (user-owned)\n\n"
            "The following Markdown is user-owned. Work Context only validates and "
            "merges it; it does not execute it. When instructions conflict, the later "
            "context layer takes precedence.\n\n"
        ).encode()
    )
    for layer in layers.present:
        heading = "User" if layer.layer is PersonalizationLayerName.USER else "Context"
        output.extend(f"### {heading} layer — {PERSONALIZATION_FILENAME}\n".encode())
        output.extend(f"from <{_display_path(layer.path)}>\n\n".encode())
        assert layer.content is not None
        output.extend(layer.content)
        if not layer.content.endswith(b"\n"):
            output.extend(b"\n")
        output.extend(b"\n")
    output.extend(f"{PERSONALIZATION_END_MARKER}\n".encode())
    return bytes(output)


def render_personalized_bridge(
    base_content: bytes,
    layers: PersonalizationLayers,
) -> bytes:
    """Append a clearly delimited personalization suffix when any layer is present."""

    section = render_personalization_section(layers)
    if not section:
        return base_content
    if base_content.endswith(b"\n\n"):
        separator = b""
    elif base_content.endswith(b"\n"):
        separator = b"\n"
    else:
        separator = b"\n\n"
    return base_content + separator + section


__all__ = [
    "PERSONALIZATION_END_MARKER",
    "PERSONALIZATION_FILENAME",
    "PERSONALIZATION_LAYER_MAX_BYTES",
    "PERSONALIZATION_START_MARKER",
    "PersonalizationEncodingError",
    "PersonalizationLayer",
    "PersonalizationLayerError",
    "PersonalizationLayerTooLargeError",
    "PersonalizationLayers",
    "PersonalizationSecretError",
    "load_personalization_layers",
    "render_personalization_section",
    "render_personalized_bridge",
    "user_personalization_path",
]
