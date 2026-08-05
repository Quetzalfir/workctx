# Outbox send

`workctx outbox send` is the alpha's only external-write surface. It delivers exactly one
canonical draft to exactly one GitHub issue or pull request, only after an exact preview and
one operation-specific approval. It does not batch, schedule, retry, or select recipients on
the operator's behalf.

Saving a draft and sending it are separate approvals. Approval used by `draft_save`,
`save_draft`, or any other local transaction never authorizes delivery.

## GitHub target

The only v1 channel is `github`. Its target is a value, not a URL:

```text
owner/repository#number
```

The owner is a 1–39 character GitHub-style alphanumeric/hyphen name, the repository is a
1–100 character alphanumeric/dot/underscore/hyphen name other than `.` or `..`, and the issue
or pull-request number is a positive decimal integer without leading zeroes. Values containing
schemes, hosts, additional path segments, query strings, or fragments other than the
separating `#number` are refused. The GitHub adapter posts the draft body to the issue-comments
endpoint, which is also the comment endpoint for pull requests.

`DraftPayload.recipient_uri` remains the canonical local person intended by the author. The
GitHub target is the single resolved external recipient for this delivery operation; neither
field accepts a list.

## Preview and fingerprint

The public API is:

```python
preview_send(root, draft_id, channel, target) -> SendPreview
send(
    root,
    draft_id,
    channel,
    target,
    *,
    approved,
    fingerprint,
    transport=None,
    clock=None,
) -> SendResult
```

`SendChannel` is the adapter protocol. `GitHubSendChannel` is the only registered adapter.

Preview performs no authentication, network call, or canonical mutation. `SendPreview`
contains the channel, exact target, resolved recipient display, full outgoing body, SHA-256 of
the complete canonical draft file, and the send fingerprint. The fingerprint is:

```text
sha256(draft_content_hash + channel + target)
```

The UTF-8 strings are concatenated in that order, with no separators, and the result is
reported as `sha256:<lowercase-hex>`. The draft hash has a fixed-width grammar, the channel is
from a closed vocabulary, and the target has the closed grammar above.

`send` re-reads canonical state. It refuses unless `approved is True` and the supplied
fingerprint exactly matches the current draft hash, channel, and target. Editing any canonical
draft field after preview, changing the channel, or swapping the target invalidates approval.
The body is scanned again with `contains_possible_secret` after fingerprint verification and
immediately before authentication and transport.

## CLI approval flows

Preview is the default:

```text
workctx outbox send DRAFT-20260804-status-01 \
  --via github --target fictional-org/status#17
```

In JSON mode, preview returns `operation: preview` and the exact fingerprint. A script that
sends must echo that reviewed value:

```text
workctx outbox send DRAFT-20260804-status-01 \
  --via github --target fictional-org/status#17 \
  --yes --fingerprint sha256:<reviewed-value> --json
```

`--yes --json` without `--fingerprint` is a non-mutating usage failure. In human mode,
`--yes` renders a fresh full preview and asks for confirmation in the same invocation. The
confirmed in-memory fingerprint is then passed to `send`, which re-reads and compares it once
more. Declining the prompt performs no external or canonical write.

## Authentication containment

The fixed GitHub token chain is:

1. ADR 0013 secret reference `github-token` (therefore
   `WORKCTX_SECRET_GITHUB_TOKEN` before the machine-global `workctx` keyring entry);
2. conventional `GITHUB_TOKEN` environment variable;
3. `gh auth token`, invoked as a fixed argument vector with captured stdout and stderr.

No token is accepted on the workctx command line or stored in a context. Fallback subprocess
output is never logged. The resolved value remains an opaque `SecretValue`; `reveal()` is used
only while cloning the logical credential-free HTTPX request at the transport boundary. The
client does not follow redirects and does not retry. Results, diagnostics, tracebacks, HTTPX
logs, canonical files, and ledger events contain no token value.

## Successful delivery and provenance

GitHub must return HTTP 201 plus a positive comment ID and a matching canonical
`github.com/...#issuecomment-...` URL. A successful response is followed by one approved local
transaction that:

- changes `delivery_state` from `unsent` to `sent`;
- replaces the `unsent` tag with `sent`;
- records `delivery.channel`, `delivery.target`, `delivery.remote_comment_id`,
  `delivery.remote_comment_url`, and `delivery.sent_at`;
- preserves the exact body and original `created_at`;
- appends one hash-chained ledger event whose source references include the draft URI.

The delivery timestamp and transaction engine share the same injected clock. The transaction
pins the previewed draft hash as both its update preimage and an explicit precondition, and it
pins the rendered sent document as a postcondition. A sent draft cannot be revised back to
unsent, previewed for delivery, or sent again.

## Failure semantics and reconciliation

Authentication, connection, timeout, non-201 status, oversized/malformed receipt, fingerprint,
approval, target, and secret-gate failures occur without a canonical draft or ledger change.
Remote response bodies, headers, exception text, and reflected credential material are not
retained in diagnostics. Workctx performs no automatic retry because a connection or response
failure can have an unknown remote outcome; inspect the target before deciding whether another
operation is safe.

GitHub and the local filesystem cannot participate in one distributed transaction. The remote
comment must exist before its ID and URL can be committed locally. If GitHub succeeds but the
single local transaction returns a failure, workctx raises an explicit reconciliation-required
failure containing the safe remote comment ID and URL and instructs the operator not to resend.
A process stop in that interval can leave no local receipt at all; the operator must inspect the
target before any retry. This is the narrow residual crash window; it is not reported as a clean
delivery failure or silently retried.

There is no MCP send tool in this change. There are also no email, Teams, browser, plugin,
attachment, batch, send-all, scheduling, or background-delivery paths.
