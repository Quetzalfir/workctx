import pytest
from pydantic import ValidationError

from workctx.domain.locators import parse_source_locator


@pytest.mark.parametrize(
    "value",
    (
        {"type": "line_range", "start_line": 4, "end_line": 3},
        {"type": "page_range", "start_page": 4, "end_page": 3},
        {"type": "time_range", "start_ms": 4, "end_ms": 3},
        {
            "type": "repo_range",
            "repo_id": "fictional-repo",
            "commit": "abcdef1",
            "path": "src/example.py",
            "start_line": 4,
            "end_line": 3,
        },
    ),
)
def test_range_locators_reject_reversed_bounds(value: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="greater than or equal"):
        parse_source_locator(value)


@pytest.mark.parametrize(
    "path",
    ("/etc/passwd", "C:/temp/file.txt", "C:\\temp\\file.txt", "../secret.txt"),
)
def test_repository_locator_rejects_absolute_or_traversing_paths(path: str) -> None:
    with pytest.raises(ValidationError, match="path"):
        parse_source_locator(
            {
                "type": "repo_range",
                "repo_id": "fictional-repo",
                "commit": "abcdef1",
                "path": path,
                "start_line": 1,
                "end_line": 2,
            }
        )


def test_locator_numbers_do_not_coerce_strings() -> None:
    with pytest.raises(ValidationError):
        parse_source_locator({"type": "line_range", "start_line": "1", "end_line": 2})


@pytest.mark.parametrize("pointer", ("status", "/bad~escape", "#/fragment-form"))
def test_json_pointer_locator_rejects_non_rfc_pointer_syntax(pointer: str) -> None:
    with pytest.raises(ValidationError, match="JSON pointer"):
        parse_source_locator({"type": "json_pointer", "pointer": pointer})
