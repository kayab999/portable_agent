# ADR 005 — ACP capability minimization

Status: accepted

## Decision
The ACP v1 server implements session/prompt/cancel/update only. It rejects
client MCP servers and exposes no filesystem, terminal, image or audio
capabilities.

## Rationale
Every advertised ACP capability is a new attack surface. The agent already has
its own sandboxed tools; bridging the client's filesystem would bypass them.

## Consequences
Some editors will report missing capabilities. This is intentional and
documented in `docs/ACP.md`.
