# Multiple contexts

A context is an isolated security boundary. Create one context per boundary,
and organize contexts on disk however you like — the parent folders are plain
filesystem organization and carry no shared state.

## Context kinds

`workctx context init` records what a context represents with `--kind`:

- `company` — an organization's internal operations: people, channels,
  management, HR, internal decisions;
- `project` — one project or one external client engagement;
- `product` — a long-lived product you own across projects;
- `personal` — your own notes and career context;
- `laboratory` — experiments and scratch work.

The kind is metadata for you and for agents reading the workspace; every kind
gets the same isolation and the same tools.

## Pattern: a company with isolated projects

A common setup is one employer or firm with several client projects that must
not see each other, plus the company's own internal context:

```text
C:\Assistants\acme\
  _company\        <- kind=company: internal operations
  client-alpha\    <- kind=project: isolated engagement
  client-beta\     <- kind=project: isolated engagement
```

```bash
workctx context init _company --name "Acme internal" --kind company
workctx context init client-alpha --name "Client Alpha" --kind project
```

Each directory is a full, sovereign context: its own canonical files, SQLite
and search state, agent configuration, and audit ledger. Evidence about a
client belongs in that client's context; evidence about the company itself
(staffing, channels, internal decisions) belongs in the company context. Never
move files between contexts — register the information where it belongs.

Cross-context references are rejected and there is no federated search or
company-wide roll-up view across projects; consolidated multi-context views
are a later-phase feature. Canonical URIs include the context ID, so similar
task IDs in two contexts never collide.

## Context registry

The advisory context registry is machine-local. Set `WORKCTX_CONTEXT_REGISTRY` to a full plain
filesystem path when the registry JSON file must live somewhere other than its platform-specific
default; this value is ordinary configuration, not a secret.

Preview an adapter refresh for every registered context before applying it:

```bash
workctx agent refresh --all
workctx agent refresh --all --yes
```

The default targets every detected available client; add `--agent codex`, `--agent claude`, or
`--agent gemini` to narrow the batch. `--yes` is the only fleet approval, so the first command
writes nothing. Missing roots and registry/context ID mismatches are warned and skipped. A failure
in one valid context is reported without stopping later contexts, and the completed partial batch
exits 6. Use `workctx agent install` when you intend to refresh only the current context.

## One context or many?

Rule of thumb: separate boundaries (different clients, different employers,
work you must never mix) get separate contexts. Within one boundary, prefer
one context even if it spans several repositories and workstreams — splitting
a single team's work across contexts only costs you cross-referencing.
A repository is not a context — it belongs to the project context that owns it; see [Code repositories](code-repositories.md).

## Where to put contexts on disk

Keep contexts on a local, non-synchronized disk. Cloud-sync folders (OneDrive,
Dropbox, Google Drive — including a OneDrive-redirected `Documents` folder on
Windows) interfere with lock files, atomic renames, and the SQLite projection,
and can silently upload evidence to a cloud account. If you want off-machine
history and backup, put each context in Git instead; that also gives the audit
ledger an independent second history.

Each context has independent:

- canonical files;
- SQLite and search state;
- plugin instances (planned for Phase 3);
- [secret references](../reference/secrets.md) resolve on this machine; names
  are machine-global in this release, so prefix them per project when contexts
  could collide;
- agent project configuration;
- audit records.
