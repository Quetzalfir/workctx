from __future__ import annotations

from pathlib import Path

import pytest

from workctx.secrets import DotenvParseError, SecretImportError, parse_dotenv, shred_dotenv

FIRST_VALUE = "fictional-first-import-material"
SECOND_VALUE = "fictional quoted import material"
THIRD_VALUE = "fictional#literal"


def test_dotenv_parser_handles_comments_quotes_escapes_and_name_normalization(
    tmp_path: Path,
) -> None:
    source = tmp_path / "fictional.env"
    source.write_text(
        "\n".join(
            (
                "# fictional input",
                f"FIRST_TOKEN={FIRST_VALUE}  # trailing comment",
                f'SECOND_TOKEN="{SECOND_VALUE}\\nnext" # quoted comment',
                f"THIRD_TOKEN='{THIRD_VALUE}'",
                "EMPTY_TOKEN=",
                "",
            )
        ),
        encoding="utf-8",
    )

    entries = parse_dotenv(source)

    assert [entry.ref.name for entry in entries] == [
        "first-token",
        "second-token",
        "third-token",
        "empty-token",
    ]
    assert entries[0].value.reveal() == FIRST_VALUE
    assert entries[1].value.reveal() == f"{SECOND_VALUE}\nnext"
    assert entries[2].value.reveal() == THIRD_VALUE
    assert entries[3].value.reveal() == ""
    assert all(entry.value.reveal() not in repr(entry) for entry in entries[:3])


@pytest.mark.parametrize(
    ("lines", "bad_line"),
    [
        (("FIRST_TOKEN=ok", "missing-assignment"), 2),
        (("FIRST_TOKEN=ok", 'BROKEN="unterminated'), 2),
        (("FIRST_TOKEN=ok", "BAD-NAME=value"), 2),
        (("SAME_NAME=one", "same_name=two"), 2),
        (("FIRST_TOKEN=ok", "SECOND__TOKEN=value"), 2),
        (("FIRST_TOKEN=ok", 'SECOND_TOKEN="bad\\q"'), 2),
    ],
)
def test_malformed_dotenv_reports_line_number_only(
    tmp_path: Path,
    lines: tuple[str, ...],
    bad_line: int,
) -> None:
    source = tmp_path / "malformed.env"
    source.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(DotenvParseError) as caught:
        parse_dotenv(source)

    assert caught.value.line_number == bad_line
    assert str(bad_line) in str(caught.value)
    assert lines[bad_line - 1] not in str(caught.value)


def test_dotenv_reader_rejects_missing_source_without_echoing_path(tmp_path: Path) -> None:
    source = tmp_path / "fictional-sensitive-looking-name.env"

    with pytest.raises(SecretImportError) as caught:
        parse_dotenv(source)

    assert source.name not in str(caught.value)


def test_shred_dotenv_truncates_and_removes_regular_file(tmp_path: Path) -> None:
    source = tmp_path / "remove.env"
    source.write_text(f"TOKEN={FIRST_VALUE}\n", encoding="utf-8")

    shred_dotenv(source)

    assert not source.exists()
