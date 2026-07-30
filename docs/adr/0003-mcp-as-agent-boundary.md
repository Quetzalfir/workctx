# ADR 0003: MCP as the agent tool boundary

- Status: accepted
- Date: 2026-07-30

## Context

Codex, Claude Code, Gemini CLI, and future agents use different instruction and extension mechanisms.

## Decision

Expose stable product operations through a local MCP server and generate thin agent-specific bootstrap adapters. Keep canonical skills in the repository.

## Consequences

- application services must be independent from MCP;
- MCP schemas become compatibility contracts;
- agent adapters can change without changing canonical data;
- ordinary CLI use remains available when an agent has no MCP support.
