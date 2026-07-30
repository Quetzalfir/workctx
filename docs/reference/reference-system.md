# Reference system

## Stable IDs and canonical URIs

Canonical entity IDs use these lexical families:

| Entity | ID form |
| --- | --- |
| Artifact | `ART-YYYYMMDD-<slug>-NN` |
| Evidence | `EVD-YYYYMMDD-<slug>-NN` |
| Observation | `<EVD-ID>#OBS-NNN` |
| Task | `TASK-YYYY-NNN` |
| Subtask | `TASK-YYYY-NNN-STNN` |
| Decision | `DEC-YYYY-NNN` |
| Risk | `RISK-YYYY-NNN` |
| Question | `Q-YYYY-NNN` |
| Claim | `CLM-YYYY-NNNNN` |
| Person | `PER-<slug>` |
| System | `SYS-<slug>` |

Slugs use lowercase ASCII letters, digits, and single hyphens. These contracts validate and
format IDs; allocating the next available ID requires context state and is handled elsewhere.

Canonical entities use:

```text
workctx://<context-id>/<entity-type>/<entity-id>
```

The entity-type vocabulary contains exactly: `evidence`, `person`, `team`, `project`,
`system`, `service`, `module`, `flow`, `integration`, `decision`, `risk`, `question`, `task`,
`claim`, `draft`, `investigation`, `incident`, `observation`, and `artifact`.

An observation ID contains `#`, but a canonical URI cannot contain that character literally
because it would start a URI fragment. Encode it as `%23`:

```text
workctx://example-context/observation/EVD-20260730-auth-review-01%23OBS-004
```

`WorkctxUri.parse` rejects the hand-authored literal form with guidance. The
`normalize_workctx_uri` helper converts a valid observation authoring form containing
`#OBS-NNN` to its canonical `%23` representation. Empty path segments, decoded traversal
segments, queries, and URI fragments are rejected.

## Source references

Artifacts use immutable content addressing with a 64-character lowercase SHA-256 digest:

```text
artifact://sha256/<digest>
```

Repository findings use a required immutable 7–64 character hexadecimal commit and a
relative, non-traversing repository path:

```text
repo://<repo-id>@<commit>/<path>#L<start>-L<end>
```

Branches are not durable repository locators. Raw POSIX paths, Windows paths, UNC paths, and
`file://` URIs are never durable references. External schemes such as `jira://` remain grammar
placeholders until connector work defines their resolver behavior.

## Source locators

Material statements should resolve to atomic observations and then to one of nine locator
types: `line_range`, `page_range`, `time_range`, `message`, `image_region`, `json_pointer`,
`table_range`, `repo_range`, or `whole_artifact`. Line, page, time, and repository ranges
require their end value to be greater than or equal to their start value. Use
`whole_artifact` only with a non-empty justification when a narrower locator is impossible.

## Observations, claims, and relations

Observation kinds distinguish facts, inferences, assumptions, decisions, commitments,
tasks, risks, blockers, dependencies, and questions. Claims record temporal assertions with
current, superseded, retracted, or uncertain status while retaining their source observation
URIs and supersession IDs.

Typed relationships make retrieval intentional. Use `related_to` only when a more precise relation is not known.

The full design is in `.agents/plan/initial/03-reference-and-retrieval-model.md`.
