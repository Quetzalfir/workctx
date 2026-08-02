# Worker report: `WP-330-mcp-server`

## Status

`completed`

## Summary

Implemented the exact ADR 0012 alpha MCP surface over official Python SDK stdio: 11
read tools, 6 structurally and runtime approval-gated mutation tools, engine-backed
operations, the contracted WP-310/WP-420 `NOT-IMPLEMENTED` placeholders, read-only
canonical resources, strict context/path isolation, safe versioned envelopes, lazy
optional-dependency loading, the `workctx mcp serve` CLI entry point, CI extra
enablement, reference documentation, and 108 focused MCP tests. The final repository
gate passes with 1,046 tests.

## Base and final commits

- Base: `925aa08c72eb5fa442750ee3d8740636d03b751c`
- Final implementation: `1bbb7f5cbc0072050f15326f263df0b77c16ffee`

## Files changed

- `.github/workflows/ci.yml`
- `docs/reference/mcp.md`
- `src/workctx/cli.py`
- `src/workctx/mcp/__init__.py`
- `src/workctx/mcp/application.py`
- `src/workctx/mcp/contracts.py`
- `src/workctx/mcp/models.py`
- `src/workctx/mcp/resources.py`
- `src/workctx/mcp/runner.py`
- `src/workctx/mcp/serialization.py`
- `src/workctx/mcp/server.py`
- `tests/mcp/__init__.py`
- `tests/mcp/support.py`
- `tests/mcp/test_cli.py`
- `tests/mcp/test_contracts.py`
- `tests/mcp/test_resources.py`
- `tests/mcp/test_sdk_integration.py`
- `tests/mcp/test_tools.py`
- `.agents/work-orders/WP-330-mcp-server/report.md`
- `.agents/work-orders/WP-330-mcp-server/report.json`

## Behavior implemented

- Registers exactly the ADR 0012 17-tool surface with hand-maintained Draft 2020-12
  input/output schemas and `schema_version: 1` on every contract and response.
- Delegates live reads and local mutations to the public context, validation,
  projection, retrieval, and transaction APIs; task queries are live through the
  projection engine.
- Keeps `inbox_list`, `artifact_register`, and `draft_save` discoverable with structured
  `NOT-IMPLEMENTED` responses while their backing engines are unavailable.
- Requires literal `approved: true` in every mutation schema and rechecks it before
  dispatch at runtime.
- Verifies the bound context before each known tool execution and rejects foreign
  `workctx://` URIs, absolute/file paths, traversal, unsafe projected resource paths,
  symlinks, and junctions.
- Maps engine and boundary failures into versioned diagnostic envelopes using CLI
  codes/categories, with generic non-leaking handling for unexpected exceptions.
- Recursively sanitizes results and resources, including bearer/private-key/token
  patterns and normalized hyphenated, camelCase, compact, or prefixed secret keys and
  assignment text.
- Exposes read-only context configuration and canonical-entity frontmatter resources;
  document bodies and arbitrary filesystem paths are not exposed, and no MCP prompts
  are registered.
- Adds a lazy `workctx mcp serve [--context]` stdio entry point. A missing or incomplete
  optional SDK fails with the clear unavailable-dependency diagnostic while other CLI
  commands remain importable and usable.
- Enables the existing `mcp` optional extra in the six-platform/version CI quality
  matrix and documents installation, client configuration, versioning, diagnostics,
  resources, and security behavior.

## Validation executed

