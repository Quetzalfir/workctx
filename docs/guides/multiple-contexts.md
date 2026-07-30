# Multiple contexts

Create one isolated context per company or security boundary. A context may contain several projects and repositories from that same boundary.

```text
D:\WorkContexts\company-a
D:\WorkContexts\company-b
D:\WorkContexts\personal-product
```

Each context has independent:

- canonical files;
- SQLite and search state;
- plugin instances;
- secret references;
- agent project configuration;
- audit records.

Cross-context search is denied by default. Similar task IDs or names in two contexts must not create ambiguity because canonical URIs include the context ID.
