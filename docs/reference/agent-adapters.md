# Agent adapters

Work Context uses one canonical project skill inventory with project-scoped adapters for Codex,
Claude Code, and Gemini CLI. The adapter API detects a client, plans changes, applies a prepared
plan, reports drift, repairs only stale managed files, uninstalls only managed files, and can open
the selected client in the project root. CLI wiring is outside this component.

## Security boundary

Agent adapters never read, copy, record, or configure client credentials. In particular, they do
not inspect user-home authentication files, credential stores, token values, or user-global client
configuration. Detection uses only executable discovery, `--version`, and metadata for the fixed
project markers listed below. Session bootstrap launches the discovered executable with the
selected project as its working directory and supplies no environment or credential mapping.

All adapter outputs, manifests, locks, staging files, and backups remain below the selected
project. The only adapter state outside the project is Work Context's own trusted install record,
`agent-adapter-installs.json`, under the directory returned by
`platformdirs.user_config_path("workctx", appauthor=False)`. It contains canonical project roots,
adapter names, derived manifest paths, and hashes only; it contains no client settings,
authentication material, or generated content. Filesystem operations reject absolute paths,
traversal, links, reparse points, and non-regular managed files. Tests inject fake executable
discovery, fake process spawning, and an isolated Work Context user-config directory; no real
agent installation is needed.

## Canonical source selection

When a project contains a safe, complete `.agents/skills/` tree, the installer reads its
`registry.yaml` and skill documents. This local, user-controlled tree has precedence. If the tree
is absent, the installer uses the canonical skills and registry shipped in the package's agent
kit. A deterministic repository sync check mirrors only `.agents/skills/`, including
`registry.yaml`, into the kit. The kit's instruction bridges are separately authored,
target-flavored resources; they are not synchronized copies of this repository's development
bridges.

Codex consumes `.agents/skills/<skill-name>/SKILL.md` directly. In a project without local
canonical sources, a Codex install first materializes the packaged canonical kit there, then
records each skill as `native-verified`; those canonical files become user-controlled sources and
are retained on uninstall. A native-verified entry commits to every file in that skill directory
through a sorted source set and an aggregate digest, so auxiliary resources participate in drift
detection. Claude and Gemini can render directly from either the local inventory or the packaged
fallback.

The registry supplies advisory `side_effect_class` metadata. Generated Claude and Gemini skill
copies receive `# workctx-side-effect-class: <class>` immediately before the closing frontmatter
delimiter. When a skill owns safe linked resources, one sorted
`# workctx-resource-sha256: sha256:<digest> "<JSON-quoted-relative-path>"` line follows the
side-effect line for each resource, and the same exact resource bytes are copied below the native
skill directory. Resource paths use the manifest's portable ASCII segment grammar. Credential-
capable names and content are rejected before copying. Canonical skill bytes are never changed.

## Client strategies

| Client | Project markers used for detection | Skill strategy | Instruction bridge |
| --- | --- | --- | --- |
| Codex | `AGENTS.md`, `.agents/skills`, `.codex/config.toml`, `.codex/config.local.toml` | Native verification at `.agents/skills/<name>/SKILL.md`; no redundant `.codex/` skill copy | `AGENTS.md` |
| Claude Code | `CLAUDE.md`, `.claude/skills`, `.mcp.json` | Generated copy at `.claude/skills/<name>/SKILL.md` | `CLAUDE.md` |
| Gemini CLI | `GEMINI.md`, `.gemini/skills`, `.gemini/commands`, `.gemini/settings.json` | Generated native skill at `.gemini/skills/<name>/SKILL.md` | `GEMINI.md` |

Gemini's Phase 1 native form is `.gemini/skills/`. Work Context does not generate Gemini commands
or require a user-global extension.

Each missing bridge is created from its self-contained packaged template and recorded in the
adapter manifest. The Codex template references `.agents/skills/`; Claude and Gemini reference
their installed `.claude/skills/` and `.gemini/skills/` projections. The latter two also direct
the client to an existing root `AGENTS.md`. In a context root, the context-template `AGENTS.md`
therefore remains the user-owned base contract. An existing bridge is classified as user-owned:
installation, repair, and uninstall never modify or delete it. Status reports template divergence
as a warning so ownership remains visible without turning the packaged bridge into authority over
user content. Only an absent bridge is generated during initial installation; later repair of a
manifest-recorded generated bridge remains subject to all three mutation-authority factors.

Installing one client computes and touches only that client's paths, its bridge, and shared
project-local transaction state. It never requires another client's executable or directory.

## Version detection

The Phase 1 supported ranges are deliberately conservative:

| Client | Supported versions |
| --- | --- |
| Codex | `>=0.1.0,<1.0.0` |
| Claude Code | `>=1.0.0,<3.0.0` |
| Gemini CLI | `>=0.1.0,<1.0.0` |

