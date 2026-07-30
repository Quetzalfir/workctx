"""Frontmatter splitting and parsing shared by canonical readers.

Lead-provided at Wave 2 open so the filesystem store (WP-200) and the SQLite
projection rebuild (WP-210) parse identically. The closing delimiter is the
first column-zero ``---`` line after the opener; YAML block scalars indent
their content, so a delimiter-like value inside valid frontmatter cannot
produce a column-zero ``---`` line.
"""

from __future__ import annotations

from typing import Any

import yaml

_DELIMITER = "---"


def split_frontmatter(text: str) -> tuple[str, str]:
    """Split a canonical Markdown document into raw YAML frontmatter and body."""

    lines = text.split("\n")
    if not lines or lines[0].rstrip("\r") != _DELIMITER:
        raise ValueError("Canonical document must start with a '---' frontmatter delimiter")
    for index in range(1, len(lines)):
        if lines[index].rstrip("\r") == _DELIMITER:
            raw = "\n".join(lines[1:index])
            body = "\n".join(lines[index + 1 :])
            return raw, body
    raise ValueError("Frontmatter is missing its closing '---' delimiter")


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse frontmatter into a mapping plus the Markdown body."""

    raw, body = split_frontmatter(text)
    loaded: Any = yaml.safe_load(raw)
    if not isinstance(loaded, dict):
        raise ValueError("Frontmatter must contain a YAML mapping")
    return loaded, body
