# Testing

Run the complete local gate:

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

Tests must use fictional fixtures and temporary directories. Never copy employer data into a test.

Critical controls include context isolation, path traversal, transaction rollback, reference resolution, secret redaction, and complete rebuild from canonical files.
