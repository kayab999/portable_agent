#!/usr/bin/env python3
"""Tiny modern MCP (2026-07-28) stdio server used by tests."""
import json
import sys

PROTOCOL_VERSION = "2026-07-28"
MODE = sys.argv[1] if len(sys.argv) > 1 else "modern"
TOOLS = [
    {
        "name": "echo",
        "description": "Echo the provided text",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "hidden",
        "description": "Only exposed when the allowlist permits it",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def send(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def error(request_id, code, message, data=None):
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }
    if data is not None:
        payload["error"]["data"] = data
    send(payload)


def validate_meta(params):
    meta = params.get("_meta") if isinstance(params, dict) else None
    if not isinstance(meta, dict):
        return "missing _meta"
    version = meta.get("io.modelcontextprotocol/protocolVersion")
    if version != PROTOCOL_VERSION:
        return "bad protocolVersion"
    capabilities = meta.get("io.modelcontextprotocol/clientCapabilities")
    if not isinstance(capabilities, dict):
        return "missing clientCapabilities"
    return None


for line in sys.stdin:
    try:
        message = json.loads(line)
    except ValueError:
        continue
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        error(
            request_id,
            -32022,
            "Unsupported protocol version",
            {"supported": [PROTOCOL_VERSION], "requested": None},
        )
        continue

    if request_id is None:
        continue

    meta_error = validate_meta(params)
    if meta_error:
        error(request_id, -32602, meta_error)
        continue

    if method == "server/discover":
        if MODE == "reject-version":
            error(
                request_id,
                -32022,
                "Unsupported protocol version",
                {"supported": ["1900-01-01"], "requested": PROTOCOL_VERSION},
            )
            continue
        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "resultType": "complete",
                    "supportedVersions": [PROTOCOL_VERSION],
                    "capabilities": {"tools": {}},
                    "_meta": {
                        "io.modelcontextprotocol/serverInfo": {
                            "name": "modern-fake",
                            "version": "1",
                        }
                    },
                },
            }
        )
    elif method == "tools/list":
        result_type = "weird" if MODE == "unknown-result-type" else "complete"
        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"resultType": result_type, "tools": TOOLS},
            }
        )
    elif method == "tools/call":
        if MODE == "input-required":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "resultType": "input_required",
                        "inputRequests": {
                            "confirm": {
                                "method": "elicitation/create",
                                "params": {"message": "confirm?"},
                            }
                        },
                    },
                }
            )
            continue
        name = params.get("name")
        if name == "echo":
            text = (params.get("arguments") or {}).get("text", "")
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "resultType": "complete",
                        "content": [{"type": "text", "text": text}],
                    },
                }
            )
        else:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "resultType": "complete",
                        "isError": True,
                        "content": [{"type": "text", "text": "boom"}],
                    },
                }
            )
    else:
        error(request_id, -32601, "method not found")
