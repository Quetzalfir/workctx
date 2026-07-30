# Context layout

```text
context.yaml
00_inbox/          Unprocessed and quarantined artifacts
01_processed/      Preserved processed originals
02_knowledge/      Durable entities, evidence, observations, claims, and relationships
03_work/           Canonical tasks, investigations, incidents, and plans
04_views/          Generated operational views
05_outbox/         Drafts and approved output artifacts
90_integrations/   Connector metadata and secret references
98_state/          Generated indexes, locks, caches, and local runtime state
99_meta/           Policies, templates, migrations, and audit metadata
```

`04_views/` and `98_state/` must be rebuildable. Do not place the only copy of knowledge there.
