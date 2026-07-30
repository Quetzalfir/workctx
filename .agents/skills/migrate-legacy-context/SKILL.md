---
name: migrate-legacy-context
description: "Use when importing an older Markdown-based work memory into the new context schema while preserving evidence, task hierarchy, provenance, and history without modifying the source."
---

# Migrate legacy context

## Procedure

1. Make the source read-only and inventory it.
2. Detect private data, secrets, absolute paths, duplicate IDs, broken links, missing originals, and generated views.
3. Produce a dry-run mapping from old paths to new canonical URIs.
4. Preserve raw evidence when present; mark unavailable originals honestly.
5. Normalize frontmatter and stable IDs.
6. Convert material evidence statements into atomic observations when source locators can be recovered.
7. Convert free-form references to typed, resolvable references.
8. Preserve task parent/subtask hierarchy and status history.
9. Convert mutable state to claims when useful.
10. Stage the entire migration and validate before apply.
11. Build new projections and generated views.
12. Produce a migration ledger and precision-loss report.
13. Never change the legacy source.
