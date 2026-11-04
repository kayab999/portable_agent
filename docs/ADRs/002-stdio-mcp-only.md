# ADR 002 — stdio MCP only

Status: accepted

## Decision
Only stdio MCP servers are supported. Remote HTTP/SSE/OAuth MCP transports are
an explicit non-goal for 1.0.

## Rationale
A local portable agent should not silently gain network egress or OAuth token
handling. stdio keeps the trust boundary local and makes the server lifecycle
observable.

## Consequences
- Some remote MCP servers cannot be used directly.
- Users can bridge them with their own local stdio proxy, outside our threat
  model.
