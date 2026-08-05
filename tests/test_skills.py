from __future__ import annotations

import json
import re
import shlex
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import get_args, get_type_hints
from urllib.parse import unquote, urlsplit

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from markdown_it import MarkdownIt
from markdown_it.token import Token
from typer import Typer
from typer.models import OptionInfo

from workctx.cli import app

ROOT = Path(__file__).parents[1]
SKILLS_ROOT = ROOT / ".agents" / "skills"
REGISTRY_PATH = SKILLS_ROOT / "registry.yaml"
EXPECTED_SKILL_IDS = frozenset(
    {
        "bootstrap-session",
        "capture-browser-evidence",
        "close-session",
        "create-work-order",
        "curate-knowledge",
        "draft-replies",
        "investigate-system",
        "lead-implementation",
        "manage-tasks",
        "migrate-legacy-context",
        "process-evidence",
        "review-work-order",
        "trace-context",
        "validate-context",
    }
)
REQUIRED_SKILL_SECTIONS = (
    "Purpose and trigger",
    "Required inputs",
    "Read dependencies",
    "Procedure",
    "Side effects and approval boundary",
    "Invariants",
    "Stop conditions",
    "Durable outputs",
    "Validation and success criteria",
    "Human-facing response",
)

_ABSOLUTE_PATH_PATTERNS = (
    ("Windows drive path", re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")),
    (
        "UNC path",
        re.compile(r"(?<!\\)\\\\[A-Za-z0-9.$_-]+[\\/][^\s`\"')]+"),
    ),
    (
        "forward-slash network path",
        re.compile(r"(?<![:/])//[A-Za-z0-9.$@_-]+/[^\s`\"')]+"),
    ),
    ("file URI", re.compile(r"\bfile:(?:/+|\\+)", re.IGNORECASE)),
    ("home-relative path", re.compile(r"(?<![A-Za-z0-9])~[\\/]")),
    (
        "single-segment POSIX path",
        re.compile(
            r"(?<![\w._:/-])/(?!/)[^\s/`\"'()<>{}\[\],.;:]+"
            r"(?=$|[\s`\"'()<>{}\[\],.;:])"
        ),
    ),
    (
        "POSIX absolute path",
        re.compile(
            r"(?<![\w._:/-])/(?!/)(?=[^\s`\"'()<>\[\]]*?/)"
            r"[^\s`\"'()<>\[\]]+"
        ),
    ),
)
_ALLOWED_SLASH_OPTIONS = frozenset({"/quiet"})
_HTML_CLOSING_TAG = re.compile(r"</[A-Za-z][A-Za-z0-9-]*\s*>")
_ASSIGNED_SECRET = re.compile(
    r"\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|private[_-]?key|password|passwd|secret|token)"
    r"\s*[:=]\s*(?P<value>'[^'\n]+'|\"[^\"\n]+\"|[^\s#;,]+)",
    re.IGNORECASE,
)
_TOKEN_PATTERNS = (
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    (
        "GitHub token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    ),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("secret key token", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}\b", re.IGNORECASE)),
)
_PLACEHOLDER_VALUE = re.compile(
    r"(?:"
    r"<(?:redacted|placeholder)>|"
    r"\$\{[A-Z][A-Z0-9_]*\}|"
    r"YOUR_[A-Z0-9_]+|"
    r"(?:EXAMPLE|PLACEHOLDER|REPLACE_ME|CHANGEME)(?:[_-][A-Z0-9]+)*|"
    r"REPLACE-WITH-[A-Z0-9_-]+"
    r")",
    re.IGNORECASE,
)
_REFERENCE_USAGE = re.compile(r"!?\[(?P<text>[^\]\n]+)\]\[(?P<label>[^\]\n]*)\]")
_WORKCTX_REFERENCE = re.compile(
    r"(?<![\w:/])workctx(?:[ \t]+(?:"
    r"(?!workctx\b)[a-z][a-z0-9-]*|"
    r"--?[a-z0-9][a-z0-9-]*(?:=[^\s`,;]+)?|"
    r"<[^>\n]+>"
    r"))+"
)
_NAMED_MCP_REFERENCES = (
    re.compile(r"\bmcp__[A-Za-z0-9_.-]+\b", re.IGNORECASE),
    re.compile(r"\bMCP[ \t]+tool[ \t]+`[^`\n]+`", re.IGNORECASE),
    re.compile(r"`[^`\n]+`[ \t]+MCP[ \t]+tool\b", re.IGNORECASE),
)
_AFFIRMATIVE_APPROVAL = re.compile(
    r"^(?:requires? explicit approval|explicit approval (?:is )?required)\b",
    re.IGNORECASE,
)
_MARKDOWN = MarkdownIt("commonmark")


