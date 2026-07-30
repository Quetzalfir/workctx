# Migration from the legacy Markdown work repository

The earlier repository is valuable as a method reference, not as public product data. Migration must operate on a private copy and produce a sanitized report.

## What to preserve

- inbox to processed evidence lifecycle;
- evidence notes and original-source retention;
- projects, modules, flows, teams, people, decisions, risks, and questions;
- parent tasks and real subtasks;
- status, blockers, dependencies, next actions, and suggested messages;
- distinction among facts, inferences, and assumptions;
- knowledge curation and draft-reply skills;
- source-system snapshot pattern used by the Rally utility;
- ownership-boundary discipline.

## What to change

### Large instruction file

Move detailed procedures out of the legacy `AGENTS.md` into portable skills and policy docs. Keep the root contract concise.

### Manual duplicate views

Convert current focus, next actions, indexes, and waiting-on lists into generated views derived from canonical tasks and claims.

### References

Replace free-form reference tables and absolute paths with:

- stable IDs;
- canonical URIs;
- typed relationships;
- atomic observation IDs;
- exact source locators;
- repository commit references.

Human-readable reference sections may remain as generated presentation.

### Secrets

Remove any policy permitting credentials in repository folders. Convert integrations to secret references and context-scoped connector configuration.

### Temporal state

Convert mutable status, ownership, deadline, and architecture assertions into current and superseded claims where historical truth matters.

### Schemas

Normalize frontmatter and validate every canonical entity type.

## Proposed migration command

```text
workctx migrate legacy <source-path> <target-context> --dry-run
workctx migrate legacy <source-path> <target-context> --apply
```

## Migration stages

1. inventory files and classify canonical versus generated or obsolete;
2. calculate hashes and produce a no-write report;
3. detect secrets, absolute paths, duplicate IDs, broken links, and unknown entity types;
4. map legacy directories to the new context template;
5. normalize frontmatter without changing substantive meaning;
6. create artifact manifests for preserved originals when available;
7. split evidence synthesis into atomic observations where source locators can be recovered;
8. convert references and task hierarchy;
9. generate claims for mutable state;
10. validate the staged context;
11. rebuild projections and views;
12. produce a migration ledger linking old paths to new URIs;
13. leave the original repository unchanged.

## Missing original evidence

A lightweight legacy export may contain only derived evidence notes. The migration must mark the raw artifact unavailable rather than pretending the derived note is the original. The note can remain a source with lower provenance quality until the original is restored.

## Acceptance

- no private data is copied into public fixtures;
- every migrated entity has a stable ID and valid schema;
- all references resolve or are explicitly marked unavailable/external;
- task hierarchy remains intact;
- generated views match canonical state;
- the migration report identifies every loss of precision;
- the source repository is untouched.
