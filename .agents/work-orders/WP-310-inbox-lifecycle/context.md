# Work-order context: WP-310-inbox-lifecycle

## Why this exists

Raw evidence retention and archive-only-after-successful-transaction are product
invariants (AGENTS.md); quarantine is the doc-08 prompt-injection control. This order
turns them into deterministic code between the WP-200 store and the WP-300 engine.

## Required architecture and decisions

- doc-08 "Prompt injection in evidence" controls: label untrusted, never execute,
  quarantine suspicious, deterministic validation.
- `schemas/artifact-manifest.schema.json` (WP-110): ART id family, sha256 content_hash,
  quarantine/duplicate statuses — your manifest contract, frozen for you.
- WP-300's apply result (see docs/reference/transactions.md when it lands) is your
  archive receipt: committed revision + ledger event referencing the artifact.

## Existing implementation

- Workspace zones exist in the template: 00_inbox/{raw,manifests,quarantine},
  01_processed. The WP-200 store is zone-aware.
- `ArtifactReference` (artifact://sha256/...) and `ArtifactManifest`/`ArtifactId` domain
  models are integrated.
- The validation engine already scans for secret patterns — reuse its published
  patterns for your quarantine heuristics rather than inventing new ones where they
  overlap; add injection markers (instruction-like patterns) per doc-08.

## Dependencies

- Starts after WP-300 integrates (your base commit includes it). WP-330 may run in
  parallel — fully disjoint paths; it will surface your APIs as MCP tools later via
  the lead's wiring.

## Known risks and edge cases

- Hashing large files: stream, do not slurp; document the size guard that routes
  oversized artifacts to quarantine.
- Windows sharing violations while moving raw files: use the WP-200 staged primitives
  or their retry pattern; never leave a half-moved original.
- Duplicate-by-hash vs same-name-different-content are different cases; the manifest
  status vocabulary covers both — map them explicitly.
- Sidecar metadata (e.g. screenshot notes) may accompany a raw file; keep the pairing
  in the manifest, not by naming convention alone.
- New test directory `tests/ingestion/` needs an `__init__.py`.
