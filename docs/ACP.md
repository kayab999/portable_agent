# ACP v1 integration

Bonsai Portable exposes an **Agent Client Protocol v1** server over stdio:

```bash
python3 launcher.py --acp
```

stdout is reserved for JSON-RPC frames. Launcher diagnostics go to stderr.

## Capabilities

| Capability | Supported |
|---|---|
| `session/new`, `session/prompt`, `session/cancel` | Yes |
| `session/update` notifications | Yes |
| Load/resume session | No (explicit non-goal in rc.5) |
| Filesystem bridge | No |
| Terminal bridge | No |
| Client-provided MCP servers | Rejected (fail closed) |
| Image/audio content | No |
| Remote transport | No |

The server advertises only prompt and session capabilities. `mcpCapabilities`
is `{http:false, sse:false}`.

## Lifecycle

1. Client spawns `launcher.py --acp`.
2. Client calls `initialize`.
3. `session/new` returns an opaque session id.
4. `session/prompt` runs one turn through the same hooks, permissions, sandbox
   and persona allowlists as the gateway.
5. The server may emit `session/update` notifications.
6. `session/cancel` sets a cancellation flag; long model calls are observed at
   the next safe boundary.
7. EOF shuts the server down and joins prompt threads.

## Security boundaries

- No client-supplied MCP servers, filesystem roots or terminals.
- Prompt text is untrusted input, same as gateway chat.
- Tool execution uses the process sandbox, not the client's capabilities.
- Hooks and permissions are loaded from the same config as the gateway.
- Session count is capped (`--max-sessions`, default 8).

## Limitations

- Cancellation is coarse: it cannot interrupt an in-flight HTTP generation.
- There is no session persistence across process restarts.
- Editors expecting filesystem or terminal capabilities will see them absent;
  this is intentional.

## Testing

`tests/test_acp.py` covers framing, prompt validation, cancellation, session
caps, stdout purity and launcher integration.
