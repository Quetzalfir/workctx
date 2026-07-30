# Test strategy

## Principles

- deterministic controls require deterministic tests;
- every bug fix adds a regression test;
- canonical data must survive projection deletion;
- tests must exercise Windows path and filesystem behavior, not only POSIX assumptions;
- agent prose is not tested by exact wording; structured contracts and invariants are;
- no tests use real employer data, tokens, or network services by default.

## Layers

### Unit tests

Cover:

- ID parsing and allocation;
- URI normalization and context boundaries;
- source locator validation;
- relation vocabulary and direction;
- task hierarchy and state transitions;
- temporal claim supersession;
- transaction preconditions;
- path sanitization;
- result envelopes and exit mapping.

### Schema and contract tests

- every JSON Schema is valid;
- Pydantic and JSON Schema examples round-trip;
- work-order and report examples validate;
- canonical template documents validate;
- CLI JSON output conforms to its envelope;
- MCP tool input/output schemas remain backward compatible within a release line.

### Filesystem integration tests

Use temporary directories to cover:

- context creation;
- atomic write and rollback;
- stale lock handling;
- path traversal and symlink escape;
- duplicate artifact hash;
- archive only after successful transaction;
- projection rebuild after deletion;
- Unicode and long path behavior where supported;
- interruption/failure between transaction stages.

### SQLite projection tests

- rebuild equivalence;
- FTS indexing and ranking fixtures;
- backlinks from outbound references;
- migration forward behavior;
- stale projection detection;
- no cross-context rows;
- concurrent readers and serialized writer behavior.

### CLI acceptance tests

Use Typer's test runner or subprocess tests for:

- human and JSON output;
- exit codes;
- context resolution precedence;
- noninteractive behavior;
- dry-run versus apply;
- actionable diagnostics.

### MCP integration tests

- tool discovery;
- structured input validation;
- read resources;
- context scoping;
- safe error mapping;
- mutation approval parameters;
- no tool can escape its context root;
- stdio server lifecycle.

### Agent adapter tests

Use isolated fake home directories and fake executable discovery:

- Codex project config generation;
- Claude bridge and skill generation;
- Gemini workspace settings and commands;
- idempotent reinstall;
- repair after user edits;
- uninstall without deleting user-owned configuration;
- no credentials copied.

## End-to-end fixtures

Create a fictional company fixture with:

- two meeting transcripts;
- one contradictory follow-up chat;
- one screenshot sidecar;
- two people;
- three systems;
- a parent task and two subtasks;
- an ownership change;
- a deadline change;
- one drafted response.

Required scenarios:

### E2E-001 — Context creation

A clean install creates a valid context and can rebuild its empty projections.

### E2E-002 — Evidence to traceable task update

An artifact is registered, observations use exact locators, a task changes state through a transaction, the original is archived, and the task traces back to the observation and artifact.

### E2E-003 — Contradiction and supersession

Newer reliable evidence contradicts an older claim. The current view uses the new claim, while history and both sources remain available.

### E2E-004 — New session recovery

After deleting chat/session state and generated views, rebuild projections and produce the same task context pack from canonical data.

### E2E-005 — Isolation

Two contexts contain similar names and task IDs. Search and MCP operations in one context never return the other.

### E2E-006 — Draft reply

A reply context includes relevant person history, current task state, evidence, and unresolved uncertainty, without inventing a promise.

### E2E-007 — Failed transaction

A proposed update contains a broken reference. No canonical file moves or partial task update occurs.

## Quality targets

- 100% branch coverage for URI boundary checks, transaction commit/rollback, and secret/path controls where feasible;
- at least 85% line coverage for the core package before alpha release;
- all documented CLI examples exercised in tests or doctest-style validation;
- no flaky test accepted without a tracked root-cause issue and quarantine policy.

Coverage is a supporting signal, not a substitute for scenario quality.

## Performance baselines

Before alpha release, record reproducible benchmarks for:

- context validation at 1k, 10k, and 100k entities;
- full projection rebuild;
- common FTS query;
- one-hop and two-hop context pack;
- transaction touching 1, 10, and 100 entities.

The lead must document hardware and fixture shape rather than publish unsupported universal performance claims.
