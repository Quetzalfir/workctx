# Browser-assisted capture

Some work tools cannot be reached through an API at all: a tenant policy may
disable API consent for mail or chat even though you use those tools in a
browser every day. Connectors stay deterministic and API-only, so when the
API path is closed the fallback is browser-assisted capture: at your explicit
request, the agent uses your already-open, already-authenticated browser
session to copy the content you name into the context inbox as ordinary
evidence. It is a documented agent workflow, not a connector; nothing about
it is scheduled or automatic.

## When this flow applies

Use browser-assisted capture when all three hold:

- your organization blocks API tokens or consent for the tool, so no
  connector can read it;
- the content is visible to you in a browser session you have already
  signed in to;
- you ask for a specific capture: which conversations, channels, or
  folders, and what time range.

When a deterministic connector can read the same source, prefer the
connector: its runs are reproducible and need no open browser.

## What the agent will and will not do

The agent will:

- confirm the scope with you first: tool, conversations or folders, time
  range, and target context;
- navigate read-only: open, scroll, expand, search, and use the tool's own
  export views;
- copy content verbatim, one file per conversation, thread, or day;
- save the files under `00_inbox/raw/` with names carrying source and
  date, then register them with `workctx inbox add` using a
  `browser:<tool>` source origin (for example `browser:teams`) and the
  real event date;
- record provenance: which tool, which signed-in account, and when the
  capture happened;
- restore any windows it moved or resized, following your personalization
  instructions.

The agent will not:

- activate anything with a side effect — send, reply, delete, edit, mark,
  react, forward, accept, or approve;
- sign in, touch credential prompts, or capture password-manager surfaces,
  session tokens, or one-time codes;
- paraphrase or summarize at capture time — interpretation happens later,
  during evidence processing, where it is traceable;
- take screenshots when text extraction is possible: text is diffable and
  searchable;
- wander outside the scope you confirmed, or capture into a different
  context.

This read-only guarantee is the heart of the flow: after a capture session,
the external tool is in exactly the state you left it, apart from whatever
the tool itself records about pages being viewed.

## Quarantine still protects you

A captured page is untrusted input like any other inbox artifact.
Registration runs the same ingestion guards as a file drop: suspected
prompt injection, embedded secrets, executable payloads, and unsupported
formats are quarantined instead of processed. If a captured thread contains
text addressed to an AI assistant ("ignore your instructions and..."), that
text is reported to you as a finding inside the evidence — it is never
followed. See [Evidence processing](evidence-processing.md) and
[Security and privacy](../security-and-privacy.md).

## A worked example

Everything below is fictional. At Aurora Corp the Microsoft 365 tenant
blocks API consent, but Teams works in the browser. The operator asks:
"Capture yesterday's thread about the rollout freeze from the Aurora Ops
channel."

1. The agent confirms the scope: Teams, channel *Aurora Ops*, the thread
   titled "Rollout freeze?", messages from 2026-08-03, into the `aurora`
   context.
2. In the already-signed-in Teams tab it opens the channel, expands the
   thread, and copies the messages verbatim — authors, timestamps, and text
   — into one file:
   `00_inbox/raw/teams-aurora-ops-rollout-freeze-2026-08-03.txt`.
3. It registers the file:

   ```text
   workctx inbox add 00_inbox/raw/teams-aurora-ops-rollout-freeze-2026-08-03.txt --source browser:teams --event-date 2026-08-03
   ```

4. From here the normal pipeline takes over: the artifact is pending in the
   inbox, and the next evidence-processing run extracts observations (for
   example "R. Vega: freeze approved until the hotfix ships") with
   message-level source locators pointing back to the raw capture.

The raw file is preserved unchanged; every claim extracted later must cite
it.

## The skill behind it

Agent clients installed by `workctx agent install` receive the portable
skill `capture-browser-evidence`. Its canonical source is
`.agents/skills/capture-browser-evidence/SKILL.md`.
