# Brief: WP-840 — Outbound voice and conversation language (C-223)

Codex worker, worktree `.worktrees/WP-840`, branch `agent/WP-840-outbound-voice`.
No commits; final message = report. `.agents/` read-only. Read C-223 in
`.agents/status/phase2-candidates.md` first, then the packaged
`draft-replies` skill, the three bridges, and the C-218/C-219/C-222
sections they carry (extend in place; no competing rules).

## Contract

1. `draft-replies` skill: add a "Voice and language" contract — drafts
   are written in the OPERATOR'S first person as if the operator typed
   them; natural, human register appropriate to the relationship; no
   assistant phrasing, no AI disclaimers, no sign-offs the operator would
   not write, no meta commentary inside the message body. Language:
   inspect the target conversation's recent messages (channel, chat, or
   thread) and match its established language; when mixed, follow the
   current exchange and the recipient's latest relevant message; never
   infer language from a person's name or company. Fallback when nothing
   is detectable: explicit operator instruction for that message > the
   operator's configured default in context or user `instructions.md`
   > English. Replace the weaker "recipient's language" wording in
   Procedure/Invariants with this contract (keep the invariant list
   coherent). The existing evidence-safety and never-deliver boundaries
   stay untouched.
2. Bridges (3) and the canonical context template `AGENTS.md`: ONE
   sentence each, in the existing voice, stating the first-person +
   conversation-language rule for any outbound message drafted or sent
   through Work Context (outbox send included).
3. Docs: the drafting/outbox reference page names the rule briefly.

## Allowed paths

`src/workctx/resources/agent_kit/skills/draft-replies/SKILL.md`, the
three bridges, `src/workctx/resources/context_template/AGENTS.md` (+
`scripts/sync_context_template.py` for the mirror), `docs/` drafting or
outbox reference page only, `tests/agents_setup/**` and
`tests/test_skills.py` (content assertions). Nothing else; `.agents/`
mirrors and the template hash history constant are lead-reconciled.

## Tests

Content assertions pin the first-person phrase, the detect-from-recent-
messages phrase, the never-infer-from-name phrase, and the fallback order
in the skill, all three bridges, and the template; skill lint green;
existing tests green; full gate where the sandbox allows, limits
declared.