@dataclass(frozen=True, slots=True)
class SkillDocument:
    metadata: dict[str, object]
    body: str
    text: str


@dataclass(frozen=True, slots=True)
class CommandSpec:
    path: tuple[str, ...]
    options: frozenset[str]
    is_group: bool


@dataclass(frozen=True, slots=True)
class ProductReference:
    kind: str
    value: str
    start: int
    end: int


def _skill_document(path: Path) -> SkillDocument:
    with path.open(encoding="utf-8", newline="") as stream:
        text = stream.read()
    lines = text.splitlines(keepends=True)
    assert lines and lines[0].rstrip("\r\n") == "---", f"Missing frontmatter: {path}"

    closing_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.rstrip("\r\n") == "---"),
        None,
    )
    assert closing_index is not None, f"Unterminated frontmatter: {path}"

    raw_frontmatter = "".join(lines[1:closing_index])
    parsed = yaml.safe_load(raw_frontmatter)
    assert isinstance(parsed, dict), f"Frontmatter must be a mapping: {path}"
    return SkillDocument(
        metadata=parsed,
        body="".join(lines[closing_index + 1 :]),
        text=text,
    )


def _callback_options(callback: Callable[..., object]) -> frozenset[str]:
    options = {"--help"}
    hints = get_type_hints(callback, include_extras=True)
    for parameter_name, annotation in hints.items():
        if parameter_name == "return":
            continue
        for metadata in get_args(annotation)[1:]:
            if not isinstance(metadata, OptionInfo):
                continue
            declarations = list(metadata.param_decls)
            if (
                not declarations
                and isinstance(metadata.default, str)
                and metadata.default.startswith("-")
            ):
                declarations.append(metadata.default)
            if not declarations:
                declarations.append("--" + parameter_name.replace("_", "-"))
            options.update(str(declaration) for declaration in declarations)
    return frozenset(options)


def _workctx_command_specs() -> dict[tuple[str, ...], CommandSpec]:
    specs: dict[tuple[str, ...], CommandSpec] = {}

    def visit(command_app: Typer, prefix: tuple[str, ...]) -> None:
        specs[prefix] = CommandSpec(path=prefix, options=frozenset({"--help"}), is_group=True)
        for command in command_app.registered_commands:
            name = command.name
            if name is None and command.callback is not None:
                name = command.callback.__name__.replace("_", "-")
            assert name is not None and command.callback is not None
            path = (*prefix, name)
            specs[path] = CommandSpec(
                path=path,
                options=_callback_options(command.callback),
                is_group=False,
            )
        for group in command_app.registered_groups:
            assert group.name is not None
            visit(group.typer_instance, (*prefix, group.name))

    visit(app, ())
    return specs


def _is_implemented_command(reference: str) -> bool:
    try:
        tokens = shlex.split(reference)
    except ValueError:
        return False
    if not tokens or tokens[0] != "workctx":
        return False

    command_tokens = tokens[1:]
    specs = _workctx_command_specs()
    candidates = [spec for path, spec in specs.items() if command_tokens[: len(path)] == list(path)]
    if not candidates:
        return False
    spec = max(candidates, key=lambda candidate: len(candidate.path))
    remaining = command_tokens[len(spec.path) :]
    if spec.is_group and any(not token.startswith("-") for token in remaining):
        return False
    for token in remaining:
        if not token.startswith("-"):
            continue
        option = token.split("=", maxsplit=1)[0]
        if option not in spec.options:
            return False
    return True


