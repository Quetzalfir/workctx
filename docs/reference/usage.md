# Usage telemetry and advisory suggestions

Usage telemetry is an opt-in, per-context signal for deciding whether a supporting reference
deserves promotion and whether an inactive task or claim deserves review. It is never canonical
knowledge and never performs a promotion, closure, archival, or supersession itself.

## Enabling telemetry

Telemetry is disabled when `telemetry` is absent and when `telemetry.usage` is `false`:

```yaml
telemetry:
  usage: true
  promotion_uses: 5
  decay_days: 60
```

The defaults are five URI uses in a rolling 30-day window for promotion and 60 complete days
without activity for decay. `promotion_uses` accepts 1 through 1000; `decay_days` accepts 1
through 3650. A running long-lived client should be restarted after changing the opt-in flag so
its read adapters capture the new setting.

When disabled, read APIs perform one cached boolean check. They do not call the recorder, create
the usage directory, open a telemetry file, hash input, or emit a warning.

## Privacy contract

Enabled contexts append compact JSON lines to:

```text
98_state/usage/usage.jsonl
```

Each valid line contains a UTC timestamp, a bounded API name, and exactly one of:

- `target_uri`, for a durable URI consulted by the API; or
- `query_sha256`, the lowercase SHA-256 digest of search text or another non-URI query.

Search text is always hashed, even when it happens to look like a URI. URI values containing
userinfo or query parameters are hashed instead of stored. The recorder never receives or writes
document bodies, file contents, resolved secret values, result payloads, context packs, source
text, or raw search queries. Query hashes prevent accidental plaintext retention; they are not an
anonymity guarantee for easily guessed queries.

Recording is best-effort. Directory creation, rotation, serialization, permission, and append
failures are swallowed and reduced to one sanitized warning per context and process. A telemetry
failure therefore cannot change a read result or turn a successful read into an error.

The file is append-only between rotations. Before an append would take the current file over
5 MiB, workctx retains `usage.jsonl.1` and `usage.jsonl.2`, with `.1` newest. This state is
machine-local, should not be synchronized, and can be deleted at any time without losing
canonical data:

```text
delete 98_state/usage/
```

Deletion resets only advisory counters. The directory is recreated on the next enabled read.

## Window and threshold math

`workctx usage status` and `summarize()` fold all retained valid lines into per-URI counters for
rolling 7-, 30-, and 90-day windows. Boundaries are inclusive: an event exactly 30 days before
the injected/current clock belongs to the 30-day count. Future-dated and corrupt lines do not
contribute; corrupt lines are reported as a count and skipped. Query hashes are counted as query
events but never become promotion candidates.

Promotion applies only to non-`workctx://` durable references, because a `workctx://` target is
already a tier-1 entity. A reference becomes a promotion candidate when:

```text
uses_30d >= telemetry.promotion_uses
```

Decay evaluation considers open tasks (anything except `done` or `cancelled`) and current or
uncertain claims. For each record it takes the latest of its canonical activity timestamp, its
last retained URI use, and verified ledger activity that referenced or mutated its canonical
path. It becomes a decay candidate when:

```text
now - latest_activity >= telemetry.decay_days
```

Thus the exact fifth use triggers the default promotion threshold, and exactly 60 inactive days
triggers the default decay threshold. A recent usage event or ledger event suppresses decay.

## CLI and suggestion flow

```text
workctx usage status [--context PATH] [--json]
workctx usage evaluate [--context PATH] [--json]
workctx usage suggest --yes [--context PATH] [--json]
```

`status` reports the opt-in flag, retained byte counts, corrupt-line count, and rolling summary.
`evaluate` is read-only and returns typed promotion and decay candidates. Neither command creates
canonical files or audit events.

`suggest` requires explicit `--yes`. It converts each candidate into one open WP-680 suggestion
record through the approved transaction API. An already-open record with the same candidate kind
and target is not duplicated. These are advisory `engine_proposal` records because telemetry
alone cannot safely invent the concrete entity creation, task status update, or claim
supersession required by a valid `data_fix` proposal. Adopting the advisory record changes only
its reviewed lifecycle status; an operator must separately review and approve any concrete
curation transaction.

Telemetry files are data, not instructions. Tampering with them can at most influence advisory
candidate output; it cannot authorize or apply a canonical or external write.