| Command | Result | Notes |
| --- | --- | --- |
| `$env:UV_CACHE_DIR=(Join-Path $pwd '.uv-cache'); uv sync --locked --extra mcp` | passed | Installed the locked official `mcp==2.0.0` SDK and dependencies. |
| `.venv\Scripts\pytest.exe -q -p no:cacheprovider tests\mcp` | passed | `108 passed in 40.23s` after the independent-review fixes. |
| `uv run ruff check .` | passed | `All checks passed!` |
| `uv run ruff format --check .` | passed | `335 files already formatted` |
| `uv run mypy src` | passed | `Success: no issues found in 72 source files` |
| `uv run pytest` | passed | `1046 passed in 312.91s (0:05:12)`; includes the official-SDK Windows stdio lifecycle test. |
| `uv run pytest tests/test_plan_contracts.py -q` | passed | `4 passed in 0.08s` after report creation. |
| `git diff --cached --check` | passed | No whitespace errors before the implementation commit. |
| `uv run python -c "import json; from pathlib import Path; from jsonschema import Draft202012Validator; root = Path('.'); schema = json.loads((root / '.agents/plan/initial/agent-report.schema.json').read_text(encoding='utf-8')); report = json.loads((root / '.agents/work-orders/WP-330-mcp-server/report.json').read_text(encoding='utf-8')); Draft202012Validator(schema).validate(report); print('WP-330 report.json validates against agent-report.schema.json')"` | passed | `WP-330 report.json validates against agent-report.schema.json` |

Two earlier `uv run pytest` attempts were terminated by outer command limits at 120
and 300 seconds without pytest assertion output. The same exact command subsequently
completed twice (`1046 passed` in 305.19 seconds before final review and 312.91 seconds
after the fixes); only the final reviewed run is acceptance evidence.

## Assumptions and decisions

- The operator-pinned base commit `925aa08c72eb5fa442750ee3d8740636d03b751c`
  supersedes the frozen contract's administrative `PENDING-FINAL-PIN` placeholder.
- WP-310 ingestion APIs and WP-420 draft persistence are absent at the pinned base, so
  their three tools use the ADR-required structured placeholders. WP-400 task
  enrichment is not required for the live projection-backed task tools.
- The context configuration resource uses
  `workctx://<context>/context/configuration`; canonical entity resources retain their
  standard `workctx://<context>/<entity_type>/<entity_id>` URIs.
- `pyproject.toml` and `uv.lock` already contained the correct optional MCP extra and
  locked SDK, so the narrow compliant dependency change is CI `--extra mcp` wiring
  only; no lockfile edit was needed or allowed.
- The low-level SDK advertises but does not enforce tool input schemas, so runtime uses
  the same hand-maintained schema dictionaries through a deterministic validator.
- Canonical resource reads compose the public projection resolver and `CanonicalStore`
  path boundary, then validate typed frontmatter before serialization.
- Pytest's required `tests/mcp/__init__.py` shadows the installed SDK under prepend
  import mode; the lifecycle module temporarily and restorably removes that test path
  only while resolving the optional SDK.

## Contract deviations

- The frozen work-order contract still says `base_commit: PENDING-FINAL-PIN`; it was not
  modified because the user supplied the final pin and the contract is outside the
  worker's writable grant.
- The named worktree/branch did not exist at session start and was created from the
  operator-pinned base before implementation.
- No objective, architecture, tool-surface, allowed-path, or dependency deviation was
  made.

## Security and migration considerations

- All operations remain bound to one local context. There are no external writes,
  network transports, prompts, raw file-edit tools, or secret persistence paths.
- Approval, context, traversal, symlink/junction, sanitization, unexpected-exception,
  and cross-context resource/tool denials have explicit regression tests.
- Canonical resources expose sanitized validated frontmatter only and label canonical
  entity payloads as untrusted data.
- This is an additive schema-version-1 adapter and optional dependency activation; it
  changes no canonical workspace schema and requires no data migration.
- Projection rebuild remains disposable derived-state mutation; canonical transaction
  apply retains the existing atomic, locked, audited engine behavior.

## Unresolved issues

- Replace the WP-310 placeholders when ingestion APIs integrate.
- Replace `draft_save` when WP-420 outbox persistence integrates; enrich task output
  when WP-400 lands without changing the frozen tool names.

## Recommended next action

Implementation lead: inspect the base-to-final diff, validate this report, rerun the
four-command gate independently, and integrate `1bbb7f5cbc0072050f15326f263df0b77c16ffee`;
then coordinate live WP-310/WP-400/WP-420 wiring behind the unchanged ADR 0012 surface.
