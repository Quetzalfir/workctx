# Declarative connectors

Declarative connectors provide the generic Level 1 snapshot path for HTTP services,
including private or internal services that do not justify shipped service-specific code.
An operator adds a YAML manifest; the runtime performs bounded read-only GET requests and
routes every response through the normal inbox registration and quarantine pipeline.

Connector version 1 has no external-write operations, resident scheduler, MCP surface,
pagination engine, or response parser. Its CLI provides manual and due-aware batch commands;
the operating system invokes those commands on a schedule. Workctx does not run a daemon.

## Manifest location and contract

Store one manifest at `07_connectors/<name>.yaml` inside an isolated context. The filename
must equal the manifest `name`. `load_manifests(root)` loads `.yaml` files in deterministic
name order, rejects malformed or duplicate-key YAML, validates the typed contract, and
refuses duplicate connector names or duplicate snapshot ids. Each manifest is capped at
1 MiB and must be a stable regular UTF-8 file inside the context boundary.

The hand-maintained public contract is
`schemas/connector-manifest.schema.json` (JSON Schema Draft 2020-12).

```yaml
schema_version: 1
name: rally-interno
base_url: https://rally.example.test/api/
secret_ref: rally-interno-token
auth_style: header:X-Fictional-Key
timeout_seconds: 30
max_bytes: 10485760
snapshots:
  - id: active-work
    path: /snapshots/active
    query:
      team: alpha
      limit: 50
    accept: application/json
    schedule: daily
```

Manifest fields:

| Field | Contract |
| --- | --- |
| `schema_version` | Required integer `1`. |
| `name` | Required lowercase kebab name, 1-64 characters, unique in the context. |
| `base_url` | Required absolute HTTP(S) URL without credentials, query, or fragment. HTTPS is the default requirement. |
| `allow_insecure_http` | Must be explicitly `true` for a plain HTTP base URL. It does not weaken redirect checks. |
| `secret_ref` | Optional ADR 0013 lowercase kebab secret name. It must be paired with `auth_style`. |
| `auth_style` | `bearer`, `header:<Name>`, or `query:<param>`; it must be paired with `secret_ref`. |
| `snapshots` | Non-empty ordered list of unique snapshot declarations. |
| `timeout_seconds` | Global request timeout; default 30, greater than zero, maximum 300. |
| `max_bytes` | Hard per-response byte cap; default 10 MiB, maximum 100 MiB. |

Each snapshot requires a lowercase kebab `id` and a relative `path`. A snapshot may also
declare a query mapping of scalar strings, numbers, and booleans; an `Accept` value
(default `application/json`); and an optional `schedule` of `hourly`, `daily`, or `weekly`.
Omitting `schedule` makes the snapshot manual-only. Paths cannot be absolute URLs, carry
their own query or fragment, or contain traversal segments. A `query:` authentication
parameter cannot also appear in the snapshot's ordinary query mapping.

## Authentication and secret containment

Secret references resolve at each `sync` call through `workctx.secrets` (environment first,
then the OS credential store). The opaque `SecretValue` is revealed only while mutating the
outgoing `httpx.Request`:

- `bearer` writes `Authorization: Bearer <value>`;
- `header:<Name>` writes the value to the named request header;
- `query:<param>` writes the value to that request URL parameter.

Connection-framing headers (`Host`, `Connection`, `Content-Length`, and
`Transfer-Encoding`) cannot be selected as custom authentication headers.

The runtime does not log requests. Request objects, response objects, transports, and raw
secret text are absent from results and typed failures. Response bytes and the persisted
content-type value are checked for reflected authentication material before any filesystem
write; a reflection is refused with a content-free `ConnectorSecretExposureError`.

HTTPX receives a credential-free logical request for its normal client lifecycle and INFO
logging. The runtime clones that request at the transport boundary and reveals the secret
only while mutating the clone that is actually sent, including for query authentication.

Manifests, results, errors, snapshots, and provenance sidecars never contain resolved
secret values. A query-auth provenance entry records only the reference name in an explicit
names-only marker:

```json
{
  "query": {
    "api_key": {
      "secret_ref": "rally-interno-token"
    }
  }
}
```

## HTTP behavior

The runtime uses a synchronous `httpx.Client` with the manifest timeout, environment proxy
discovery disabled, and automatic redirects disabled. It follows at most three manual
redirects when the target hostname is unchanged. Cross-host targets, credentialed targets,
missing or invalid locations, and HTTPS-to-HTTP downgrades are refused before constructing
another authenticated request.

Only 2xx responses become snapshots. Connection, timeout, status, redirect, size, secret
reflection, filesystem, and registration failures are typed and content-free. Status
failures include only the connector name, snapshot id, and status code; transport messages,
headers, and response bodies are discarded.

Response streams are read as raw bytes (`iter_raw`) and aborted as soon as the next chunk
would exceed `max_bytes`. The runtime does not decode, decompress, parse, render, or execute
the response. A pre-buffered custom transport response follows the same byte cap.

## Inbox files and provenance

A successful response is written verbatim to:

```text
00_inbox/raw/<connector>-<snapshot>-<YYYYMMDDTHHMMSSffffffZ>.<ext>
```

The extension is selected only from the response content type:

- `application/json` and `+json` types use `.json`;
- `application/xml`, `text/xml`, and `+xml` types use `.xml`;
- `text/*` types use `.txt`;
- missing or other types use `.bin`.

