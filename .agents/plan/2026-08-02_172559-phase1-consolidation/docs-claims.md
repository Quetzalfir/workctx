# Docs facade/stale claims (explorer sweep, 2026-08-02)

Verified against src/workctx at master `373ce82`. Grouped by file; `facade` = promises
unimplemented behavior; `stale` = superseded by shipped waves.

## README.md
- :18 scaffold framing — stale (Waves 1-3 shipped engine/MCP/installer).
- :27, :104 "small executable CLI foundation" — stale understatement; omits MCP,
  transactions, projections, retrieval.
- :58-59 `workctx agent open …` shown as target experience — facade: no `agent` CLI
  group (API-only: session.py:31).
- :62 "later in Phase 1, the same workctx MCP server" — stale: `mcp serve` shipped.
- :9-12 and :84-94 evidence lifecycle (inbox → process → archive → outbox drafts) —
  facade: WP-310 in flight, WP-420 unbuilt; section is unhedged.
- :119 `uv sync --all-groups` — misses `--extra mcp`; contributor hits exit 5 on
  `mcp serve` (mcp.md:18 has the correct line).
- :136-138 adapter file list (.codex/config.toml, .mcp.json, .gemini/commands|settings)
  — stale vs agent-adapters.md: those are detection markers (detection.py:33-41), not
  generated; MCP config not_implemented; Gemini native form is .gemini/skills/.

## CHANGELOG.md
- :5-13 Unreleased lists only the scaffold — stale: no entries for Waves 1-3.

## docs/reference/
- cli-envelope.md:9-11 JSON-capable command list — stale: missing index rebuild,
  ref ×3, context-pack. :88-92 "WP-200 will insert registry step 3" — stale: landed
  (presentation/context.py:38-49).
- canonical-store.md:285-286 step-3 wiring "deferred" — stale: landed.
- projections.md:171-173 "incremental updates + freshness deferred to WP-300" — stale:
  freshness landed (sqlite/freshness.py); WP-300 shipped WITHOUT incremental updates →
  incremental projection updates currently have NO stated owner. :150-151 "later
  packages" lock integration — stale.
- agent-adapters.md:150-151 MCP config gated on "WP-330 defines identity" — stale
  rationale: identity exists (mcp.md:22-45); state remains not_implemented (LEAD-W2
  closes it).
- skill-adapters.md:3,21,356-357 normative future tense about WP-320 — stale: installer
  shipped.

## docs/concepts.md
- :9 artifact hashing/dedup as present capability — facade until WP-310.

## docs/guides/
- evidence-processing.md:5-18 twelve-step workflow unhedged — facade for steps 1-2, 10,
  and views half of 11 (WP-310/WP-400).
- quickstart.md:21 "after agent installers are implemented" — stale: installer engine
  shipped; the gap is the CLI group.
- multiple-contexts.md:14-16 plugin instances + secret references per context —
  facade: no plugin system (Phase 3), secrets only a validation rule.
- context-layout.md:9 04_views "generated views" — facade until WP-400.

## Clean
- validation-diagnostics.md (all 32 codes match diagnostics.py/engine.py) and
  transactions.md (every named export present).

## Cross-cutting
- Dominant pattern: temporal WP-reference drift. Cheapest re-audit: grep `WP-\d{3}`
  across docs/reference/ after each wave.
- ROADMAP.md lacks per-item status markers.

## Disposition
Docs refresh = dedicated package AFTER W1/W2/WP-310 integrate (it must describe the
post-wiring truth). Assigned to a Claude writing agent per the orchestration mode;
cli-envelope.md command table is W1's (exclude from the docs package).
