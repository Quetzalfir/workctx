# Declarative connectors

Declarative connectors provide the generic Level 1 snapshot path for HTTP services,
including private or internal services that do not justify shipped service-specific code.
An operator adds a YAML manifest; the runtime performs bounded read-only GET requests and
routes every response through the normal inbox registration and quarantine pipeline.

Connector version 1 has no external-write operations, background scheduler, CLI command,
MCP surface, pagination engine, or response parser. The `schedule` field records future
automation metadata only.

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
    schedule: "0 8 * * 1-5"
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
(default `application/json`); and schedule metadata. Paths cannot be absolute URLs, carry
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

from workctx.connectors import load_manifests, sync

root = Path("/path/to/one/context")
manifests = load_manifests(root)
result = sync(root, "rally-interno", snapshot_id="active-work")
```

`sync(root, name, *, snapshot_id=None, transport=None, clock=None)` fetches every declared
snapshot unless one id is selected. `transport` is the `httpx` test seam; production calls
normally omit it. `clock` supports deterministic tests and UTC naming. `SyncResult` and its
per-snapshot records are frozen Pydantic models and serialize directly for a later CLI
envelope without carrying request, response, transport, or secret objects.