def _is_escaped(text: str, index: int) -> bool:
    backslashes = 0
    index -= 1
    while index >= 0 and text[index] == "\\":
        backslashes += 1
        index -= 1
    return backslashes % 2 == 1


def _mask_inline_code(text: str) -> str:
    characters = list(text)
    index = 0
    while index < len(text):
        if text[index] != "`" or _is_escaped(text, index):
            index += 1
            continue
        run_end = index
        while run_end < len(text) and text[run_end] == "`":
            run_end += 1
        delimiter = text[index:run_end]
        closing = text.find(delimiter, run_end)
        if closing == -1:
            index = run_end
            continue
        for masked_index in range(index, closing + len(delimiter)):
            characters[masked_index] = " "
        index = closing + len(delimiter)
    return "".join(characters)


def _walk_markdown_tokens(tokens: list[Token]) -> Iterator[Token]:
    for token in tokens:
        yield token
        if token.children:
            yield from _walk_markdown_tokens(token.children)


def _broken_internal_links(path: Path, repository_root: Path, text: str) -> list[str]:
    issues: list[str] = []
    resolved_root = repository_root.resolve()
    environment: dict[str, object] = {}
    tokens = _MARKDOWN.parse(text, environment)
    destinations: list[str] = []

    references = environment.get("references", {})
    reference_labels: set[str] = set()
    if isinstance(references, dict):
        reference_labels = {str(label).casefold() for label in references}
        for reference in references.values():
            if isinstance(reference, dict) and isinstance(reference.get("href"), str):
                destinations.append(reference["href"])

    for token in _walk_markdown_tokens(tokens):
        if token.type == "link_open":
            destination = token.attrGet("href")
            if destination is not None:
                destinations.append(destination)
        elif token.type == "image":
            destination = token.attrGet("src")
            if destination is not None:
                destinations.append(destination)
        elif token.type == "inline":
            inline_source = _mask_inline_code(token.content)
            for match in _REFERENCE_USAGE.finditer(inline_source):
                bracket_index = match.start() + int(inline_source[match.start()] == "!")
                if _is_escaped(inline_source, bracket_index):
                    continue
                raw_label = match.group("label") or match.group("text")
                label = re.sub(r"\s+", " ", raw_label.strip()).casefold()
                if label not in reference_labels:
                    issues.append(f"broken_reference_link: {raw_label}")

    for destination in destinations:
        parsed = urlsplit(destination)
        if parsed.scheme.casefold() == "file":
            issues.append(f"machine_specific_file_link: {destination}")
            continue
        if parsed.scheme or not parsed.path:
            continue
        relative_path = unquote(parsed.path)
        candidate = (path.parent / relative_path).resolve()
        if not candidate.is_relative_to(resolved_root):
            issues.append(f"internal_link_escapes_repository: {destination}")
        elif not candidate.exists():
            issues.append(f"broken_internal_link: {destination}")
    return issues


def _line_product_references(line: str) -> list[ProductReference]:
    candidates = [
        ProductReference("command", match.group(0), match.start(), match.end())
        for match in _WORKCTX_REFERENCE.finditer(line)
    ]
    for pattern in _NAMED_MCP_REFERENCES:
        candidates.extend(
            ProductReference("mcp", match.group(0), match.start(), match.end())
            for match in pattern.finditer(line)
        )
    references: list[ProductReference] = []
    for candidate in sorted(
        candidates,
        key=lambda reference: (reference.start, -(reference.end - reference.start)),
    ):
        if references and candidate.start < references[-1].end:
            continue
        references.append(candidate)
    return references