The adjacent `<snapshot-file>.provenance.json` sidecar records schema version, connector
system name, credential-free base URL, declared path and query, HTTP status, response
content type, byte count, and UTC retrieval time. The sidecar is passed to `RegisterRequest`
as a real ingestion sidecar, so it is hashed, preserved, moved with quarantine, and retained
in artifact metadata.

Per decision D-049, connector snapshots use the existing frozen ingestion vocabulary:

```text
source_type = external_snapshot
source_origin = connector:<manifest-name>
```

For the example above, `source_origin` is `connector:rally-interno`. No
`connector_snapshot` enum value exists or is required.

Registration uses `IngestionService.register_batch`. A clean response normally becomes a
pending registered artifact. Prompt injection, possible secrets, executable signatures,
unsupported media, and other existing ingestion findings quarantine the response normally;
they are per-snapshot outcomes, not connector-engine errors. A repeated primary content
hash is returned as a normal duplicate outcome referencing the existing artifact.

## Service API

```python
from pathlib import Path

from workctx.connectors import load_manifests, status, sync, sync_all

root = Path("/path/to/one/context")
manifests = load_manifests(root)
result = sync(root, "rally-interno", snapshot_id="active-work")
batch = sync_all(root, due_only=True)
schedule_status = status(root)
```

`sync(root, name, *, snapshot_id=None, transport=None, clock=None)` fetches every declared
snapshot unless one id is selected. A named sync is always manual: it runs even when the
snapshot is not due or has no schedule. `sync_all(root, *, due_only=False, transport=None,
clock=None)` returns an ordered `SyncAllResult` with one typed outcome per connector. One
connector failure does not prevent later connectors from running. `status(root, *,
clock=None)` returns one typed schedule row per connector and snapshot.

`transport` is the `httpx` test seam; production calls normally omit it. `clock` supports
deterministic due evaluation, tests, and UTC naming. Results are frozen Pydantic models and
serialize directly for CLI envelopes without carrying request, response, transport, or
secret objects.

## Due evaluation and last-success state

Schedule intervals are fixed durations: `hourly` is 1 hour, `daily` is 24 hours, and
`weekly` is 7 days. A scheduled snapshot is due exactly when:

```text
now - last_success >= schedule_interval
```

The equality boundary is due. A scheduled snapshot with no usable last-success timestamp is
due. A snapshot without `schedule` is manual-only and is never selected by `--due`; an
explicit named sync or `sync --all` without `--due` still runs it.

Successful snapshots update the machine-local advisory file
`98_state/connectors/last-sync.json`. It stores UTC timestamps by connector and snapshot:

```json
{
  "schema_version": 1,
  "connectors": {
    "rally-interno": {
      "active-work": "2026-08-04T12:00:00Z"
    }
  }
}
```

Updates use a flushed temporary file in the same directory followed by atomic replacement.
This file is not canonical knowledge: it is rebuild-safe and may be deleted. Missing,
corrupt, oversized, or otherwise unusable state is never a connector error; it means every
scheduled snapshot is due. A successful manual sync also records its selected snapshots.

## CLI and operating-system scheduling

The connector commands are:

```text
workctx connector list
workctx connector sync <name> [--snapshot <id>]
workctx connector sync --all [--due]
workctx connector status
```

`--all` and a positional connector name are mutually exclusive. `--due` is valid only with
`--all`. Batch synchronization reports every per-connector outcome. It exits `0` when no
connector failed, including when nothing is currently due, and exits `1` when any connector
failed; the failure envelope retains successful and skipped outcomes and includes one safe
diagnostic per failed connector. `connector status` reports `schedule`, `last_success`, and
`due_now` for each connector and snapshot.

Run the due-aware command at least hourly and let its interval math decide what work is due.
For Windows Task Scheduler, replace both executable and context paths with absolute paths
appropriate to the machine:

```powershell
schtasks.exe /Create /TN "workctx connector sync" /SC HOURLY /MO 1 /TR "workctx connector sync --all --due --json --context C:\workctx\example" /F
```

An equivalent hourly crontab entry is:

```cron
0 * * * * /usr/local/bin/workctx connector sync --all --due --json --context /srv/workctx/example
```

These recipes invoke a terminating batch command. They do not install or depend on a
workctx background process.

## Real-world read example: GitHub issues

The following manifest is intentionally a **real-world example**, unlike the fictional
`.example.test` hosts required in automated tests. It reads the public GitHub REST issues
endpoint for `octocat/Hello-World`; replace the repository path for actual use. GitHub's
issues endpoint can also return pull requests, so downstream evidence processing must retain
the response verbatim rather than assume every item is an issue.

```yaml
schema_version: 1
name: github-issues
base_url: https://api.github.com/
secret_ref: github-token
auth_style: bearer
timeout_seconds: 30
max_bytes: 10485760
snapshots:
  - id: open-issues
    path: /repos/octocat/Hello-World/issues
    query:
      state: open
      per_page: 100
    accept: application/vnd.github+json
    schedule: hourly
```

The repository-wide GitHub authentication chain is the ADR 0013 `github-token` secret
reference, then the conventional `GITHUB_TOKEN` environment variable, then `gh auth token`
where a GitHub-aware operation supports those fallbacks. The generic declarative connector
runtime itself resolves its declared `secret_ref` through ADR 0013
(`WORKCTX_SECRET_GITHUB_TOKEN`, then the OS credential store). If the token currently exists
only in `GITHUB_TOKEN` or `gh`, seed the reference before using this manifest, for example
with `workctx secret set github-token --from-env GITHUB_TOKEN`.
