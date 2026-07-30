# Work-order context: WP-120-cli-envelope

## Why this exists

Doc-04 defines a strict output contract and exit-code table; the scaffold hand-rolls a
different ad-hoc JSON shape in each command, prints Rich error markup to stdout even in
JSON mode, and only uses exit codes 0/1. Every later package registers commands through
this framework, so the envelope must land before Wave 2.

## Required architecture and decisions

- `.agents/plan/initial/04-cli-specification.md` — envelope, exit codes, mutation UX.
- ADR 0008 — the envelope gets a hand-maintained schema plus fixtures.
- D-012 (`.agents/status/decision-register.md`): the top-level `validate` alias stays
  (README and quickstart document it); public CLI docs own its documentation, the plan
  file remains historical.
- D-013: context-resolution step 3 (user registry) is deferred to WP-200; you provide the
  seam, not the implementation.
- Lead-decided exit-code mapping is in the contract scope — do not re-litigate per command.

## Existing implementation

- `src/workctx/cli.py` — Typer app; version/doctor/validate alias + context sub-app.
  Partial ad-hoc envelopes: `context validate --json` {ok, command, context_id, root,
  issues[]}; `doctor --json` {ok, command, result:[checks]}; `context inspect --json` raw
  dump; `context init` has no JSON mode. Errors print `[red]Error:[/red]` to stdout.
- `src/workctx/doctor.py` — presentation-free data (DoctorCheck dataclass); only its
  serialization site moves.
- `src/workctx/errors.py` — WorkctxError hierarchy (yours to extend additively; do not
  modify existing classes, services import them).
- `src/workctx/validation/workspace.py` — ValidationReport already exposes .ok/.errors/
  .warnings; mapping to envelope warnings/errors arrays is mechanical (frozen file — you
  consume it).
- `tests/test_cli.py` — 3 tests pinning the pre-envelope shapes; they are expected to be
  rewritten by this order. The doctor test monkeypatches `workctx.cli.run_doctor` by
  import site.
- Rich `Console()` is created once at module import writing to stdout; you need a second
  stderr console or a writer abstraction.

## Dependencies

- WP-001 baseline only. WP-100/WP-110 run in parallel and never touch your files; you never
  touch theirs. Consume `services/contexts.py`, `validation/workspace.py`, and model enums
  strictly through their frozen public interfaces.

## Known risks and edge cases

- Click owns exit code 2 for usage errors — preserve it; do not shadow it with your
  mapping. The 10 band requires a top-level exception boundary around `app()` instead of
  framework defaults.
- Click 8 `CliRunner` merges stderr into stdout by default — envelope tests must split
  streams (`mix_stderr=False` or equivalent) or purity assertions are meaningless.
- Typer option types currently use ContextKind/ContextProfile enums from models — keep
  consuming them; do not redefine.
- `context inspect` prints resolved root in human mode today; doc-04's "print resolved
  context for mutation" applies to init — add it there without breaking the human output
  tests of other commands.
- Exception sanitization: `InvalidContextError` messages can embed YAML parser output —
  ensure envelope errors stay single-line, structured, and secret-free.
- New test directories (`tests/cli/`) need an `__init__.py`: pytest's default import mode
  collides on duplicate basenames against the flat `tests/test_*.py` files otherwise.