def _product_reference_issues(text: str) -> list[str]:
    issues: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        references = _line_product_references(line)
        for index, reference in enumerate(references):
            next_start = references[index + 1].start if index + 1 < len(references) else len(line)
            marker_scope = line[reference.end : next_start]
            if "(planned)" in marker_scope.casefold():
                continue
            if reference.kind == "command" and not _is_implemented_command(reference.value):
                issues.append(f"unimplemented_product_command:{line_number}: {reference.value}")
            elif reference.kind == "mcp":
                issues.append(f"unimplemented_mcp_tool:{line_number}: {reference.value}")
    return issues


def _skill_lint_issues(path: Path, repository_root: Path) -> list[str]:
    document = _skill_document(path)
    issues: list[str] = []
    path_scan_text = _HTML_CLOSING_TAG.sub(lambda match: " " * len(match.group(0)), document.text)
    for label, pattern in _ABSOLUTE_PATH_PATTERNS:
        for match in pattern.finditer(path_scan_text):
            if (
                label == "single-segment POSIX path"
                and match.group(0).casefold() in _ALLOWED_SLASH_OPTIONS
            ):
                continue
            issues.append(f"machine_specific_absolute_path: {label}: {match.group(0)}")
    for match in _ASSIGNED_SECRET.finditer(document.text):
        value = match.group("value").strip("'\"")
        if len(value) >= 12 and _PLACEHOLDER_VALUE.fullmatch(value) is None:
            issues.append("secret_like_value: assigned credential")
    for label, pattern in _TOKEN_PATTERNS:
        for _match in pattern.finditer(document.text):
            issues.append(f"secret_like_value: {label}")
    issues.extend(_broken_internal_links(path, repository_root, document.text))
    issues.extend(_product_reference_issues(document.text))
    return issues


def _registry_issues(skill_names: set[str], registry_entries: list[dict[str, object]]) -> list[str]:
    issues: list[str] = []
    registry_ids = [str(entry.get("id", "")) for entry in registry_entries]
    duplicate_ids = sorted(
        {skill_id for skill_id in registry_ids if registry_ids.count(skill_id) > 1}
    )
    if duplicate_ids:
        issues.append(f"Duplicate registry IDs: {', '.join(duplicate_ids)}")

    registry_names = set(registry_ids)
    missing = sorted(skill_names - registry_names)
    orphaned = sorted(registry_names - skill_names)
    if missing:
        issues.append(f"Unclassified skills: {', '.join(missing)}")
    if orphaned:
        issues.append(f"Orphan registry entries: {', '.join(orphaned)}")

    for entry in registry_entries:
        if entry.get("side_effect_class") != "external_write":
            continue
        notes = entry.get("notes")
        if not isinstance(notes, str) or _AFFIRMATIVE_APPROVAL.search(notes) is None:
            issues.append(f"External write lacks approval boundary: {entry.get('id', '')}")
    return issues


def _write_fixture_skill(root: Path, body: str) -> Path:
    path = root / "skills" / "fixture-skill" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\n"
        "name: fixture-skill\n"
        'description: "Use when exercising portable skill lint behavior in an isolated fixture."\n'
        "---\n\n"
        "# Fixture skill\n\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return path


def _valid_manifest(adapter: str = "claude") -> dict[str, object]:
    return {
        "schema_version": 1,
        "adapter": adapter,
        "adapter_version": 1,
        "scope": "project",
        "generated_at": "2026-07-30T20:00:00Z",
        "registry": {
            "path": ".agents/skills/registry.yaml",
            "content_hash": "sha256:" + "0" * 64,
        },
        "skills": [
            {
                "name": "bootstrap-session",
                "canonical": {
                    "path": ".agents/skills/bootstrap-session/SKILL.md",
                    "content_hash": "sha256:" + "1" * 64,
                },
                "generated": [
                    {
                        "path": f".{adapter}/skills/bootstrap-session/SKILL.md",
                        "content_hash": "sha256:" + "2" * 64,
                    }
                ],
            }
        ],
    }


def _manifest_schema() -> dict[str, object]:
    return json.loads(
        (ROOT / "schemas" / "skill-adapter-manifest.schema.json").read_text(encoding="utf-8")
    )


