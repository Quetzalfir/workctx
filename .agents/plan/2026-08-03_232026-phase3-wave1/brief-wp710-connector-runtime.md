# Brief: WP-710 — Generic declarative connector runtime (C-214)

Codex worker, worktree `.worktrees/WP-710`, branch `agent/WP-710-connectors`.
You cannot commit; leave changes uncommitted. Final message = report.
`.agents/` read-only — read C-214 in `.agents/status/phase2-candidates.md`,
D-047/D-048 in the decision register, and ADR 0013 FIRST. httpx is already a
dependency (pyproject frozen for you).

## Manifest (declarative, operator-authored, canonical)

Location: `07_connectors/<name>.yaml` inside the context (user-owned config;
if zone validation rejects the directory, STOP and report the exact rule).
Hand-maintained JSON Schema `schemas/connector-manifest.schema.json` +
positive/negative fixtures (ADR 0008):

- `schema_version: 1`, `name` (kebab, unique per context), `base_url`
  (https required; plain http ONLY with an explicit `allow_insecure_http:
  true`), optional `secret_ref` (ADR 0013 name grammar) + `auth_style`
  (bearer | header:<Name> | query:<param>),
- `snapshots`: list of {`id` (kebab), `path`, optional `query` mapping,
  optional `accept` (default application/json), optional `schedule`
  (recorded metadata only in v1 — no daemon)},
- global `timeout_seconds` (default 30, max 300) and `max_bytes`
  (default 10 MiB, max 100 MiB).

## Engine (`src/workctx/connectors/`)

- `load_manifests(root)` -> validated manifests; duplicate names refused.
- `sync(root, name, *, snapshot_id=None, transport=None, clock=None)`:
  1. resolve `secret_ref` at call time (workctx.secrets); inject per
     auth_style; the SecretValue is revealed ONLY into the request object —
     never logged, never stored, never in errors or the result;
  2. GET each selected snapshot endpoint via httpx with the manifest
     timeout, no redirects across hosts (same-host redirects <= 3), and a
     hard max_bytes cap (stream and abort past the cap with a typed error);
  3. write the response body VERBATIM as
     `00_inbox/raw/<name>-<snapshot_id>-<UTC timestamp>.<ext>` (ext from
     content type: json/xml/txt/bin allowlist; anything else -> .bin) plus a
     sidecar provenance mapping passed into registration metadata: system
     name, base_url WITHOUT credentials, path, query WITH secret-bearing
     params replaced by the ref name, HTTP status, response content type,
     byte count, retrieved_at;
  4. register through the EXISTING batch ingestion API (source_type
     `connector_snapshot` if the vocabulary allows a source_origin string —
     inspect RegisterRequest; if a new enum value is required, STOP and
     report the blocker) — quarantine and duplicate outcomes are normal
     per-file results, never engine errors;
  5. return a typed SyncResult (per-snapshot outcome, artifact ids/refs,
     bytes, duration) that is envelope-serializable later.
- Failures are typed and content-free: connection/timeout/status/size each
  get a diagnostic naming the connector, snapshot id, and status code only —
  never response bodies, never header values.
- Responses are UNTRUSTED DATA end to end: no parsing beyond content-type
  sniffing for the extension; the evidence pipeline owns interpretation.

## Do NOT touch

Anything outside: `src/workctx/connectors/**`, `tests/connectors/**`,
`schemas/connector-manifest.schema.json` + its fixtures,
`docs/reference/connectors.md`. NO cli.py (deferred to the lead), no
scheduling daemon, no MCP tools, no new dependencies.

## Tests required

Manifest schema fixtures; https/insecure-http gating; auth injection per
style with a mocked httpx transport (fictional token via in-memory secrets
backend) proving the value reaches ONLY the request and appears nowhere in
results/errors/repr; size-cap abort; cross-host redirect refusal; snapshot
bytes verbatim + provenance sidecar content; registration outcomes incl. a
prompt-injection response body quarantining normally; duplicate re-sync
dedup; deterministic naming with injected clock. Fictional hosts only
(example.test). Full gate; declare sandbox limits explicitly.
