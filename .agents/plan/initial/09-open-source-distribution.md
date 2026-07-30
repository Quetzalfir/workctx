# Open-source distribution plan

## Distribution goals

A user should not need to clone the source repository to use `workctx` after the first release.

Primary installation target:

```text
uv tool install workctx
```

Development install:

```text
git clone <repository>
cd workctx
uv sync --all-groups
```

Alternative packaging may include `pipx`; standalone binaries are deferred until demand and packaging maturity justify them.

## Repository hygiene

Public examples must be fictional and license-compatible. Never ship:

- real company names or conversations;
- proprietary source code;
- screenshots or documents from employers;
- real tokens, tenant IDs, dashboards, or internal URLs;
- private agent transcripts.

## Documentation set

Before alpha, publish:

- installation;
- five-minute quick start;
- context concepts;
- evidence processing;
- task and reference model;
- agent setup for Codex, Claude, and Gemini;
- security and privacy;
- context isolation;
- migration guide;
- plugin author guide stub;
- troubleshooting;
- architecture and ADRs;
- contributing and release process.

## Versioning

- semantic versioning for the CLI/package;
- explicit workspace schema version;
- MCP tool version or compatibility metadata;
- plugin API version;
- migration path for every released workspace schema change;
- deprecation warnings before removal where possible.

## Agent-neutral packaging

Canonical skills live once under `.agents/skills/`. The installer generates agent-native adapters. Do not force a user to install all agents.

Agent setup must:

- detect installed clients;
- show proposed configuration changes;
- preserve user-owned settings;
- be idempotent;
- support repair and uninstall;
- never capture authentication tokens;
- scope MCP to a project/context when supported.

## Profiles

Initial context profiles:

| Profile | Core behavior |
| --- | --- |
| `light` | meetings, people, tasks, drafts; no source repositories required |
| `architect` | systems, flows, decisions, risks, repositories, broad document context |
| `developer` | tasks, requirements, repositories, implementation and test context |
| `hybrid` | architect plus developer capabilities |

Profiles are defaults, not security roles.

## Optional products

Graphify, CodeGraph, Obsidian, and connectors remain optional plugins or documented integrations. The public quick start must not imply a paid dependency.

## Release gate

- clean package build;
- install into an empty environment;
- CLI smoke test;
- license and notices reviewed;
- dependency lock committed;
- changelog and known limitations complete;
- fictional acceptance fixture included;
- security reporting route configured;
- supported agents tested with documented versions.