def _manifest_validator() -> Draft202012Validator:
    return Draft202012Validator(_manifest_schema(), format_checker=FormatChecker())


def test_canonical_skill_frontmatter_is_valid_and_unique() -> None:
    schema = json.loads(
        (ROOT / "schemas" / "skill-frontmatter.schema.json").read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema)
    names: set[str] = set()

    skill_paths = sorted(SKILLS_ROOT.glob("*/SKILL.md"))
    assert skill_paths

    for path in skill_paths:
        metadata = _skill_document(path).metadata
        validator.validate(metadata)
        name = str(metadata["name"])
        assert name == path.parent.name
        assert name not in names
        names.add(name)
    assert names == EXPECTED_SKILL_IDS


def test_canonical_skills_pass_portability_lint() -> None:
    issues = {
        path.relative_to(ROOT).as_posix(): found
        for path in sorted(SKILLS_ROOT.glob("*/SKILL.md"))
        if (found := _skill_lint_issues(path, ROOT))
    }
    assert not issues


def test_canonical_skills_follow_uniform_body_contract() -> None:
    for path in sorted(SKILLS_ROOT.glob("*/SKILL.md")):
        headings = [
            line.removeprefix("## ")
            for line in _skill_document(path).body.splitlines()
            if line.startswith("## ")
        ]
        assert headings == list(REQUIRED_SKILL_SECTIONS), path


