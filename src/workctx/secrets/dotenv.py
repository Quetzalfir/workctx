"""Minimal, value-safe dotenv import support."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from workctx.secrets.errors import DotenvParseError, SecretImportError
from workctx.secrets.models import SecretRef, SecretValue

_DOTENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DOUBLE_QUOTE_ESCAPES = {
    "\\": "\\",
    '"': '"',
    "n": "\n",
    "r": "\r",
    "t": "\t",
}
_SHRED_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True, slots=True)
class DotenvEntry:
    """One normalized secret name and an opaque imported value."""

    ref: SecretRef
    value: SecretValue


def parse_dotenv(path: Path) -> tuple[DotenvEntry, ...]:
    """Parse a UTF-8 dotenv file completely before any caller stores values."""

    source = path.expanduser()
    failed = False
    content = ""
    try:
        if source.is_symlink() or not source.is_file():
            raise OSError
        content = source.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        failed = True
    if failed:
        raise SecretImportError("The dotenv file could not be read safely.")

    entries: list[DotenvEntry] = []
    seen_names: set[str] = set()
    for line_number, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "\x00" in line or "=" not in line:
            raise DotenvParseError(line_number)
        raw_name, raw_value = line.split("=", maxsplit=1)
        ref = _normalize_name(raw_name.strip(), line_number)
        if ref.name in seen_names:
            raise DotenvParseError(line_number)
        seen_names.add(ref.name)
        entries.append(
            DotenvEntry(
                ref=ref,
                value=SecretValue(_parse_value(raw_value, line_number)),
            )
        )
    return tuple(entries)


def shred_dotenv(path: Path) -> None:
    """Best-effort overwrite, truncate, and delete of one regular source file."""

    source = path.expanduser()
    descriptor = -1
    failed = False
    opened_stat: os.stat_result | None = None
    try:
        flags = os.O_WRONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(source, flags)
        opened_stat = os.fstat(descriptor)
        linked_stat = source.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or not stat.S_ISREG(linked_stat.st_mode)
            or not os.path.samestat(opened_stat, linked_stat)
        ):
            raise OSError

        remaining = opened_stat.st_size
        zeroes = b"\0" * min(_SHRED_CHUNK_SIZE, max(1, remaining))
        while remaining:
            written = os.write(descriptor, zeroes[: min(len(zeroes), remaining)])
            if written <= 0:
                raise OSError
            remaining -= written
        os.fsync(descriptor)
        os.ftruncate(descriptor, 0)
        os.fsync(descriptor)
    except OSError:
        failed = True
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                failed = True

    if not failed and opened_stat is not None:
        try:
            current_stat = source.stat(follow_symlinks=False)
            if not os.path.samestat(opened_stat, current_stat):
                raise OSError
            source.unlink()
        except OSError:
            failed = True
    if failed:
        raise SecretImportError("The imported dotenv file could not be securely removed.")


def _normalize_name(raw_name: str, line_number: int) -> SecretRef:
    if _DOTENV_NAME.fullmatch(raw_name) is None:
        raise DotenvParseError(line_number)
    candidate = raw_name.lower().replace("_", "-")
    try:
        return SecretRef(candidate)
    except Exception:
        raise DotenvParseError(line_number) from None


def _parse_value(raw_value: str, line_number: int) -> str:
    value = raw_value.lstrip()
    if not value:
        return ""
    if value[0] == "'":
        return _parse_single_quoted(value, line_number)
    if value[0] == '"':
        return _parse_double_quoted(value, line_number)

    comment_at: int | None = None
    for index, character in enumerate(value):
        if character == "#" and (index == 0 or value[index - 1].isspace()):
            comment_at = index
            break
    if comment_at is not None:
        value = value[:comment_at]
    return value.rstrip()


def _parse_single_quoted(value: str, line_number: int) -> str:
    closing = value.find("'", 1)
    if closing < 0:
        raise DotenvParseError(line_number)
    _require_comment_suffix(value[closing + 1 :], line_number)
    return value[1:closing]


def _parse_double_quoted(value: str, line_number: int) -> str:
    parsed: list[str] = []
    index = 1
    while index < len(value):
        character = value[index]
        if character == '"':
            _require_comment_suffix(value[index + 1 :], line_number)
            return "".join(parsed)
        if character == "\\":
            index += 1
            if index >= len(value) or value[index] not in _DOUBLE_QUOTE_ESCAPES:
                raise DotenvParseError(line_number)
            parsed.append(_DOUBLE_QUOTE_ESCAPES[value[index]])
        else:
            parsed.append(character)
        index += 1
    raise DotenvParseError(line_number)


def _require_comment_suffix(suffix: str, line_number: int) -> None:
    normalized = suffix.strip()
    if normalized and not normalized.startswith("#"):
        raise DotenvParseError(line_number)
