---
name: validate-context
description: "Use before and after broad context mutations, migrations, release checks, or when references, views, indexes, tasks, or context isolation may be inconsistent."
---

# Validate context

## Checks

- context configuration and schema version;
- required canonical directories;
- entity frontmatter schemas;
- unique IDs and canonical URIs;
- reference resolution and context boundary;
- artifact hashes and source locators;
- task parent/root integrity and dependency cycles;
- claim validity and supersession chains;
- forbidden absolute paths and secret-like content;
- generated view headers and freshness;
- SQLite/index consistency and rebuildability;
- inbox/processed lifecycle anomalies;
- audit and transaction consistency.

## Procedure

1. Run non-destructive validation.
2. Classify findings as error, warning, or advisory.
3. Explain impact and exact repair action.
4. Never auto-repair canonical data without a reviewable proposal.
5. Rebuild derived state only when canonical validation permits it.
6. Re-run validation and report exact before/after results.
