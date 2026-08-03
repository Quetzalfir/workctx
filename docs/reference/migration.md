# Legacy repository migration

`workctx migrate legacy` converts a legacy Markdown work repository into a new,
isolated workctx context. The implementation is deterministic and local. It does
not call a model, execute source content, follow filesystem links, or modify the
legacy tree.

## Command

```text
workctx migrate legacy <source-path> <target-context-path>
workctx migrate legacy <source-path> <target-context-path> --dry-run
workctx migrate legacy <source-path> <target-context-path> --apply
```

Preview is the default. `--dry-run` is therefore optional, but it is useful in
scripts to make the intended mode explicit. If `--dry-run` and `--apply` are both
present, dry-run wins and the JSON envelope includes
`MIGRATION_DRY_RUN_OVERRIDES_APPLY` as a warning.

Use `--json` for the standard CLI result envelope. The result contains `mode`,
`applied`, and the complete typed `report`. Human preview mode prints the Markdown
report to stdout.

Apply requires a destination that does not exist or is an empty directory. The
engine constructs a sibling staging context, validates canonical state, rebuilds
SQLite and generated views, writes the reports, verifies the source fingerprint,
and only then publishes the staged directory. A non-empty destination is refused.

## Findings and overrides

The inventory pass reports possible secrets, instruction-like unsafe content,
machine-specific absolute paths, duplicate IDs, broken links, frontmatter parse
failures, and unknown entity types. Possible secrets and unsafe instruction-like
content block apply by default:

```text
workctx migrate legacy SOURCE TARGET --apply --allow-findings
```

`--allow-findings` only permits the migration to continue. It never copies a file
that triggered secret or unsafe-content detection, and reports use sanitized paths
and diagnostics. Absolute paths in normalized canonical content become stable
`unavailable://legacy/...` markers. Preserved source artifacts remain byte-exact
and are treated as untrusted evidence.

## Thirteen stages

1. Inventory files and classify them as canonical, generated, obsolete, or unknown.
2. Hash every source file and calculate the source-tree fingerprint.
3. Detect unsafe content and structural findings.
4. Map supported legacy directories and IDs into the initialized context template.
5. Normalize frontmatter through the existing domain models.
6. Preserve eligible original bytes and register their artifact manifests through ingestion.
7. Create observations only for recoverable artifact locators, such as exact frontmatter line ranges.
8. Convert durable references and parent/subtask relationships; mark unresolved targets unavailable.
9. Create evidence-backed claims for authored mutable state.
10. Validate the staged canonical context.
11. Rebuild the SQLite projection and generated operational views, then validate freshness.
12. Produce the old-path-to-new-URI migration ledger in both reports.
13. Recalculate the source-tree fingerprint and require it to match the initial value.

Dry-run executes stages 1 through 3 plus the stage-4 mapping preview. The stage table
marks stages 4 through 13 as `not_run`, because no destination is initialized or
written. Apply publishes only after all thirteen stages complete.

## Reports

Successful apply writes:

- `99_meta/migration/report.json`, the versioned machine-readable report;
- `99_meta/migration/report.md`, the human-readable report.

Both reports include source fingerprints, all thirteen stage statuses, classified
inventory entries and hashes, sanitized findings, every skipped file and reason,
recorded precision loss, the old-path-to-new-URI mapping ledger, validation results,
projection counts, generated-view paths, and audit-ledger counts. A blocked apply
returns the same report in the CLI failure envelope but does not create the target.

## Missing original evidence

When a legacy evidence note resolves to an available raw source, its canonical
frontmatter references the registered artifact. When only the derived note exists,
the note itself is preserved for traceability and the canonical evidence record is
marked with `raw_unavailable: true` and `provenance_quality: derived_only`. The
migration does not invent an original or a locator.

## Audit-ledger decision seam

The ledger policy is deliberately not settled by the migration implementation.
`MigrationLedgerWriter` is the seam. The provisional default is `single_import`:
all normalized canonical documents are applied atomically through one transaction
and one import ledger event.

Preserved artifact registration uses the existing ingestion service unchanged, so
it creates one ingestion event per artifact before the single canonical import
event. All of these events live in the unpublished staged context; a later failure
removes that staged context rather than publishing partial state.

The outstanding choices are:

- `single_import` (provisional): atomic and compact, but one event has coarse
  per-entity history and can be large;
- `per_entity`: finer audit and replay granularity, but more events and a safe
  all-or-nothing orchestration policy would need to be established;
- `none`: smallest bootstrap ledger, but canonical writes lose audit provenance
  and this conflicts with the normal transaction path.

The operator or architecture owner must decide which policy becomes permanent.

## Limitations

- Canonical legacy documents must be UTF-8 Markdown with parseable YAML frontmatter.
- Type inference is limited to supported directory names, explicit `entity_type` or
  `type`, and recognized ID prefixes.
- Duplicate-ID references resolve to the first source path in deterministic sorted
  order and record precision loss.
- Canonical tasks support parent and subtask depth. Deeper legacy hierarchies are
  flattened to the root and reported.
- Generated and obsolete files are not migrated; generated views are rebuilt from
  canonical state.
- Heuristic safety detection can require operator review. The override never weakens
  the rule that flagged source bytes are excluded.
