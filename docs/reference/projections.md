# SQLite projections

The SQLite database is a disposable, context-local projection of canonical Markdown and
YAML. It is never the only copy of user knowledge. Deleting `98_state/index.sqlite3` and
rebuilding from canonical files is a supported recovery path.

## Context boundary and files

`SQLiteProjection` binds to one resolved context root and derives its database path as
`98_state/index.sqlite3`. Callers cannot supply another database path, connection, cursor,
or SQL statement. The adapter rejects a context configuration, state directory, database,
canonical zone, or canonical document that resolves outside the bound root. A canonical
document must also resolve within the same indexed zone in which it was discovered, so a
symlink from `02_knowledge` into an excluded zone is skipped. Existing database and
state-directory symbolic links are rejected, as are symbolic links used as canonical zone
roots. The context configuration and state path are revalidated immediately before reads,
temporary-file creation, and replacement. Database sidecars are checked with non-following
existence semantics, and symbolic-link sidecars are rejected before SQLite opens the live
database. If the state path changes in the irreducible interval around temporary creation,
the adapter verifies the created file identity and removes that exact file before failing.

The adapter owns only these runtime names under `98_state/`:

- `index.sqlite3`;
- SQLite `-wal` and `-shm` siblings if a platform creates them;
- uniquely named `index.sqlite3.*.tmp` build databases and their SQLite temporary siblings.

The current implementation deliberately uses SQLite's `DELETE` journal mode and does not
create WAL files. The canonical store owns `lock.json`, `staging/`, and `backups/`; the
projection adapter does not read or write them.

Every stored row carries the bound context ID where the table shape permits it. Insert and
context-update triggers compare that ID with the singleton metadata row. Query predicates
also include the context ID. Local `workctx://` entity URIs, edge targets, claim subjects,
source-observation URIs, observation relations, and structured task fields are checked with
`WorkctxUri.require_context` before indexing or querying. External durable references remain
allowed, but a foreign Work Context URI is denied.
The context triggers fail closed while the singleton metadata row is absent.

## Canonical inputs

Rebuild scans Markdown files in deterministic relative-path order from:

- `02_knowledge/**` for knowledge entities, evidence notes, and claims;
- `03_work/**` for tasks and other work entities.

Zone `README.md` files are explanatory and ignored. `00_inbox` raw payloads,
`01_processed`, generated `04_views`, `05_outbox`, integration state, `98_state`, and
`99_meta/templates` are not indexed by this adapter.

Markdown is split only by `workctx.domain.frontmatter.parse_frontmatter`. Parsed mappings
then pass through the integrated domain models:

- task entities use `Task`;
- other entity documents use `EntityFrontmatter`;
- standalone claim documents use `Claim`;
- observations embedded in entity frontmatter, and supported standalone observation
  documents, use `Observation`;
- authored entity references are revalidated as public `TypedReference` values.

The Markdown body returned by the shared parser is retained for entity, claim, and
standalone-observation full-text search. Embedded observations use their statements and do
not duplicate the containing entity body. A malformed or unsupported document is skipped as
one unit. The rebuild continues, and its report contains the relative path, a stable reason,
and a sanitized diagnostic that does not echo source content. Duplicate IDs or URIs and
invalid task hierarchy members are also skipped deterministically. Canonical input files are
read-only during rebuild.

## Projection schema

`projection_metadata` contains one row with:

- projection and workspace schema versions;
- the bound context ID and canonical `context.yaml` `updated_at` value;
- a SHA-256 source fingerprint over `context.yaml` and every sorted candidate document,
  including malformed document bytes;
- source, indexed, and skipped document counts;
- UTC build-start and build-completion timestamps.

The remaining schema is organized by query purpose:

- `entities`, `aliases`, and `entity_tags` preserve entity identity and authored order;
- `edges` and `edge_source_observations` preserve authored typed references and their
  metadata;
- `backlinks` is a view over `edges` with the direction reversed for lookup, so it cannot
  drift from outbound references;
- `observations` and `observation_derivations` preserve exact IDs, artifact references,
  typed locator JSON, temporal fields, standalone bodies, parent entities, and provenance;
- `claims` and `claim_source_observations` preserve JSON claim objects, temporal status,
  supersession IDs, and trace inputs;
