# Initial risk register

| ID | Risk | Probability | Impact | Mitigation / validation |
| --- | --- | --- | --- | --- |
| R-001 | The project becomes another note convention instead of deterministic software. | Medium | High | Prioritize transaction, validation, resolver, and rebuild behavior before advanced integrations. |
| R-002 | Reference model is too complex for ordinary use. | Medium | High | Generate references automatically; keep human Markdown readable; test copy/paste workflows. |
| R-003 | Reference model is too weak to recover precise context. | Medium | High | Require atomic observations and source locators for material claims; acceptance tests trace to source. |
| R-004 | Multiple agents create incompatible architecture. | High | High | Lead-owned contracts, path ownership, ADRs, worktrees, independent review, integration gates. |
| R-005 | Agent-specific adapters drift. | High | Medium | Canonical skills and generated adapters with contract tests. |
| R-006 | Windows filesystem behavior breaks atomicity or locking. | Medium | High | Early ADR and cross-platform failure-injection tests. |
| R-007 | SQLite becomes an accidental source of truth. | Medium | High | Destructive rebuild acceptance test from canonical files. |
| R-008 | Prompt injection in evidence causes unsafe actions. | Medium | Critical | Untrusted-data boundary, quarantine, deterministic tools, approval, security tests. |
| R-009 | Context data leaks between companies. | Low/Medium | Critical | Explicit context handle, isolated state, URI enforcement, denial tests. |
| R-010 | Secrets enter Markdown or agent reports. | Medium | Critical | Keyring references, validation scanner, redaction, CI scanning, contributor rules. |
| R-011 | Scope expands into UI/connectors before core is stable. | High | High | Phase boundary and release definition; reject out-of-phase work orders. |
| R-012 | Generated operational views become stale or manually edited. | Medium | Medium | Generated headers, validation, rebuild command, canonical task/claim source. |
| R-013 | LLM extraction produces plausible but unsupported claims. | High | High | Fact/inference distinction, exact source locators, confidence, proposal review, contradiction checks. |
| R-014 | The CLI is too hard for non-developers. | Medium | High | Strong defaults, profiles, doctor, actionable errors, five-minute acceptance workflow; UI deferred but planned. |
| R-015 | Optional graph/code tools are treated as mandatory. | Medium | Medium | Core acceptance runs with no optional plugin installed. |
| R-016 | Open-source examples leak legacy business context. | Low | Critical | Fictional fixtures, automated string checks, manual release review. |
| R-017 | MCP surface changes break agents. | Medium | High | Versioned schemas, contract tests, deprecation policy. |
| R-018 | Work-order reports become bureaucracy without quality value. | Medium | Medium | Keep contracts bounded and generated; require only load-bearing evidence and review. |
