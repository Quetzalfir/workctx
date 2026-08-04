# Secret references

Work Context stores secret names, never secret values, in user-controlled workspace files.
Canonical entities and integration configuration use the `secret_ref` convention:

```yaml
secret_ref: github-token
```

The name is lowercase kebab-case, contains 1–64 ASCII letters or digits separated by single
hyphens, and carries no credential material. Validation accepts a bare reference such as the
example above and continues to reject secret-shaped inline assignments.

## Resolver chain

`resolve(name)` checks two layers in a fixed order:

1. `WORKCTX_SECRET_<UPPER_SNAKE_NAME>` in the current process environment;
2. the operating-system credential store through keyring, under service namespace `workctx`.

For example, `github-token` maps to `WORKCTX_SECRET_GITHUB_TOKEN`. An environment entry wins
when both layers contain the name. A missing name raises `SecretNotFoundError`; the error names
the reference but never a value. If the environment does not contain the name and keyring or
its platform backend is unavailable, the operation reports an unavailable dependency. This is
the documented env-only mode for minimal installations and CI.

Secret names are deliberately machine-global in v1. They are not scoped by context ID. Use
distinct, project-prefixed names when two contexts need different credentials for the same
provider. Per-context keyring scoping requires a later compatibility decision.

`resolve` returns `SecretValue`, not `str`. Its string and representation forms and its JSON and
Pydantic serializers emit a redaction marker. Pickling is blocked. `reveal()` is the single API
that returns the wrapped text and should be called only at the final authorized consumer.

## CLI

`secret` commands do not require an active context because names are machine-global:

```text
workctx secret set github-token
workctx secret set github-token --from-env EXISTING_GITHUB_TOKEN
workctx secret check github-token
workctx secret list
workctx secret unset github-token
workctx secret import ../legacy.env --shred
```

- `set` accepts no value argument. It reads from a masked interactive prompt or from the named
  environment variable and writes only to the OS credential store.
- `check` reports whether the name resolves and whether `env` or `os-store` wins.
- `list` reports names and per-layer presence only. With no usable keyring backend, OS-store
  presence is `null` and the command emits a `SECRET_BACKEND_UNAVAILABLE` warning.
- `unset` removes only the OS-store entry. It warns when an environment override still resolves.
- `import` parses a UTF-8 dotenv file completely before storing any pair. Shell-style names are
  normalized from snake case to lowercase kebab-case. Blank lines, full-line comments, trailing
  comments, single-quoted values, and double-quoted values with basic escapes are supported.
  Malformed or colliding names refuse the whole file and identify only the line number.

Interactive import asks whether to remove the source after a successful import. JSON mode never
prompts and requires either `--shred` or `--keep`. `--shred` overwrites, flushes, truncates, and
deletes one regular file; it refuses symlinks. Storage media, copy-on-write filesystems, backups,
and synchronization services can retain prior blocks or copies, so deletion is best effort rather
than a physical-erasure guarantee. The source path may be outside a context and is never copied
into one.

## Names-only index

Keyring cannot enumerate credentials portably. Work Context therefore maintains
`secret-names.json` under the platform-specific `workctx` user configuration directory. The file
contains a schema version and sorted secret names only. It is atomically replaced under a
cross-process lock and never contains values.

The OS store remains authoritative. A stale index can omit an existing entry or retain a deleted
name; `check` and exact-name resolution still consult the resolver layers directly, while `list`
shows backend presence for indexed names. If an OS-store write succeeds but the subsequent index
update fails, `set` reports failure and repeating the same command repairs the index without
printing or persisting the value elsewhere.

## Safety boundary

Do not put plaintext credentials in a context, repository, local-only workspace file, command
argument, log, diagnostic bundle, issue, report, or agent conversation. Local repositories are
commonly indexed, synchronized, and pushed. Use the masked `set` flow for human-assisted agent
registration, `--from-env` for controlled scripting, or `import` to migrate an existing dotenv
file directly into the OS credential store.
