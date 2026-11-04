# ADR 004 — A2A is a non-goal

Status: accepted

## Decision
Bonsai Portable will not implement agent-to-agent federation in 1.0.

## Rationale
A2A introduces remote identity, authorization, transport and multi-tenant
isolation requirements that contradict the local single-user scope.

## Consequences
- No remote agent discovery or delegation.
- Subagents remain local Markdown definitions and are opt-in.
