# Brief: WP-620 — Secret references (C-211 + dev-ergonomics addendum)

Codex worker, worktree `.worktrees/WP-620`, branch `agent/WP-620-secrets`. You
cannot commit; leave changes uncommitted. Final message = report. `.agents/` is
read-only for you — read `.agents/status/phase2-candidates.md` (C-211 + its
addendum) and decision D-043 in the decision register first.

## Non-negotiable invariant

Secret VALUES never touch the workspace, source control, envelopes, logs,
diagnostics, reports, or exception messages. Every code path you write must be
provably value-free outside the resolver return itself.

## Scope

1. New package `src/workctx/secrets/`:
   - `SecretRef` name grammar: lowercase kebab, 1-64 chars, validated;
   - resolver chain: env var `WORKCTX_SECRET_<UPPER_SNAKE_NAME>` first, then
     the OS store via `keyring` (service namespace `workctx`, per-context
     scoping DELIBERATELY absent in v1 — names are machine-global; document
     this); missing -> typed `SecretNotFoundError` naming only the ref;
   - `resolve(name) -> SecretValue`: an opaque wrapper whose `__repr__`,
     `__str__`, and pydantic/json serialization NEVER emit the value
     (`SecretValue.reveal()` is the single accessor);
   - `store(name, value)`, `delete(name)`, `exists(name)`, `list_names()`
     (keyring cannot enumerate -> maintain a names-only index file in the
     platformdirs user directory; names only, never values).
2. CLI group `secret` in `src/workctx/cli.py` (envelope-first, lazy import):
   - `secret set <name>`: interactive masked prompt (typer hide_input) for
     the value; refuses a value argument on argv; `--from-env VAR` reads an
     environment variable instead (for scripting);
   - `secret check <name>`: resolvable? which layer (env/os-store)? exit 1
     with `SECRET_NOT_FOUND` when missing — value never printed;
   - `secret list`: names + backend presence only;
   - `secret unset <name>`;
   - `secret import <path>`: parse a dotenv-format file (NAME=value, comments,
     quoted values; implement minimal parsing, no new dependency), store every
     pair, report count + names, then offer deletion of the file — under
     `--json` require an explicit `--shred/--keep` flag instead of prompting.
     The file path may be outside the context; NEVER copy it anywhere.
3. Canonical `secret_ref` convention: document (docs/reference/secrets.md)
   how entities/config reference secrets by name. No engine change needed —
   validation already refuses secret-shaped values; verify it does NOT flag
   bare ref names and add a regression test proving a `secret_ref:` line
   passes validation while an inline value still quarantines/refuses.
4. Draft ADR content for the lead (in your REPORT, not in docs/adr/):
   resolver chain, opaque-value type, names-index trade-off, keyring-absent
   behavior (env-only mode with a clear diagnostic).

## Do NOT touch

Anything outside: `src/workctx/secrets/**`, `src/workctx/cli.py` (secret
group + lazy import), `tests/secrets/**`, `docs/reference/secrets.md`,
`docs/reference/cli-envelope.md` (rows). pyproject is frozen (keyring is
already a dependency). No changes to validation engine, ingestion, or MCP.

## Tests required

Resolver chain order; env fallback without keyring (monkeypatch keyring
import failure); SecretValue never leaks via repr/str/json/pickle-block;
masked `set` (CliRunner input simulation); `check`/`list`/`unset` envelopes;
dotenv import including quoted/comment/malformed lines (malformed = refuse
whole file, name the LINE NUMBER only, never content); the validation
regression pair from scope item 3. All tests must use FICTIONAL values and a
monkeypatched in-memory keyring backend — never touch the real OS store in
tests. Full gate: ruff check, ruff format --check, mypy src, pytest; declare
sandbox limitations explicitly.
