"""Bounded, non-rendering artifact hashing and quarantine guards."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from workctx.ingestion.errors import ArtifactReadError
from workctx.ingestion.models import IngestionPolicy, QuarantineReason
from workctx.validation import contains_possible_secret

DEFAULT_HASH_CHUNK_BYTES = 1024 * 1024

_MEDIA_BY_SUFFIX = {
    ".bmp": "image/bmp",
    ".csv": "text/csv",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".eml": "message/rfc822",
    ".flac": "audio/flac",
    ".gif": "image/gif",
    ".htm": "text/html",
    ".html": "text/html",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".json": "application/json",
    ".log": "text/plain",
    ".md": "text/markdown",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".odf": "application/vnd.oasis.opendocument.formula",
    ".odg": "application/vnd.oasis.opendocument.graphics",
    ".odp": "application/vnd.oasis.opendocument.presentation",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ogg": "audio/ogg",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".rtf": "application/rtf",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".tsv": "text/tab-separated-values",
    ".txt": "text/plain",
    ".wav": "audio/wav",
    ".webm": "video/webm",
    ".webp": "image/webp",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xml": "application/xml",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
}

_SUPPORTED_APPLICATION_TYPES = frozenset(
    {
        "application/json",
        "application/msword",
        "application/pdf",
        "application/rtf",
        "application/vnd.ms-excel",
        "application/vnd.ms-powerpoint",
        "application/vnd.oasis.opendocument.formula",
        "application/vnd.oasis.opendocument.graphics",
        "application/vnd.oasis.opendocument.presentation",
        "application/vnd.oasis.opendocument.spreadsheet",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/xml",
        "application/yaml",
    }
)

_EXECUTABLE_SUFFIXES = frozenset(
    {
        ".apk",
        ".app",
        ".bat",
        ".cmd",
        ".com",
        ".cpl",
        ".deb",
        ".dll",
        ".dmg",
        ".exe",
        ".hta",
        ".ipa",
        ".jar",
        ".js",
        ".lnk",
        ".msi",
        ".msix",
        ".mjs",
        ".pkg",
        ".ps1",
        ".psm1",
        ".py",
        ".pyw",
        ".reg",
        ".rpm",
        ".scr",
        ".sh",
        ".vbe",
        ".vbs",
        ".wsf",
    }
)

_EXECUTABLE_MEDIA_TYPES = frozenset(
    {
        "application/java-archive",
        "application/vnd.android.package-archive",
        "application/vnd.microsoft.portable-executable",
        "application/x-dosexec",
        "application/x-executable",
        "application/x-msdownload",
        "application/x-msi",
        "application/x-sh",
    }
)

_PROMPT_INJECTION_MARKERS = (
    re.compile(
        r"(?i)\b(?:ignore|disregard|override)\s+(?:all\s+)?"
        r"(?:previous|prior|above|system|developer)\s+"
        r"(?:instructions|messages|prompts?)\b"
    ),
    re.compile(r"(?i)\b(?:system|developer)\s+(?:message|prompt)\b"),
    re.compile(r"(?i)<\s*/?\s*(?:system|developer|assistant)\s*>"),
    re.compile(
        r"(?is)\b(?:reveal|print|exfiltrate)\b.{0,120}"
        r"\b(?:credentials?|passwords?|secrets?|system\s+prompt|tokens?)\b"
    ),
)

_EXECUTABLE_MAGICS = (
    b"MZ",
    b"\x7fELF",
    b"\xca\xfe\xba\xbe",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
)


@dataclass(frozen=True, slots=True)
class ArtifactScan:
    """Bounded scan result without source content."""

    content_hash: str
    size_bytes: int
    media_type: str
    reasons: tuple[QuarantineReason, ...]


def scan_artifact(
    path: Path,
    *,
    declared_media_type: str | None,
    policy: IngestionPolicy,
) -> ArtifactScan:
    """Hash a stable regular file and apply streaming, non-rendering guards."""

    suffix = path.suffix.lower()
    inferred_media_type = _MEDIA_BY_SUFFIX.get(suffix)
    media_type = _normalize_media_type(declared_media_type) or (
        inferred_media_type or "application/octet-stream"
    )
    reasons: set[QuarantineReason] = set()
    if suffix in _EXECUTABLE_SUFFIXES or media_type in _EXECUTABLE_MEDIA_TYPES:
        reasons.add(QuarantineReason.EXECUTABLE_PAYLOAD)
    if (
        not _is_supported_media_type(media_type)
        or inferred_media_type is None
        or not _media_types_are_compatible(media_type, inferred_media_type)
    ):
        reasons.add(QuarantineReason.UNSUPPORTED_TYPE)

    digest = hashlib.sha256()
    tail = ""
    first_chunk = True
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or path.is_symlink() or _is_junction(path):
            raise ArtifactReadError()
        if before.st_size > policy.max_artifact_bytes:
            reasons.add(QuarantineReason.OVERSIZED)

        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or not _same_identity(before, opened):
                raise ArtifactReadError()
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = -1
                while chunk := stream.read(policy.scan_chunk_bytes):
                    digest.update(chunk)
                    if first_chunk:
                        first_chunk = False
                        if _has_executable_magic(chunk):
                            reasons.add(QuarantineReason.EXECUTABLE_PAYLOAD)
                    window = tail + chunk.decode("latin-1")
                    _scan_text_window(window, reasons)
                    tail = window[-policy.scan_overlap_chars :]
                after = os.fstat(stream.fileno())
            if not _stable_metadata(opened, after):
                raise ArtifactReadError()
        finally:
            if descriptor >= 0:
                os.close(descriptor)

        final = path.lstat()
        if not stat.S_ISREG(final.st_mode) or not _stable_metadata(before, final):
            raise ArtifactReadError()
    except ArtifactReadError:
        raise
    except (OSError, ValueError) as exc:
        raise ArtifactReadError() from exc

    return ArtifactScan(
        content_hash=f"sha256:{digest.hexdigest()}",
        size_bytes=before.st_size,
        media_type=media_type,
        reasons=tuple(sorted(reasons, key=lambda reason: reason.value)),
    )


def hash_preserved_file(path: Path) -> str:
    """Return a streaming SHA-256 for a regular preserved file."""

    digest = hashlib.sha256()
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or path.is_symlink() or _is_junction(path):
            raise ArtifactReadError()
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or not _same_identity(before, opened):
                raise ArtifactReadError()
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = -1
                for chunk in iter(lambda: stream.read(DEFAULT_HASH_CHUNK_BYTES), b""):
                    digest.update(chunk)
                after = os.fstat(stream.fileno())
            if not _stable_metadata(opened, after):
                raise ArtifactReadError()
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        final = path.lstat()
        if not stat.S_ISREG(final.st_mode) or not _stable_metadata(before, final):
            raise ArtifactReadError()
    except ArtifactReadError:
        raise
    except OSError as exc:
        raise ArtifactReadError() from exc
    return f"sha256:{digest.hexdigest()}"


def _scan_text_window(window: str, reasons: set[QuarantineReason]) -> None:
    candidates = (window, window.replace("\x00", "")) if "\x00" in window else (window,)
    if QuarantineReason.POSSIBLE_SECRET not in reasons and any(
        contains_possible_secret(candidate) for candidate in candidates
    ):
        reasons.add(QuarantineReason.POSSIBLE_SECRET)
    if QuarantineReason.PROMPT_INJECTION not in reasons and any(
        marker.search(candidate) is not None
        for candidate in candidates
        for marker in _PROMPT_INJECTION_MARKERS
    ):
        reasons.add(QuarantineReason.PROMPT_INJECTION)


def _normalize_media_type(value: str | None) -> str | None:
    if value is None:
        return None
    return value.partition(";")[0].strip().lower()


def _is_supported_media_type(media_type: str) -> bool:
    return (
        media_type.startswith(("audio/", "image/", "text/", "video/"))
        or media_type == "message/rfc822"
        or media_type in _SUPPORTED_APPLICATION_TYPES
    )


def _media_types_are_compatible(declared: str, inferred: str) -> bool:
    if declared == inferred:
        return True
    aliases = {
        frozenset({"application/json", "text/json"}),
        frozenset({"application/xml", "text/xml"}),
        frozenset({"application/yaml", "text/yaml", "text/x-yaml"}),
        frozenset({"audio/wav", "audio/x-wav"}),
    }
    return frozenset({declared, inferred}) in aliases


def _has_executable_magic(chunk: bytes) -> bool:
    return chunk.startswith(_EXECUTABLE_MAGICS) or chunk.startswith((b"#!", b"\xef\xbb\xbf#!"))


def _same_identity(first: os.stat_result, second: os.stat_result) -> bool:
    return first.st_dev == second.st_dev and first.st_ino == second.st_ino


def _stable_metadata(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        _same_identity(first, second)
        and first.st_size == second.st_size
        and first.st_mtime_ns == second.st_mtime_ns
    )


def _is_junction(path: Path) -> bool:
    return hasattr(path, "is_junction") and path.is_junction()


__all__ = ["ArtifactScan", "hash_preserved_file", "scan_artifact"]
