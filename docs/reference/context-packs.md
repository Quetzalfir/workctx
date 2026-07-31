# Context packs

Context packs are deterministic, bounded retrieval results around one projected Work
Context entity. They are application-layer values: CLI and MCP adapters may serialize
them, but retrieval does not depend on either adapter.

## Public APIs

`workctx.retrieval` exposes:

- `resolve(reader, reference)` for `workctx://`, `artifact://`, and `repo://`
  references;
- `related(reader, reference, direction=..., depth=..., relations=...)` for
  deterministic typed-edge traversal;
- `trace(reader, reference, include_history=False)` for claim, task, entity, or
  observation provenance;
- `rank(candidates, query=None, reference_time=None)` and `score_candidate(...)` for
  explainable ordering;
- `build_pack(reader, reference, budget=12000, ...)` for the ten-section contract;
- `serialize_context_pack(pack)` for stable, sorted-key JSON.

All reads use the WP-210 typed projection API. Retrieval contains no SQL and does not
open the projection database or canonical files.

`resolve` returns an explicit result rather than using a missing-row exception. A valid
local reference is either `resolved` or `not_found`. Artifact and repository references
resolve to structural descriptors: digest, or repository/commit/path/line range. The
projection does not expose artifact-manifest availability or source-origin metadata, so
structural resolution does not claim that source bytes are currently available.

A foreign `workctx://` URI is refused at the retrieval boundary before lookup.

## Traversal and tracing

`related` performs breadth-first traversal in stable lexical order. Depth zero returns no
related nodes or edges. Callers can select inbound, outbound, or both directions and can
filter by a set of `RelationType` values. A stored edge and a related reference appear at
most once even when a cycle reaches them again.

`trace` follows:

- a claim's subject and source observations;
- a task's direct source observations independently of claims;
- current and uncertain claims by default;
- all claim states when `include_history=True`;
- observations embedded under an entity;
- source observations authored on direct edges.

Each traced observation retains its immutable artifact reference and exact typed locator.
An invalid or absent authored observation is returned as stable missing-observation
metadata; exception text and source content are not copied into the result.

## Ranking

Every ranking factor is an integer from 0 through 100. The final score is the weighted
sum `sum(factor * weight)`, from 0 through 10,000. There is no division after
weighting.

| Factor | Weight | Definition |
| --- | ---: | --- |
| Relation semantics | 20 | Fixed table below; a missing relation scores 0. |
| Reliable-evidence recency | 15 | Inclusive age buckets relative to one shared reference time; table below. |
| Claim state | 15 | Current, uncertain, superseded, and retracted score 100, 60, 25, and 0; non-claims score 50. |
| Confidence | 10 | High, medium, low, and missing score 100, 60, 25, and 0. |
| Directness | 15 | Depth zero scores 100 and each hop removes 20 points, to a floor of zero. |
| Query match | 10 | `floor(100 * matched_distinct_tokens / distinct_query_tokens)`; an empty query scores 0. |
| Entity importance | 5 | Task priority wins when present; otherwise the fixed entity-type table below is used. |
| Source quality | 10 | Floor of the arithmetic mean of the available observation-kind and locator-precision scores; no signals score 0. |

Relation semantics use this complete table:

| Score | Relations |
| ---: | --- |
| 100 | `evidenced_by`, `supports`, `contradicts`, `supersedes` |
| 95 | `derived_from`, `blocks`, `depends_on`, `waiting_on` |
| 90 | `owned_by` |
| 85 | `requested_by`, `parent_of` |
| 80 | `implements`, `affects`, `authenticates_via`, `operated_by` |
| 75 | `produces` |
| 70 | `calls`, `publishes_to`, `consumes_from`, `stores_in` |
| 40 | `mentions` |
| 20 | `related_to` |

Reliable-evidence recency uses the first inclusive bucket that matches:

| Evidence age | Score |
| --- | ---: |
| Future through 7 days | 100 |
| More than 7 through 30 days | 80 |
| More than 30 through 90 days | 60 |
| More than 90 through 180 days | 40 |
| More than 180 through 365 days | 20 |
| More than 365 days, or no timestamp | 0 |

A future timestamp is clamped to age zero. Context-pack candidates use the timestamp of
their resolved supporting observation, not an entity edit time or edge validity time.
An observation item uses its own observation timestamp. If no supporting evidence
timestamp is available, recency is 0. When a claim or edge authors multiple source
observations, retrieval checks them in authored order and uses the first reference that
resolves; unresolved references are skipped for ranking. The same selected observation
supplies timestamp, kind, and locator, so the factor inputs cannot come from different
sources.

Task-priority importance is P0=100, P1=80, P2=60, P3=40, and P4=20. It overrides
entity-type importance when both are present. Entity-type importance is:

| Score | Entity types |
| ---: | --- |
| 100 | `incident` |
| 90 | `risk` |
| 85 | `decision` |
| 80 | `question` |
| 75 | `project` |
| 70 | `task`, `investigation` |
| 65 | `system`, `service`, `flow`, `integration` |
| 60 | `person`, `team` |
| 55 | `evidence`, `claim`, `observation` |
| 45 | `module` |
| 35 | `artifact` |
| 20 | `draft` |
| 0 | missing entity type |

Observation-kind source-quality scores are fact=100, decision=95, commitment=90,
task=85, blocker=85, dependency=85, risk=75, question=65, inference=55, and
assumption=25. Precise `line_range`, `page_range`, `time_range`, `message`,
`image_region`, `json_pointer`, `table_range`, and `repo_range` locators score 100;
`whole_artifact` scores 20. For example, a precise assumption scores
`floor((25 + 100) / 2) = 62`. If only one source-quality signal exists, that signal is
the score. Source quality does not invent artifact-manifest quality signals that WP-210
cannot return.

Query tokenization is Unicode NFC/case-folded and diacritic-insensitive, then split into
literal alphanumeric tokens. Repeated query tokens count once, only complete token
matches count, and integer division rounds down. It does not expose FTS syntax.

Ranking never reads the clock. An explicit aware reference time is used when supplied.
Otherwise all candidates share their newest timestamp; candidates without any timestamp
use the fixed Unix epoch. Ordering is total score descending, factor vector descending in
the table order, then candidate key ascending.

## Ten sections

The hand-maintained `schemas/context-pack.schema.json` requires these sections:

1. `focal_entity`;
2. `claims_and_status_history`;
3. `direct_relationships`;
4. `source_observations`;
5. `related_tasks_and_dependencies`;
6. `people_and_interactions`;
7. `decisions_risks_and_questions`;
8. `contradictory_or_superseding_evidence`;
9. `architecture_entities`;
10. `budget_and_truncation`.

All ten are present even when a section has no items. One focal item is always present.
Default packs contain full current and uncertain claims plus a compact superseded-history
summary. `include_history=True` replaces that summary with full historical claims.
One-hop architecture entities are included only when `include_architecture=True`.

The projection has no distinct interaction model. The people section therefore reports
relevant person entities and treats dated source observations authored on their direct
relationships as interaction evidence.

## Budget units and truncation

The budget unit is `approx_tokens_chars_div_4`. For each included item, retrieval renders
compact, sorted-key JSON, counts Unicode characters, divides by four, and rounds up.
`used_units` is the sum of item costs in sections 1 through 9. It deliberately excludes
the stable top-level envelope, section wrappers, and section 10 so truncation metadata
cannot recursively change its own budget.

When a pack is over budget, items are removed in this order:

1. optional architecture entities;
2. historical claims and history summaries in `claims_and_status_history`;
3. people and interaction evidence;
4. decisions, risks, and questions;
5. related tasks and dependencies;
6. direct relationships;
7. contradictory or superseding evidence, including its compact historical metadata;
8. source observations;
9. current and uncertain claims;
10. focal details are compacted to identity and state.

Within one tier, the lowest total score and then lowest factor vector in table order is
removed first; the candidate key (section plus item ID) is the final stable tie-break.
Retained items use the inverse order.

The minimal focal identity is never removed. If the requested budget is smaller than that
minimum, the pack remains valid and reports `within_budget=false`, `minimum_units`, and
`over_budget_by`. Every removal or focal compaction produces an `omitted_items` record with
the section, item ID, removed units, and reason. Untruncated packs still contain section 10
with zero omissions.

## Determinism, snapshots, and security

Pack output contains the canonical context update time and projection source fingerprint,
not a wall-clock generation time. Assembly compares projection metadata before and after
retrieval and retries once if the projection changed. Repeated changes produce an explicit
`ProjectionChangedError` rather than a mixed-generation pack.

No embeddings, LLM calls, randomness, or network access participate in retrieval. Stable
serialization sorts every JSON object key.

Before user-authored titles, summaries, notes, bodies, or structured values enter a pack,
a recursive guard redacts the repository's existing secret-pattern union: credential
assignments, bearer tokens, secret-named JSON fields, and private-key material. Validated
durable source references and typed locator structure remain exact. Invalid authored
references are exposed only through guarded metadata and a digest-based synthetic item ID.

The Pydantic contract and `schemas/context-pack.schema.json` are independently maintained
per ADR 0008. Positive fixtures round-trip through both, and negative fixtures must be
rejected by both. JSON Schema 2020-12 has no standard instance-data equality operator, so
two relational invariants are additionally enforced by the typed application model:
`focal_uri` belongs to `context_id`, and budget/count fields agree arithmetically with
their related fields and arrays. Builders always construct and validate the model before
serialization.