def test_registry_is_valid_complete_and_unique() -> None:
    schema = json.loads(
        (ROOT / "schemas" / "skill-registry.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    registry = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(registry)
    assert isinstance(registry, dict)
    entries = registry["skills"]
    assert isinstance(entries, list)

    skill_names = {path.parent.name for path in SKILLS_ROOT.glob("*/SKILL.md")}
    assert skill_names == EXPECTED_SKILL_IDS
    assert not _registry_issues(skill_names, entries)


def test_frontmatter_parser_preserves_delimiter_like_values_and_body(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    path.write_text(
        "---\n"
        "name: delimiter-demo\n"
        'description: "Use when content includes \\n---\\nand remains data during parsing."\n'
        "---\n\n"
        "# Delimiter demo\n\n"
        "Before.\n"
        "---\n"
        "After.\n",
        encoding="utf-8",
    )

    document = _skill_document(path)

    assert str(document.metadata["description"]).splitlines()[1] == "---"
    assert document.body.splitlines()[-3:] == ["Before.", "---", "After."]


def test_frontmatter_parser_preserves_indented_block_scalar_delimiter(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    path.write_bytes(
        b"---\r\n"
        b"name: delimiter-demo\r\n"
        b"description: >-\r\n"
        b"  Use when a block scalar contains\r\n"
        b"  ---\r\n"
        b"  and must remain YAML data.\r\n"
        b"---\r\n"
        b"# Delimiter demo\r\n"
    )

    document = _skill_document(path)

    assert "---" in str(document.metadata["description"])
    assert document.body == "# Delimiter demo\r\n"


@pytest.mark.parametrize(
    "body",
    [
        r"Read `C:\Users\alice\notes.md`.",
        "Read `/home/alice/notes.md`.",
        "Read `/@scope/pkg/notes.md`.",
        "Read `/tmp`.",
        "Read `/etc`.",
        "Read `/workspace`.",
        "Read `/data`.",
        "Read `/café`.",
        "Read `/数据`.",
        "Read </home/alice>.",
        r"Read `\\server\share\notes.md`.",
        "Read `//server/share/notes.md`.",
        "Read `file:///home/alice/notes.md`.",
        "Read `file:/home/alice/notes.md`.",
        "Read `~/private/notes.md`.",
    ],
)
def test_skill_lint_rejects_machine_specific_absolute_paths(tmp_path: Path, body: str) -> None:
    path = _write_fixture_skill(tmp_path, body)

    assert any(
        issue.startswith("machine_specific_absolute_path:")
        for issue in _skill_lint_issues(path, tmp_path)
    )


def test_skill_lint_allows_urls_uris_and_repository_relative_paths(tmp_path: Path) -> None:
    path = _write_fixture_skill(
        tmp_path,
        "Use https://example.test/docs, workctx://demo/task/TASK-1, `/quiet`, or "
        "`.agents/skills/`, or `文档/文件.md`.",
    )

    assert not _skill_lint_issues(path, tmp_path)


def test_skill_lint_allows_markdown_html_closing_tags(tmp_path: Path) -> None:
    path = _write_fixture_skill(tmp_path, "<details>\nSummary.\n</details>")

    assert not _skill_lint_issues(path, tmp_path)


@pytest.mark.parametrize(
    "body",
    [
        "Credential: api_key = " + "x" * 24,
        "Credential: password = 'correct horse battery staple'",
        "Credential: refresh_token = " + "r" * 24,
        "Credential: ghp_" + "a" * 32,
        "-----BEGIN " + "PRIVATE KEY-----",
    ],
)
def test_skill_lint_rejects_secret_like_values(tmp_path: Path, body: str) -> None:
    path = _write_fixture_skill(tmp_path, body)

    assert any(
        issue.startswith("secret_like_value:") for issue in _skill_lint_issues(path, tmp_path)
    )


def test_skill_lint_allows_secret_policy_words_and_placeholders(tmp_path: Path) -> None:
    path = _write_fixture_skill(
        tmp_path,
        "Never store secrets, tokens, or passwords. Use api_key = <redacted>, "
        "token = ${API_TOKEN}, or client_secret = YOUR_CLIENT_SECRET_HERE in examples.",
    )

    assert not _skill_lint_issues(path, tmp_path)


@pytest.mark.parametrize(
    "value",
    [
        "thisisarealexamplepassword",
        "prefix_your_actualsecretvalue",
        "abc<redacted>stillsecretxyz",
    ],
)
def test_skill_lint_does_not_allow_placeholder_substrings(tmp_path: Path, value: str) -> None:
    path = _write_fixture_skill(tmp_path, f"Credential: password = {value}")

    assert any(
        issue.startswith("secret_like_value:") for issue in _skill_lint_issues(path, tmp_path)
    )


def test_skill_lint_resolves_internal_links_relative_to_skill(tmp_path: Path) -> None:
    guide = tmp_path / "skills" / "guide file.md"
    nested_guide = tmp_path / "skills" / "guide_(v1).md"
    guide.parent.mkdir(parents=True)
    guide.write_text("# Guide\n", encoding="utf-8")
    nested_guide.write_text("# Nested guide\n", encoding="utf-8")
    path = _write_fixture_skill(
        tmp_path,
        "See [guide](<../guide%20file.md#guide>), "
        "[nested](../guide_(v1).md), [reference][guide-ref], [section](#local), and "
        "[external](https://example.test/docs).\n\n"
        "[guide-ref]: ../guide%20file.md#guide",
    )

    assert not _skill_lint_issues(path, tmp_path)


def test_skill_lint_ignores_markdown_links_inside_code(tmp_path: Path) -> None:
    path = _write_fixture_skill(
        tmp_path,
        "Inline `[missing](missing.md)` is an example.\n\n"
        "```text\n"
        "[also missing](also-missing.md)\n"
        "```",
    )

    assert not _skill_lint_issues(path, tmp_path)


def test_skill_lint_ignores_markdown_links_inside_indented_code(tmp_path: Path) -> None:
    path = _write_fixture_skill(tmp_path, "    [code example](missing.md)")

    assert not _skill_lint_issues(path, tmp_path)


def test_skill_lint_checks_indented_list_paragraph_links(tmp_path: Path) -> None:
    path = _write_fixture_skill(
        tmp_path,
        "- Item\n\n    See [real list link](missing.md).",
    )

    assert any(
        issue.startswith("broken_internal_link:") for issue in _skill_lint_issues(path, tmp_path)
    )


def test_skill_lint_ignores_indented_code_inside_list_item(tmp_path: Path) -> None:
    path = _write_fixture_skill(
        tmp_path,
        "- Item\n\n      [code example](missing.md)",
    )

    assert not _skill_lint_issues(path, tmp_path)


def test_skill_lint_rejects_broken_link_with_nested_label(tmp_path: Path) -> None:
    path = _write_fixture_skill(tmp_path, "See [outer [inner]](missing.md).")

    assert any(
        issue.startswith("broken_internal_link:") for issue in _skill_lint_issues(path, tmp_path)
    )


def test_skill_lint_does_not_treat_info_text_as_a_closing_fence(tmp_path: Path) -> None:
    path = _write_fixture_skill(
        tmp_path,
        "```text\n```not-a-close\n[code example](missing.md)\n```",
    )

    assert not _skill_lint_issues(path, tmp_path)


def test_skill_lint_rejects_undefined_reference_link(tmp_path: Path) -> None:
    path = _write_fixture_skill(tmp_path, "See [missing][undefined-reference].")

    assert any(
        issue.startswith("broken_reference_link:") for issue in _skill_lint_issues(path, tmp_path)
    )


@pytest.mark.parametrize(
    ("destination", "expected_code"),
    [
        ("missing.md", "broken_internal_link:"),
        ("../../../outside.md", "internal_link_escapes_repository:"),
    ],
)
def test_skill_lint_rejects_broken_or_escaping_links(
    tmp_path: Path, destination: str, expected_code: str
) -> None:
    path = _write_fixture_skill(tmp_path, f"See [unsafe]({destination}).")

    assert any(issue.startswith(expected_code) for issue in _skill_lint_issues(path, tmp_path))


@pytest.mark.parametrize(
    ("body", "expected_issue"),
    [
        ("Run `workctx version`.", False),
        ("Run `workctx context`.", False),
        ("Run `workctx context validate`.", False),
        ("Run `workctx context validate --json`.", False),
        ("Run `workctx context validate --repair`.", True),
        # `workctx secrets export` is the evergreen unimplemented fixture: the
        # product invariant forbidding stored secret values guarantees this
        # command can never become real, unlike past fixtures that went live.
        ("Run `workctx secrets export`.", True),
        ("Run `workctx secrets export` (planned).", False),
        ("Call MCP tool `context_lookup`.", True),
        ("Call MCP tool `context_lookup` (planned).", False),
        ("Call MCP tool `mcp__workctx__lookup`.", True),
        ("Call MCP tool `mcp__workctx__lookup` (planned).", False),
        ("Generic MCP tools may be available.", False),
        ("Resolve workctx://demo/task/TASK-1.", False),
    ],
)
def test_product_reference_lint_requires_planned_marker(
    tmp_path: Path, body: str, expected_issue: bool
) -> None:
    path = _write_fixture_skill(tmp_path, body)
    issues = _product_reference_issues(path.read_text(encoding="utf-8"))

    assert bool(issues) is expected_issue


def test_planned_marker_applies_only_to_the_preceding_product_reference() -> None:
    issues = _product_reference_issues(
        "Use `workctx version` (planned), then `workctx secrets export`."
    )

    assert len(issues) == 1
    assert "workctx secrets export" in issues[0]


@pytest.mark.parametrize(
    ("skill_names", "entries", "expected"),
    [
        (
            {"alpha-skill", "beta-skill"},
            [{"id": "alpha-skill", "side_effect_class": "read_only"}],
            "Unclassified skills: beta-skill",
        ),
        (
            {"alpha-skill"},
            [
                {"id": "alpha-skill", "side_effect_class": "read_only"},
                {"id": "orphan-skill", "side_effect_class": "read_only"},
            ],
            "Orphan registry entries: orphan-skill",
        ),
    ],
)
def test_registry_coverage_rejects_missing_and_orphan_entries(
    skill_names: set[str], entries: list[dict[str, object]], expected: str
) -> None:
    assert expected in _registry_issues(skill_names, entries)


def test_registry_rejects_duplicate_ids_and_unapproved_external_writes() -> None:
    entries: list[dict[str, object]] = [
        {"id": "alpha-skill", "side_effect_class": "read_only"},
        {"id": "alpha-skill", "side_effect_class": "external_write", "notes": "Sends data."},
    ]

    issues = _registry_issues({"alpha-skill"}, entries)

    assert "Duplicate registry IDs: alpha-skill" in issues
    assert "External write lacks approval boundary: alpha-skill" in issues


@pytest.mark.parametrize(
    "notes",
    [
        "Approval is required.",
        "No explicit approval is required.",
        "Explicitly bypass approval checks.",
    ],
)
def test_registry_schema_rejects_weak_or_negated_external_write_approval(notes: str) -> None:
    schema = json.loads(
        (ROOT / "schemas" / "skill-registry.schema.json").read_text(encoding="utf-8")
    )
    registry = {
        "schema_version": 1,
        "skills": [
            {
                "id": "send-reply",
                "side_effect_class": "external_write",
                "notes": notes,
            }
        ],
    }

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(registry)


def test_registry_schema_accepts_affirmative_external_write_approval() -> None:
    schema = json.loads(
        (ROOT / "schemas" / "skill-registry.schema.json").read_text(encoding="utf-8")
    )
    registry = {
        "schema_version": 1,
        "skills": [
            {
                "id": "send-reply",
                "side_effect_class": "external_write",
                "notes": "Requires explicit approval for the exact external action.",
            }
        ],
    }

    Draft202012Validator(schema).validate(registry)
    assert not _registry_issues({"send-reply"}, registry["skills"])


@pytest.mark.parametrize("adapter", ["codex", "claude", "gemini"])
def test_skill_adapter_manifest_schema_accepts_v1_contract(adapter: str) -> None:
    schema = _manifest_schema()
    Draft202012Validator.check_schema(schema)

    _manifest_validator().validate(_valid_manifest(adapter))


def test_documented_skill_adapter_manifest_example_matches_schema() -> None:
    document = (ROOT / "docs" / "reference" / "skill-adapters.md").read_text(encoding="utf-8")
    example = document.split("```json", maxsplit=1)[1].split("```", maxsplit=1)[0]

    _manifest_validator().validate(json.loads(example))


@pytest.mark.parametrize(
    ("adapter", "wrong_root"),
    [
        ("codex", "claude"),
        ("claude", "gemini"),
        ("gemini", "codex"),
    ],
)
def test_skill_adapter_manifest_schema_rejects_adapter_path_mismatch(
    adapter: str, wrong_root: str
) -> None:
    manifest = _valid_manifest(adapter)
    skills = manifest["skills"]
    assert isinstance(skills, list)
    skills[0]["generated"][0]["path"] = f".{wrong_root}/skills/bootstrap-session/SKILL.md"

    with pytest.raises(ValidationError):
        _manifest_validator().validate(manifest)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("generated_at", "definitely-not-a-timestamp"),
        ("generated_at", "2026-07-30T20:00:00+00:00"),
        ("schema_version", 2),
    ],
)
def test_skill_adapter_manifest_schema_rejects_invalid_timestamp_or_version(
    field: str, value: object
) -> None:
    manifest = _valid_manifest()
    manifest[field] = value

    with pytest.raises(ValidationError):
        _manifest_validator().validate(manifest)


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "C:/Users/alice/SKILL.md",
        "/home/alice/SKILL.md",
        "../.claude/skills/demo/SKILL.md",
        ".claude/../outside.md",
        r".claude\skills\demo\SKILL.md",
    ],
)
def test_skill_adapter_manifest_schema_rejects_unsafe_generated_paths(
    unsafe_path: str,
) -> None:
    manifest = _valid_manifest()
    skills = manifest["skills"]
    assert isinstance(skills, list)
    skills[0]["generated"][0]["path"] = unsafe_path

    with pytest.raises(ValidationError):
        _manifest_validator().validate(manifest)
