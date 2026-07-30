# Phase 1 definition of done

Phase 1 is complete only when all required criteria below are demonstrated from a clean checkout.

## Installation and portability

- package builds and installs with documented commands;
- Windows, macOS, and Linux CI pass for supported Python versions;
- `workctx doctor` gives actionable results;
- no paid service or optional graph tool is required.

## Context lifecycle

- create at least two isolated contexts;
- inspect, validate, back up, and rebuild each context;
- no query or MCP call crosses context boundaries;
- schema versions and migrations are explicit.

## Evidence and provenance

- register evidence with an immutable hash and precise metadata;
- preserve the original artifact;
- represent atomic observations with exact source locators;
- distinguish fact, inference, assumption, decision, commitment, risk, and question;
- detect duplicates and invalid/broken references;
- archive only after successful canonical update.

## References and retrieval

- resolve canonical URIs;
- retrieve inbound and outbound typed relationships;
- trace a task or claim to an observation and source locator;
- preserve superseded history while identifying current state;
- build a bounded context pack with truncation metadata;
- rebuild retrieval indexes entirely from canonical data.

## Transactions and audit

- dry-run shows exact intended changes;
- invalid proposals leave canonical state untouched;
- valid multi-entity update is atomic;
- concurrent stale proposal is rejected;
- audit records actor, source, operations, result, and content hashes;
- failed projection rebuild is recoverable and clearly reported.

## Work operations

- parent tasks and real subtasks validate;
- dependencies, blockers, owners, requesters, waiting-on, and next actions are queryable;
- operational views are generated rather than manually canonical;
- a new session can recover current focus from workspace state.

## Agent interoperability

- install and inspect project adapters for Codex, Claude Code, and Gemini CLI;
- adapters use canonical rules and skills;
- a local MCP server is discoverable by the supported clients according to documentation;
- agent authentication remains owned by the user's chosen client;
- uninstall/repair does not destroy user-owned configuration.

## User acceptance scenario

Using only fictional fixtures:

1. create a context;
2. add a transcript and follow-up message;
3. process a proposal through an agent workflow;
4. apply a validated transaction;
5. show a task whose current status changed;
6. trace the status to exact evidence;
7. show the person being waited on and prior interaction;
8. draft a response with uncertainty preserved;
9. close the agent session;
10. delete generated state;
11. rebuild;
12. open a different supported agent;
13. recover equivalent context and next action.

## Quality and public release

- full lint, format, typing, unit, integration, security, and acceptance gates pass;
- critical controls have dedicated negative tests;
- public documentation is complete and uses no private examples;
- release archive excludes raw private evidence, credentials, caches, and local work orders not intended for publication;
- known limitations are explicit;
- the implementation lead signs off in a final review document with executed evidence.
