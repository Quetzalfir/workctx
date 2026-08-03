# Wave 5 — Release wave (WP-500 split)

Opened 2026-08-02 after Wave 4 close. Operator approved starting Wave 5 without a
checkpoint (D-040 continuous pipeline). WP-500 from
`.agents/plan/initial/06-implementation-work-packages.md` is split into three
disjoint packages so workers never share writable paths.

## Packages

| Package | Scope | Worker | Launch |
| --- | --- | --- | --- |
| WP-510 | `workctx migrate legacy` engine + CLI + fictional legacy fixture | Codex (max effort) | immediately |
| WP-520 | Automated acceptance scenarios for the full alpha cycle (`tests/e2e/`) | Codex (max effort) | immediately (disjoint from WP-510) |
| WP-530 | Public docs completion, security/privacy guidance, release notes, packaging verification | Claude agent | after WP-510 integrates (docs must describe integrated truth, not in-flight work) |

## Path ownership (disjoint)

- WP-510: `src/workctx/migration/**`, `tests/migration/**`, `src/workctx/cli.py`
  (migrate group only), `docs/reference/migration.md`, `docs/reference/cli-envelope.md`
  (command table row only).
- WP-520: `tests/e2e/**` only. Product defects found = blocker reports, never src edits.
- WP-530: `README.md`, `CHANGELOG.md`, `ROADMAP.md`, `docs/**` except the two files
  owned by WP-510; `pyproject.toml` version stamp if the lead confirms it.

## Wave-close criteria (doc-12 definition of done)

1. Acceptance scenarios pass on all three OSes (CI matrix `[full-ci]`).
2. A sanitized fictional legacy fixture migrates end-to-end with a loss report and
   the source tree untouched.
3. `uv build` artifacts install into a clean venv and `workctx version` works.
4. Public docs carry no claims about unimplemented behavior.
5. Release notes + known limitations exist. Tagging/publishing the release is an
   operator decision, not part of the wave.

## Known decision points reserved for the lead/operator

- Whether migration apply-mode commits through the transaction engine (ledger
  coverage) or stages + validates + rebuilds with a single import event. Worker
  must propose, not decide.
- Final version string (`0.1.0a1` per PEP 440 for the "0.1.0-alpha" target).
- Publishing anything (PyPI, GitHub release) — operator-only.

## Notes carried from Wave 4

- `inbox add` out-of-zone path error message is rough ("Inbox artifact metadata is
  invalid") — lead fixes inline during this wave or folds into WP-520 blocker flow.
- Incremental projection updates remain explicitly post-alpha (documented).