- `tasks` plus ordered waiting-on, dependency, blocker, and source-observation child tables
  preserve the task model without inventing relation direction for arbitrary task strings;
- `search_documents` and `search_fts` provide full-text lookup.

Claims do not fabricate entity titles or aliases because the canonical `Claim` model has no
such fields. Claim and observation URIs are derived with `WorkctxUri`; this percent-encodes
the literal `#` in observation IDs. Exact IDs remain in ordinary columns, not tokenized text.
Modeled date-time columns are normalized to UTC before storage, which makes claim history
ordering chronological even when canonical sources use different offsets. Typed locator
payload JSON remains the canonical domain serialization of that source metadata.

## FTS5 behavior

FTS5 indexes entity titles and bodies, claim bodies and predicate/object statements,
observation statements, and standalone observation bodies. The tokenizer is:

```text
unicode61 remove_diacritics 2
```

Search NFC-normalizes user input, splits it into literal Unicode alphanumeric tokens while
retaining in-token combining marks (and treating underscore as a separator), and joins those
tokens with `AND`; it does not expose the FTS query language. Results use weighted BM25
(`title=8`, `body=2`, `statement=5`) and a stable record-kind/ID tie-break. SQLite BM25 is
lower-is-better internally; the typed API returns its negation as `SearchHit.score`, so a
larger exposed score is better. Exact ID and URI lookup remains separate from FTS. FTS5 is
mandatory: absence of the module raises `Fts5UnavailableError` and stops the rebuild rather
than adding a replacement dependency.

## Full rebuild and schema versions

`rebuild()` always performs a full rebuild. `ensure_ready()` also performs one when the
database is missing, unreadable/incompatible, bound to another context, or has a projection
or workspace version mismatch. Compatibility requires every versioned table, view, index,
context guard, and the usable FTS virtual table; missing objects trigger a full rebuild.
Projection tables are never migrated in place, as required by ADR 0007.

The rebuild sequence is:

1. validate the bound context and scan canonical inputs read-only;
2. parse and validate each document, collecting sanitized skips;
3. build the complete schema, rows, and FTS index in a unique database sibling;
4. run the FTS external-content integrity check and SQLite `integrity_check`;
5. commit, close every build connection, and flush the temporary database file;
6. block new adapter-managed readers and wait for active readers to close;
7. checkpoint a live WAL, switch it back to `DELETE` mode, and require all destination
   `-wal`, `-shm`, and `-journal` sidecars to be absent; a busy or stale sidecar refuses the
   swap while preserving the old database;
8. replace `index.sqlite3` atomically with `os.replace` on the same filesystem;
9. reopen short-lived readers against the replacement on their next query.

Readers can continue using the old complete database while the temporary database is built.
At the final swap they either complete against the old file or reopen against the new file;
no adapter query observes a partially built schema. A bounded Windows sharing-violation
retry handles a reader in another process that is closing. If replacement fails, the old
database remains live and temporary siblings are cleaned.

The process-local reader/swap gate covers all adapter-managed readers in one process. A
long-lived raw SQLite connection in another process can still block filename replacement on
Windows; callers must use the typed API, and the canonical writer lock — which the
transaction engine holds across its post-commit rebuild — provides cross-process
serialization for transaction-driven rebuilds.

## Typed query surface

The adapter returns frozen typed records and fully materializes them before closing its
read-only `mode=ro&cache=private` connection. Its public reads include:

- entity lookup by ID or URI and exact alias lookup;
- outbound edges and derived inbound backlinks, optionally filtered by `RelationType`;
- exact observation and claim lookup, observations by containing entity, subject claim
  history/status filters, and a discriminated document lookup with generic-entity fallback;
- exact task lookup and `TaskQuery` filters for status, owner, waiting-on value, root, and
  parent;
- ranked FTS results with optional `EntityType` filters;
- projection metadata.

Each composite document lookup selects its specialized or generic representation through a
single reader connection, so a concurrent rebuild cannot mix two projection generations.

No SQLite row, cursor, connection, or SQL fragment crosses the adapter boundary.
Canonical-revision freshness is covered by the adapter's read-only `SqliteFreshnessProbe`,
which feeds the validation engine's stale-projection diagnostics. Incremental projection
updates remain unimplemented and are explicitly post-alpha work with no assigned owner; the
adapter supplies explicit full rebuild plus version/context compatibility checks.
