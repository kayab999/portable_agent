# ADR 003 — Plugins are declarative only

Status: accepted

## Decision
Plugin adapters parse manifests, skills, agents and MCP declarations. They
never import or execute plugin code.

## Rationale
Executing foreign JS/TS/Python inside the agent process would erase every
sandbox and permission boundary. Declarative parsing keeps plugins in the
untrusted-content category.

## Consequences
- Plugins cannot ship executable hooks.
- Tool declarations are upper bounds intersected with global/persona policy.
