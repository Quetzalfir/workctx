# Using Obsidian with a context

Obsidian is optional. Work Context remains fully useful without it, and
Work Context is the only supported mutation path for canonical context data.

Canonical relationships use `workctx://` URIs. Obsidian does not turn those
URIs into graph edges. Use the explicit graph export when you want a visual
map; it generates Obsidian `[[wikilinks]]` inside two separate vaults.

## Install Obsidian

Preview the platform package-manager command without running it:

```text
workctx obsidian install
```

Run that command only after reviewing it:

```text
workctx obsidian install --yes
```

Work Context uses `winget install --id Obsidian.Obsidian -e` on Windows,
`brew install --cask obsidian` on macOS, and
`flatpak install -y flathub md.obsidian.Obsidian` on Linux. It never passes
package-manager agreement flags unless the operator explicitly adds
`--accept-agreements`; that option adds the two `winget` agreement flags on
Windows. If the platform manager is unavailable, use the download page shown
by the command.

## Generate the maps

Run:

```text
workctx graph export --context <context-path>
```

The command fully replaces these generated, self-contained vaults:

- `04_views/graph/topic-map/` contains one note per active topic. A link
  joins topics that share a tagged or referenced canonical document, and the
  note records the shared-item count. There are no item nodes in this map.
- `04_views/graph/reference-map/` links each topic to small metadata-only
  stubs for its tagged items and the canonical documents they reference.
  Stubs contain only title, type, canonical `workctx://` URI, and
  context-relative path—never a document body or secret value.

Use `--topic <slug-or-alias>` to export only that active topic and its directly
connected topic neighborhood. Output is sorted and deterministic for the same
canonical data and heat evaluation time. `workctx view rebuild` does not
regenerate these vaults; graph export is intentionally explicit.

Both vaults include Obsidian graph color groups for the computed heat tags:
hot is red, warm is orange, cool is blue, and cold is purple. Obsidian sizes
nodes by link count. In the reference map, a larger topic has more supporting
items; in the topic map, a larger topic is connected to more other topics.

## Open a generated vault

Use either command:

```text
workctx obsidian open --map topics --context <context-path>
workctx obsidian open --map references --context <context-path>
```

If the generated vaults are missing, `open` exports them first. It then opens
the selected absolute vault path through an encoded `obsidian://open` URI.

## Read-only rule

Everything under `04_views/graph/` is generated and read-only. Edits are
discarded by the next `workctx graph export`. Do not place notes, attachments,
or canonical knowledge in either generated vault.

You may still open the context root itself as an Obsidian vault for reading,
search, and browsing Markdown. Treat `00_inbox/`, `01_processed/`, `04_views/`,
`98_state/`, and `99_meta/` according to their Work Context ownership rules,
and do not expect canonical `workctx://` references to appear as Obsidian graph
edges. Obsidian may create a `.obsidian/` directory in the context root; keep
that editor state out of version control.
