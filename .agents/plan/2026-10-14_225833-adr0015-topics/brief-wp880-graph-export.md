# Brief: WP-880 — Graph export and Obsidian helpers (ADR 0015 §7)

Codex worker, worktree `.worktrees/WP-880`, branch `agent/WP-880-graph-export`.
No commits; final message = report. `.agents/` read-only (mirrors and
`_HISTORICAL_TEMPLATE_BRIDGE_HASHES` are lead-reconciled). Read ADR 0015,
the WP-860 topics package, the WP-870 heat module, and
docs/guides/obsidian.md first.

Key fact: canonical references are `workctx://` URIs, which Obsidian does
not render as graph edges — the existing guide's graph claim is
effectively false today. Obsidian links only `[[wikilinks]]`/Markdown
links WITHIN one vault.

## Contract

1. `workctx graph export [--context PATH] [--json]` regenerates (fully
   replaces) two self-contained, read-only Obsidian vaults under the
   generated zone:
   - `04_views/graph/topic-map/`: one note per active topic
     (`<slug>.md`): frontmatter `tags: [heat/<bucket>]`, name, scope,
     heat score, last activity; body lists wikilinks to OTHER topics that
     share at least one referenced or tagged document, with the shared
     count (edge = co-reference). No item nodes in this map.
   - `04_views/graph/reference-map/`: one note per active topic linking
     to one stub note per supporting item (entities, tasks, evidence
     notes, drafts carrying the topic, plus documents they reference),
     each stub holding title, type, canonical `workctx://` URI, and
     context-relative path — never document bodies, never secret values.
   - Each vault gets `.obsidian/graph.json` with color groups mapping
     `tag:#heat/hot` -> red, warm -> orange, cool -> blue, cold -> purple
     (Obsidian colorGroups format: `{"query": "...", "color": {"a": 1,
     "rgb": <int>}}`), and any minimal `.obsidian/app.json` needed. Note
     in the guide: Obsidian sizes nodes by link count, so in the
     reference map a topic's size reflects how much supports it, and in
     the topic map it reflects how connected it is.
   - Deterministic output (sorted, stable names, no timestamps except
     the source data's own), idempotent, safe filenames on all OSes.
     `view rebuild` does not need to call it; `graph export` is explicit.
   - Optional `--topic <slug>`: export only that topic's neighborhood.
2. `workctx obsidian install [--yes] [--accept-agreements]`: detect an
   existing Obsidian install (platform-appropriate checks); otherwise
   print the exact platform command — Windows `winget install --id
   Obsidian.Obsidian -e`, macOS `brew install --cask obsidian`, Linux
   `flatpak install -y flathub md.obsidian.Obsidian` — and run it only
   with `--yes`. Never pass package-manager agreement-acceptance flags
   unless the operator explicitly supplies `--accept-agreements`.
   Missing package manager -> user-correctable error with the download
   page URL. Executable discovery and process execution behind
   injectable seams; tests never install anything.
3. `workctx obsidian open --map topics|references [--context PATH]`:
   ensures the export exists (runs it if missing) and opens the vault
   via the `obsidian://open?path=<urlencoded absolute vault path>` URI
   through the platform opener (injectable seam).
4. Docs: rewrite docs/guides/obsidian.md (accurate: two generated maps,
   colors, sizes, install/open commands, read-only rule, the context
   itself can still be opened as a vault for reading); cli-envelope
   rows. `workctx guide` lists `04_views/graph/` as generated. One
   sentence in each bridge and template AGENTS.md pointing agents to
   `workctx graph export` when the operator asks for a visual map
   (extend in place).

## Allowed paths

`src/workctx/graph/**` (new), `src/workctx/obsidian/**` (new),
`src/workctx/topics/**` (read helpers only), `src/workctx/cli.py`,
`src/workctx/guide.py`, `src/workctx/views/**` (only if a shared helper
is required), agent kit bridges (3), canonical context template
AGENTS.md via the sync script, `docs/guides/obsidian.md`,
`docs/reference/cli-envelope.md`, `tests/**`.

## Tests

Export: deterministic byte-identical reruns, co-reference edges, heat
tags, color config shape, stub content excludes bodies, topic filter,
empty-context behavior. Install/open: every platform branch via seams,
--yes and --accept-agreements gating, missing-manager error, URI
encoding of paths with spaces and non-ASCII. Prompt-surface content
assertions. Full gate where the sandbox allows; limits declared.
