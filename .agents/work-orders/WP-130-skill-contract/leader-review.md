# Leader review: `WP-130-skill-contract`

## Decision

`accepted`

## Contract compliance

- Base commit matches (`ea6861f`); delivery `e377960`, report `64aa455` on
  `agent/WP-130-skill-contract`.
- Changed-path audit: 21 files, all inside `allowed_paths`.
- `schemas/skill-frontmatter.schema.json` verified untouched (0-line diff) — the frozen
  minimal portable contract holds; extra metadata lives in the registry as contracted.
- No `.claude/`, `.gemini/`, or `.codex/` adapter directories exist on the branch
  (verified via git ls-tree).

## Diff review

- `.agents/skills/registry.yaml`: 13/13 skills classified into doc-13 side-effect classes
  with sensible boundaries (draft-replies = local_proposal with explicit no-send note;
  investigate-system = external_read with scoped read-only note; lead-implementation =
  local_mutation with pushes/merges excluded). Registry validated by the lead against
  `schemas/skill-registry.schema.json`; completeness verified both directions (no
  unclassified skills, no orphans).
- Lint coverage in `tests/test_skills.py` (21 → 98 tests) covers every doc-13 item:
  absolute machine paths (with URL/repo-relative allowances), secret-like values (with
  placeholder handling), internal link resolution (including code-fence exclusions,
  reference links, nested labels, escaping links), robust frontmatter parsing (regression
  for delimiter-like values inside frontmatter — the real failure mode), registry
  completeness/duplicates, and product-reference planned markers.
- `docs/reference/skill-adapters.md` + `schemas/skill-adapter-manifest.schema.json`:
  deterministic manifest (sha256 content hash, adapter version, staleness semantics,
  unsafe-path rejection) — concrete enough for WP-320 to implement without new design
  decisions; the documented example is itself schema-validated in tests.
- Skill bodies normalized to the uniform doc-13 section contract; approval boundaries
  explicit; registry schema even rejects weak/negated external-write approval language.

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| report.json vs agent-report.schema.json | pass | validated by lead |
| registry.yaml vs skill-registry.schema.json + bidirectional completeness | pass | programmatic lead check, 13/13 |
| frozen-schema diff | pass | 0 lines |
| `uv run ruff check .` | pass | worker worktree, independent run |
| `uv run ruff format --check .` | pass | 143 files |
| `uv run mypy src` | pass | 14 source files, strict |
| `uv run pytest` | pass | 98 passed (baseline was 21) |

## Findings

- The worker's note about the branch's pre-activation `proposed`/`PENDING` contract copy
  is the known branch-point artifact (pin landed in `b1a006d`); master's contract is
  authoritative. No action needed.
- Language-separation lint remains a manual lead-review item per the recorded contract
  narrowing; verified manually: all skill bodies are English.

## Required revisions

None.

## Integration notes

- Integrated third per the Wave 1 order; WP-120 merges immediately after, followed by the
  final combined Wave 1 regression gate.
