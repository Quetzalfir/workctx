# Validation diagnostics

`validate_workspace(root)` reads canonical workspace state and returns a
`ValidationReport`. It never edits, renames, quarantines, or repairs a file. Every issue
includes a stable code, severity, message, relative path when available, and a reported
`repair_action` for a caller or human to evaluate separately.

Passing `strict=True` escalates warnings to errors. Advisories remain advisories because
they describe references or derived state that the local engine cannot resolve, rather than
invalid canonical state.

## Diagnostic catalog

| Code | Default severity | Cause | Repair action |
| --- | --- | --- | --- |
| `CTX-CONFIG` | error | `context.yaml` is missing, unreadable, or invalid for `ContextConfig`. | Restore `context.yaml` and correct it to satisfy the current typed model. |
| `CTX-FEDERATED-SEARCH` | error | The Phase 1 context enables `federated_search`. | Set `policies.federated_search` to `false`. |
| `CTX-MISSING-DIRECTORY` | error | A required top-level context directory is absent. | Restore the named directory from the canonical context layout. |
| `CTX-NON-UTF8` | warning | A workspace text file cannot be decoded as UTF-8. | Convert the file to UTF-8 without changing its intended content. |
| `CTX-UNREADABLE-PATH` | error | A workspace file or directory could not be inspected. | Restore read access and rerun validation so the path can be checked. |
| `CTX-ABSOLUTE-PATH` | warning | A durable value contains a machine-specific absolute path. | Replace it with a context-relative path or canonical durable URI. |
| `CTX-PATH-ESCAPE` | error | A workspace link crosses a read boundary, or an artifact path is unsafe or non-portable. | Remove the link or use a portable forward-slash artifact path inside the context root. |
| `CTX-POSSIBLE-SECRET` | error | Text contains a secret-looking assignment or private-key marker. | Remove the value and store only an approved secret reference. |
| `DOC-PARSE` | error | A canonical Markdown, YAML, or JSON document cannot be parsed. | Repair its syntax and required frontmatter delimiters. |
| `DOC-MODEL` | error | A canonical document does not satisfy its integrated domain model. | Correct its fields to satisfy the current typed domain contract. |
| `DOC-FILENAME-ID` | error | A canonical document filename does not match its frontmatter ID. | Rename the file to its immutable ID while preserving the appropriate extension. |
| `DOC-DUPLICATE-ID` | error | More than one canonical document declares the same identity. | Keep one canonical owner of the ID and reconcile the duplicate. |
| `REF-INVALID-URI` | error | A durable reference is malformed or not canonically encoded. | Use a canonical `workctx://`, `artifact://`, `repo://`, or external URI. |
| `REF-REPO-SHA` | error | A repository reference omits an immutable hexadecimal commit SHA. | Replace the branch or tag with a 7-64 character commit SHA. |
| `REF-CONTEXT-MISMATCH` | error | A `workctx://` reference crosses the active context boundary. | Use an entity in the active context or an explicitly approved federated operation. |
| `REF-UNKNOWN-ENTITY-TYPE` | error | A `workctx://` reference uses a type outside the canonical entity-type vocabulary. | Replace the entity-type segment with one of the canonical entity-type values. |
| `REF-UNRESOLVED` | error | An internal Work Context reference has no canonical target. | Restore the target entity or point to an existing canonical URI. |
| `REF-ARTIFACT-UNAVAILABLE` | advisory | An `artifact://` digest has no matching artifact manifest. | Restore or ingest the manifest, or explicitly retain the artifact as unavailable. |
| `REF-EXTERNAL-UNAVAILABLE` | advisory | A valid repository or external reference has no local resolver. | Configure an authorized resolver or explicitly retain it as unavailable. |
| `OBS-INVALID` | error | An embedded observation or source locator violates `Observation`. | Correct the observation fields, artifact reference, and exact locator. |
| `OBS-EVIDENCE-ID` | error | An embedded observation ID does not belong to its evidence note. | Use the containing evidence ID followed by `#OBS-NNN`. |
| `TASK-HIERARCHY` | error | The task corpus violates parent, root, uniqueness, or context rules. | Restore the parent or correct the task type, IDs, parent, root, and context. |
| `TASK-RELATION-CYCLE` | error | Normalized `blocks` and `depends_on` precedence relations form a cycle. | Remove or reverse a contradictory relation so prerequisites are acyclic. |
| `TASK-RELATION-TARGET` | error | A `blocks` or `depends_on` relation points to something other than a task. | Use a canonical task ID or `workctx://` task URI. |
| `CLAIM-INTERVAL` | error | A claim interval is empty or ends before it starts. | Set `valid_to` after `valid_from`, or leave the appropriate bound open. |
| `CLAIM-CURRENT-OVERLAP` | error | Current claims for one subject and predicate overlap in time. | Close or supersede the older interval so one current value applies at a time. |
| `CLAIM-SUPERSESSION-MISSING` | error | A supersession ID has no canonical claim target. | Restore the referenced claim or correct the supersession ID. |
| `CLAIM-SUPERSESSION-CYCLE` | error | Normalized claim supersession relations form a cycle. | Break the cycle so supersession history progresses in one direction. |
| `PROJECTION-STALE` | warning | A configured freshness probe reports stale derived projections. | Rebuild projections from canonical workspace files. |
| `PROJECTION-BACKLINK-MISMATCH` | warning | Generated backlinks differ from canonical outbound edges. | Rebuild the backlink projection from canonical edges. |
| `PROJECTION-FRESHNESS-UNKNOWN` | advisory | A configured probe cannot determine projection freshness. | Configure a projection-aware probe or verify projections independently. |
| `PROJECTION-PROBE-FAILED` | warning | A configured freshness probe fails during its read-only check. | Inspect the probe integration and retry validation. |

## Semantic conventions

Phase 1 represents one current value per exact claim `subject` and `predicate`; a JSON
array can carry a multi-item value. Current validity intervals are half-open
`[valid_from, valid_to)`, with a missing bound treated as open-ended. Adjacent intervals
therefore do not overlap.

Task relation checks normalize edges into prerequisite order:

- `A depends_on B`, `A.dependencies: [B]`, and `A.blockers: [B]` mean `B` precedes `A`;
- `A blocks B` means `A` precedes `B`.

For claims, `new.supersedes: old` and `old.superseded_by: new` describe the same directed
edge. Reciprocal declarations therefore do not create a false cycle.

The engine does not import a projection adapter. A caller may provide a `FreshnessProbe`,
which receives the validated canonical outbound edge set. `NullFreshnessProbe` reports
`unknown`; omitting a probe leaves projection freshness unreported so a canonical-only
workspace can validate cleanly.

In canonical zones, every YAML or JSON file is a typed-document candidate. Markdown with
frontmatter is also typed; a stable-ID filename without frontmatter produces `DOC-PARSE`.
Other plain Markdown remains auxiliary content and still receives encoding, absolute-path,
and secret checks, preserving the existing CLI behavior for context-local notes.