Executable discovery and version probing are independent per client. A supported executable is
`available`; project markers without an executable are `configured_only`; neither is `missing`.
An unparseable version, failed probe, or version outside the table is `unsupported`, and install
or open fails safely. Supporting a new major client version requires an adapter release that
verifies its native layout rather than an optimistic range expansion.

## Manifests, drift, and repair

Context installations store one manifest per client at
`98_state/agent-adapters/<client>/skill-manifest.json`; repository-only installations use
`.workctx/agent-adapters/<client>/skill-manifest.json`. The manifest records the adapter and
registry versions and hashes, canonical paths and hashes, generated targets and
hashes or native-verified source sets, bridge ownership, retained backups, and component states.
Content hashes are SHA-256 over exact bytes. Timestamps are informational and do not determine
freshness. The project manifest is bookkeeping, not mutation authority.

Deletion or overwrite authority requires all three authority factors at preflight and immediately
before mutation:

1. the target is within the selected adapter's schema-enforced project scope;
2. its current exact-byte hash equals the hash recorded for that target in the manifest; and
3. the SHA-256 digest of the exact manifest bytes equals the stable digest for that canonical
   project root and adapter in the Work Context user-config install record.

If any factor fails, repair and uninstall are report-only: they describe the preserved paths but
perform no deletion or overwrite. User approval and backup creation cannot substitute for a
missing factor. The trusted record is written only by the installer through a guarded,
compare-and-swap update; it is separate from all client authentication and configuration files.

Status safely compares the recorded inventory with current canonical inputs and managed outputs.
It distinguishes changed sources, changed inventory or render layout, missing outputs, and
hand-modified outputs. A modified generated file is a conflict, while ordinary source or renderer
drift is stale. Unmanaged files alongside generated files are warnings and remain untouched.

Install and repair first return a complete dry-run plan. A clean reinstall performs no writes and
preserves the manifest bytes. Repair targets only stale managed entries. An unchanged generated
target may be replaced after a canonical or renderer change only when all three authority factors
hold. A modified generated target is a conflict and is preserved in report-only mode. Every
precondition is rechecked while holding the project-local lock; output and manifest changes use a
staged, recoverable transaction.

Before a project mutation, the installer stores a pending trusted transition containing the old
and new manifest digests and a digest of the exact ordered operation set. Pending state never
authenticates an ordinary repair or uninstall. Normal completion or recovery must bind the
project transaction to that operation digest and resolve the trusted record only to the exact
verified preimage or postimage manifest digest. An unknown manifest digest, a different operation
set, or an unsafe record leaves the transition pending and permits no automatic project mutation.

## Backups and uninstall

Retained backups use `.workctx/backups/<timestamp>/`, even when the adapter manifest lives below
`98_state/`. Backup entries record the original path, backup path, exact content hash, and UTC
creation time. They are bookkeeping for already-retained preimages, not evidence of ownership and
not mutation authority. Transaction-local verified preimages remain separate and exist only to
roll back an authenticated in-progress transaction.

Uninstall preflights the complete manifest and removes only manifest-listed generated files and
generated bridges. Missing managed files are already removed. Unmanaged files and user-owned
bridges always survive, and directories are never recursively deleted. A modified managed file or
backup fails the content-hash factor, so uninstall becomes report-only; neither approval nor a new
backup authorizes its deletion. The authenticated manifest is removed last. Project transaction
state and the operation-bound pending trusted transition remain available when recovery cannot
safely verify a preimage or postimage result.

## MCP configuration

Each adapter configures the integrated stdio server as `workctx mcp serve --context .` under the
fixed server name `workctx`. Configuration is project-scoped and client-specific:

| Client | Path | Native entry |
| --- | --- | --- |
| Codex | `.codex/config.toml` | `[mcp_servers.workctx]` with `command = "workctx"` and `args = ["mcp", "serve", "--context", "."]` |
| Claude Code | `.mcp.json` | `mcpServers.workctx` with the same command and argument array |
| Gemini CLI | `.gemini/settings.json` | `mcpServers.workctx` with the same command and argument array |

An absent file is generated deterministically and recorded in
`components.mcp_configuration` with state `generated`, its exact path, and its exact-byte hash. An
existing file remains user-owned and is never merged or rewritten: it is recorded as `native` when
the `workctx` entry has the expected command and arguments, otherwise as `divergent`. Status
compares the fixed path and current hash with the manifest. A changed generated config is a
`generated_modified` conflict; a changed or missing user-owned config is a preserved
`mcp_divergent` warning.

Repair and uninstall may replace or remove only a `generated` config whose path, current hash, and
trusted manifest digest satisfy all three authority factors. Native and divergent configs
are never mutation targets. Legacy manifests with state `not_implemented` remain readable and are
upgraded by an authenticated repair.

## Session bootstrap

`open_context(root, client, ...)` repeats typed detection, rejects missing or unsupported clients,
and spawns only the selected executable with `root` as the working directory and shell execution
disabled. It returns the resolved root, executable, client, and process ID. A discovery-to-launch
race is reported as an unavailable dependency. Process lifecycle and interactive streams remain
owned by the caller.
