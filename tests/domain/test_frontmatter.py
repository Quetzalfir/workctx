import pytest

from workctx.domain.frontmatter import parse_frontmatter, split_frontmatter


def test_parse_returns_mapping_and_body() -> None:
    data, body = parse_frontmatter("---\nid: TASK-2026-001\n---\n\nBody text.\n")
    assert data == {"id": "TASK-2026-001"}
    assert body == "\nBody text.\n"


def test_delimiter_like_value_inside_frontmatter_is_preserved() -> None:
    text = '---\nnote: "before --- after"\nid: X-1\n---\nbody\n'
    data, body = parse_frontmatter(text)
    assert data["note"] == "before --- after"
    assert body == "body\n"


def test_horizontal_rule_in_body_is_not_a_delimiter() -> None:
    raw, body = split_frontmatter("---\nid: X-1\n---\nabove\n---\nbelow\n")
    assert raw == "id: X-1"
    assert body == "above\n---\nbelow\n"


def test_missing_opening_delimiter_is_rejected() -> None:
    with pytest.raises(ValueError, match="must start with"):
        split_frontmatter("id: X-1\n---\n")


def test_missing_closing_delimiter_is_rejected() -> None:
    with pytest.raises(ValueError, match="closing"):
        split_frontmatter("---\nid: X-1\n")


def test_non_mapping_frontmatter_is_rejected() -> None:
    with pytest.raises(ValueError, match="mapping"):
        parse_frontmatter("---\n- just\n- a list\n---\nbody\n")


def test_crlf_documents_parse() -> None:
    data, body = parse_frontmatter("---\r\nid: X-1\r\n---\r\nbody\r\n")
    assert data == {"id": "X-1"}
    assert body == "body\r\n"
