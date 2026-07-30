# Reference and retrieval model

This is a foundational work package. Precise, typed, resolvable references are required before large-scale ingestion or task automation.

## Goals

- locate the exact evidence supporting a statement;
- retrieve related tasks, people, systems, decisions, and history without scanning the whole workspace;
- preserve historical truth when newer evidence changes the current state;
- avoid machine-specific paths;
- support local files, repositories, and external systems through one normalized reference model;
- make every relationship inspectable and testable.

## Stable identifiers

Each canonical entity has:

- `id`: stable within its context;
- `entity_type`: controlled vocabulary;
- `uri`: globally unambiguous inside the installation;
- `aliases`: alternative names for resolution, never replacement IDs.

Recommended ID families:

| Entity | Pattern | Example |
| --- | --- | --- |
| Artifact | `ART-YYYYMMDD-<slug>-NN` | `ART-20260730-auth-review-01` |
| Evidence note | `EVD-YYYYMMDD-<slug>-NN` | `EVD-20260730-auth-review-01` |
| Observation | `<EVD-ID>#OBS-NNN` | `EVD-20260730-auth-review-01#OBS-004` |
| Task | `TASK-YYYY-NNN` | `TASK-2026-014` |
| Subtask | `TASK-YYYY-NNN-STNN` | `TASK-2026-014-ST03` |
| Decision | `DEC-YYYY-NNN` | `DEC-2026-005` |
| Risk | `RISK-YYYY-NNN` | `RISK-2026-006` |
| Question | `Q-YYYY-NNN` | `Q-2026-021` |
| Claim | `CLM-YYYY-NNNNN` | `CLM-2026-00421` |
| Person | `PER-<slug>` | `PER-alex-rivera` |
| System | `SYS-<slug>` | `SYS-customer-portal` |

IDs are immutable after creation. Renaming changes titles or aliases, not IDs.

## Canonical URI

```text
workctx://<context-id>/<entity-type>/<entity-id>
```

Examples:

```text
workctx://new-company/task/TASK-2026-014
workctx://new-company/evidence/EVD-20260730-auth-review-01
workctx://new-company/person/PER-alex-rivera
workctx://new-company/observation/EVD-20260730-auth-review-01%23OBS-004
```

A resolver must reject a URI whose context ID does not match the active context unless an explicitly authorized federated operation is used.

## Source references

A source reference identifies an artifact or external object plus the smallest useful locator.

### Artifact URI

```text
artifact://sha256/<hex-digest>
```

The artifact manifest maps that immutable digest to the context-local preserved file, metadata, and ingest status.

### Repository URI

```text
repo://<repo-id>@<commit-sha>/<path>#L<start>-L<end>
```

The commit is required for durable findings. A branch may be recorded as convenience metadata but is not a stable locator.

### External URIs

Examples:

```text
jira://<connection-id>/<issue-key>
confluence://<connection-id>/page/<page-id>
github://<connection-id>/<owner>/<repo>/pull/<number>
dynatrace://<connection-id>/query/<saved-query-id>
```

The connection ID is context-scoped and resolves configuration without exposing credentials.

## Source locator types

Every material observation should use one of these selectors when available:

| Type | Fields |
| --- | --- |
| `line_range` | `start_line`, `end_line` |
| `page_range` | `start_page`, `end_page` |
| `time_range` | `start_ms`, `end_ms`, optional speaker |
| `message` | channel/thread/message IDs and timestamp |
| `image_region` | page/frame plus normalized bounding box |
| `json_pointer` | RFC-style pointer into a captured payload |
| `table_range` | sheet/table and cell/range locator |
| `repo_range` | repo ID, commit, path, start line, end line |
| `whole_artifact` | only when a narrower locator is impossible and justified |

Locator precision is a quality metric. `whole_artifact` should be uncommon for long artifacts.

## Evidence note and observations

An evidence note is a human-readable synthesis. Atomic observations provide precise traceability.

Example frontmatter excerpt:

```yaml
id: EVD-20260730-auth-review-01
entity_type: evidence
artifact_ref: artifact://sha256/abc123...
observations:
  - id: EVD-20260730-auth-review-01#OBS-001
    kind: fact
    statement: The portal delegates authentication to the identity service.
    confidence: high
    source:
      ref: artifact://sha256/abc123...
      locator:
        type: time_range
        start_ms: 412000
        end_ms: 439000
        speaker: Alex Rivera
  - id: EVD-20260730-auth-review-01#OBS-002
    kind: inference
    statement: The identity service is probably the policy enforcement point.
    confidence: low
    derived_from:
      - workctx://new-company/observation/EVD-...%23OBS-001
```

Facts, inferences, assumptions, decisions, commitments, risks, and questions must be distinguishable.

## Claims and time

Use claims for mutable assertions such as status, ownership, deadlines, dependencies, and current architecture choices.

```yaml
id: CLM-2026-00421
subject: workctx://new-company/task/TASK-2026-014
predicate: status
object: blocked
observed_at: 2026-07-30T14:42:00Z
valid_from: 2026-07-30
valid_to: null
status: current
source_observations:
  - workctx://new-company/observation/EVD-...%23OBS-003
supersedes: null
confidence: high
```

When newer evidence changes the state, create a new claim and mark the old one superseded. Do not erase the history.

## Typed relations

Initial vocabulary:

### Provenance

- `derived_from`
- `evidenced_by`
- `supports`
- `contradicts`
- `supersedes`

### Work

- `parent_of`
- `depends_on`
- `blocks`
- `requested_by`
- `owned_by`
- `waiting_on`
- `produces`

### Architecture and organization

- `implements`
- `calls`
- `publishes_to`
- `consumes_from`
- `stores_in`
- `authenticates_via`
- `operated_by`
- `owned_by`
- `affects`

### General

- `mentions`
- `related_to`

Use `related_to` only when no more precise relation is known. Relations may include confidence, validity interval, source observations, and notes.

## Backlinks

Do not require humans or agents to maintain backlinks manually. Canonical outbound references are indexed into derived inbound edges. The CLI and MCP resolver expose both directions.

## Context packs

A context pack is a bounded retrieval result for one task or question. It should include:

1. the focal entity;
2. current claims and status history;
3. direct typed relationships;
4. source observations with locators;
5. related tasks and dependencies;
6. relevant people and last interactions;
7. decisions, risks, and open questions;
8. recent contradictory or superseding evidence;
9. optional one-hop architecture entities;
10. a token/size budget and truncation explanation.

Retrieval must rank by:

- relation semantics;
- recency of reliable evidence;
- current versus superseded state;
- confidence;
- directness;
- user query match;
- entity importance;
- source quality.

## Required CLI and MCP behaviors

```text
workctx ref show <uri>
workctx ref related <uri> --depth 1
workctx ref trace <uri> --to-source
workctx context-pack <uri> --budget 12000
workctx search "authentication flow" --type flow --json
```

The MCP equivalents return structured data plus human-readable summaries.

## Validation rules

- every URI resolves or is explicitly marked external/unavailable;
- context IDs match the active boundary;
- observations reference valid artifacts and locators;
- task parent/root relationships are acyclic;
- `blocks` and `depends_on` edges are checked for contradictory cycles;
- current claims have no overlapping current successor for single-valued predicates;
- supersession chains are acyclic;
- repository references use commit SHAs;
- absolute machine paths are rejected as durable references;
- generated backlinks match canonical outbound edges.
