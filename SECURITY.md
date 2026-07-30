# Security policy

## Reporting a vulnerability

Do not open a public issue for a vulnerability that could expose secrets, private evidence, cross-context data, or unsafe external actions. Use the repository's private security reporting channel once the public repository is created.

Until then, report privately to the project maintainer.

## Security boundaries

Work Context OS treats each context as an independent security boundary. Implementations must prevent accidental reads, searches, indexes, logs, credentials, and tool calls from crossing that boundary.

## Secrets

Secret values are forbidden in:

- context repositories;
- `.env` files committed to source control;
- Markdown or YAML knowledge files;
- logs and audit payloads;
- agent prompts and reports;
- test fixtures.

Use operating-system keyrings or external secret managers and store only opaque references in context configuration.

## External actions

External writes—messages, issue updates, documentation publication, infrastructure changes, and production operations—must require explicit approval by default and must be audited.
