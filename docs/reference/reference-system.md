# Reference system

Canonical entities use:

```text
workctx://<context-id>/<entity-type>/<entity-id>
```

Artifacts use immutable content addressing:

```text
artifact://sha256/<digest>
```

Repository findings use an immutable commit:

```text
repo://<repo-id>@<commit>/<path>#L<start>-L<end>
```

Material statements should resolve to atomic observations and then to a source locator. Supported locator families include line, page, time, message, image region, table range, JSON pointer, and repository range.

Typed relationships make retrieval intentional. Use `related_to` only when a more precise relation is not known.

The full design is in `.agents/plan/initial/03-reference-and-retrieval-model.md`.
