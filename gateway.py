#!/usr/bin/env python3
"""
gateway.py — Single-origin HTTP surface for Bonsai Portable.

Serves:
  - static web UI from web/
  - /api/identity, /api/personas, /api/tools, /api/rag, /api/config, /api/health
  - /api/session/unlock|status
  - reverse-proxy /v1/* and /health to llama-server (internal port)

Security (RC remediation):
  - Session bearer required for sensitive /api/* and /v1/* when not DEBUG
  - Body size cap; rate limit on expensive POSTs
  - RAG index confined to docs/workspace/datasets
  - Security headers on responses
  - POST /api/tools disabled unless allow_direct_tools (DEBUG)
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

from model_client import HttpModelClient as _HttpModelClient
from session_auth import SessionManager, Vault, extract_bearer

ROOT = Path(__file__).parent.resolve()
WEB_DIR = ROOT / "web"
DOCS_DIR = ROOT / "docs"
WORKSPACE_DIR = ROOT / "workspace"
DATASETS_DIR = ROOT / "data" / "KAYAB_DATASETS"

MAX_BODY_BYTES = 2 * 1024 * 1024  # 2 MiB

# Paths that never require a session token
PUBLIC_POST_PREFIXES = (
    "/api/session/unlock",
)


def _json_bytes(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def _security_headers() -> dict[str, str]:
    return {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'"
        ),
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Cache-Control": "no-store",
    }


class _RateBucket:
    def __init__(self, max_requests: int = 60, window_s: float = 60.0):
        self.max_requests = max_requests
        self.window_s = window_s
        self._hits: list[float] = []
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = time.time()
        with self._lock:
            self._hits = [t for t in self._hits if now - t < self.window_s]
            if len(self._hits) >= self.max_requests:
                return False
            self._hits.append(now)
            return True


class GatewayState:
    def __init__(
        self,
        *,
        llm_base: str,
        debug_mode: bool,
        is_unlocked: Callable[[], bool],
        rag_enabled: bool = False,
        rag_max_chars: int = 6000,
        rag_top_k: int = 3,
        persistence_enabled: bool = False,
        db_path: Path | None = None,
        vault: Vault | None = None,
        insecure_mode: bool = False,
        model_name: str = "bonsai",
        host: str = "127.0.0.1",
        port: int = 8080,
        mask_outputs: bool = True,
        sessions: SessionManager | None = None,
        auth_required: bool | None = None,
        allow_direct_tools: bool = False,
        agents_md_enabled: bool = True,
        skills_enabled: bool = True,
        mcp_enabled: bool = False,
        tool_registry=None,
        hooks=None,
        permissions=None,
        subagents_enabled: bool = False,
        subagent_index=None,
    ):
        self.llm_base = llm_base.rstrip("/")
        self.debug_mode = debug_mode
        self.is_unlocked = is_unlocked
        self.rag_enabled = rag_enabled
        self.rag_max_chars = max(0, int(rag_max_chars))
        self.rag_top_k = max(1, min(int(rag_top_k), 20))
        self.persistence_enabled = persistence_enabled
        self.db_path = Path(db_path) if db_path is not None else None
        self.vault = vault
        self.insecure_mode = insecure_mode
        self.model_name = model_name
        self.host = host
        self.port = port
        self.mask_outputs = mask_outputs
        self.sessions = sessions or SessionManager()
        self._session_unlocked = False
        # Default: auth required unless DEBUG
        if auth_required is None:
            self.auth_required = not debug_mode
        else:
            self.auth_required = auth_required
        # Direct /api/tools is dangerous; off by default (agent uses in-process tools)
        self.allow_direct_tools = allow_direct_tools or debug_mode
        self.agents_md_enabled = agents_md_enabled
        self.skills_enabled = skills_enabled
        self.mcp_enabled = mcp_enabled
        self.tool_registry = tool_registry
        self.hooks = hooks
        self.permissions = permissions
        self.subagents_enabled = subagents_enabled
        self.subagent_index = subagent_index
        self.rag_lock = threading.Lock()
        self.rate_limit = _RateBucket(max_requests=90, window_s=60.0)
        self.chat_rate_limit = _RateBucket(max_requests=30, window_s=60.0)
        self.unlock_rate_limit = _RateBucket(max_requests=5, window_s=60.0)

    def unlocked(self) -> bool:
        return bool(self.is_unlocked() or self._session_unlocked)

    def mark_unlocked(self) -> None:
        self._session_unlocked = True

    @property
    def allowed_origins(self) -> frozenset[str]:
        p = int(self.port)
        return frozenset(
            {
                f"http://127.0.0.1:{p}",
                f"http://localhost:{p}",
            }
        )

    @property
    def allowed_hosts(self) -> frozenset[str]:
        p = int(self.port)
        hosts = {
            f"127.0.0.1:{p}",
            f"localhost:{p}",
        }
        if self.host not in ("127.0.0.1", "localhost", "0.0.0.0", "::"):
            hosts.add(f"{self.host.lower()}:{p}")
        return frozenset(hosts)

    def issue_bootstrap_token(self) -> str:
        return self.sessions.issue()


def _confined_index_path(source: str | Path) -> Path | None:
    """Return resolved path if under docs/workspace/datasets, else None."""
    raw = str(source).strip()
    if not raw:
        return DOCS_DIR if DOCS_DIR.exists() else WORKSPACE_DIR
    try:
        candidate = Path(raw)
        if not candidate.is_absolute():
            if raw.startswith(("docs/", "docs\\", "data/", "data\\", "workspace/", "workspace\\")):
                candidate = (ROOT / raw).resolve()
            else:
                candidate = (DOCS_DIR / raw).resolve()
        else:
            candidate = candidate.resolve()
    except OSError:
        return None

    roots = []
    for r in (DOCS_DIR, WORKSPACE_DIR, DATASETS_DIR):
        if r.exists():
            roots.append(r.resolve())
        else:
            # still allow intended dirs even if empty (workspace always created)
            roots.append(r.resolve() if r == WORKSPACE_DIR else r)
    # Always include workspace/docs roots
    for r in (WORKSPACE_DIR.resolve(), DOCS_DIR.resolve()):
        if r not in roots:
            roots.append(r)
    if DATASETS_DIR.exists():
        roots.append(DATASETS_DIR.resolve())

    for root in roots:
        try:
            root = root.resolve()
            candidate.relative_to(root)
            return candidate
        except (ValueError, OSError):
            continue
    return None


def make_handler(state: GatewayState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            if os.environ.get("BONSAI_HTTP_LOG"):
                super().log_message(fmt, *args)

        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            return not origin or origin in state.allowed_origins

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").strip().lower()
            return bool(host) and host in state.allowed_hosts

        def _request_guard(self, *, mutating: bool) -> bool:
            if not self._host_ok():
                self._json(403, {"error": "invalid_host"})
                return False
            if mutating and not self._origin_ok():
                self._json(403, {"error": "forbidden_origin"})
                return False
            return True

        def _cors_headers(self) -> dict[str, str]:
            origin = self.headers.get("Origin")
            if origin and origin in state.allowed_origins:
                return {
                    "Access-Control-Allow-Origin": origin,
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                    "Access-Control-Allow-Headers": (
                        "Content-Type, Authorization, X-Bonsai-Token"
                    ),
                    "Vary": "Origin",
                }
            return {}

        def _send(
            self,
            code: int,
            body: bytes,
            content_type: str = "application/json",
            extra_headers: dict | None = None,
        ) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header(
                "X-Unlock-Status",
                "unlocked" if state.unlocked() else "locked",
            )
            self.send_header(
                "X-Debug-Mode", "true" if state.debug_mode else "false"
            )
            self.send_header("Vary", "Origin")
            for k, v in _security_headers().items():
                self.send_header(k, v)
            for k, v in self._cors_headers().items():
                self.send_header(k, v)
            if extra_headers:
                for k, v in extra_headers.items():
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data: dict) -> None:
            self._send(code, _json_bytes(data), "application/json; charset=utf-8")

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length < 0:
                raise ValueError("invalid Content-Length")
            if length > MAX_BODY_BYTES:
                raise ValueError(f"body exceeds {MAX_BODY_BYTES} bytes")
            raw = self.rfile.read(length) if length else b"{}"
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))

        def _client_token(self) -> str | None:
            return extract_bearer(
                self.headers.get("Authorization"),
                self.headers.get("X-Bonsai-Token"),
            )

        def _auth_ok(self) -> bool:
            if not state.auth_required:
                return True
            return state.sessions.verify(self._client_token())

        def _require_auth(self) -> bool:
            """Return True if authorized; else write 401 and return False."""
            if self._auth_ok():
                return True
            self._json(
                401,
                {
                    "error": "unauthorized",
                    "message": "Bearer token required. POST /api/session/unlock first.",
                },
            )
            return False

        def _require_rate(self, bucket: _RateBucket | None = None) -> bool:
            b = bucket or state.rate_limit
            if b.allow():
                return True
            self._json(429, {"error": "rate_limited", "message": "Too many requests"})
            return False

        def _proxy(self, method: str, *, public: bool = False) -> None:
            if not public and not self._require_auth():
                return
            if not self._require_rate():
                return
            path = self.path
            url = f"{state.llm_base}{path}"
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length < 0:
                self._json(400, {"error": "invalid_content_length"})
                return
            if length > MAX_BODY_BYTES:
                self._json(413, {"error": "payload_too_large"})
                return
            body = self.rfile.read(length) if length else None
            headers = {}
            ct = self.headers.get("Content-Type")
            if ct:
                headers["Content-Type"] = ct
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=300) as resp:
                    data = resp.read()
                    self.send_response(resp.status)
                    for key in ("Content-Type", "Content-Length"):
                        val = resp.headers.get(key)
                        if val:
                            self.send_header(key, val)
                    self.send_header(
                        "X-Unlock-Status",
                        "unlocked" if state.unlocked() else "locked",
                    )
                    self.send_header(
                        "X-Debug-Mode",
                        "true" if state.debug_mode else "false",
                    )
                    for k, v in _security_headers().items():
                        self.send_header(k, v)
                    self.end_headers()
                    self.wfile.write(data)
            except urllib.error.HTTPError as e:
                data = e.read()
                self._send(
                    e.code,
                    data or _json_bytes({"error": e.reason}),
                    e.headers.get("Content-Type", "application/json"),
                )
            except Exception as e:
                self._json(502, {"error": "llm_unavailable", "message": str(e)})

        def do_OPTIONS(self) -> None:
            if not self._request_guard(mutating=True):
                return
            self.send_response(204)
            for k, v in self._cors_headers().items():
                self.send_header(k, v)
            if not self._cors_headers():
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header(
                    "Access-Control-Allow-Headers",
                    "Content-Type, Authorization, X-Bonsai-Token",
                )
            for k, v in _security_headers().items():
                self.send_header(k, v)
            self.end_headers()

        def do_GET(self) -> None:
            if not self._request_guard(mutating=False):
                return
            parsed = urlparse(self.path)
            path = parsed.path

            if path in ("/v1",) or path.startswith("/v1/") or path == "/health":
                if path == "/health":
                    self._proxy("GET", public=True)
                    return
                self._proxy("GET")
                return

            if path == "/api/health":
                llm_ok = False
                try:
                    with urllib.request.urlopen(
                        f"{state.llm_base}/health", timeout=2
                    ) as resp:
                        llm_ok = resp.status == 200
                except Exception:
                    llm_ok = False
                self._json(
                    200,
                    {
                        "ok": True,
                        "gateway": True,
                        "llm": llm_ok,
                        "model": state.model_name,
                        "debug_mode": state.debug_mode,
                        "unlocked": state.unlocked(),
                        "insecure_mode": state.insecure_mode,
                        "auth_required": state.auth_required,
                        "rag_enabled": state.rag_enabled,
                        "persistence_enabled": state.persistence_enabled,
                        "features": {
                            "rag": "enabled" if state.rag_enabled else "disabled",
                            "persistence": (
                                "enabled"
                                if state.persistence_enabled
                                else "disabled"
                            ),
                        },
                    },
                )
                return

            if path == "/api/config":
                self._json(
                    200,
                    {
                        "host": state.host,
                        "port": state.port,
                        "model": state.model_name,
                        "rag_enabled": state.rag_enabled,
                        "persistence_enabled": state.persistence_enabled,
                        "debug_mode": state.debug_mode,
                        "auth_required": state.auth_required,
                    },
                )
                return

            if path == "/api/session/status":
                self._json(
                    200,
                    {
                        "auth_required": state.auth_required,
                        "authenticated": self._auth_ok(),
                        "provisioned": bool(state.vault and state.vault.exists()),
                        "unlocked": state.unlocked(),
                        "insecure_mode": state.insecure_mode,
                        "debug_mode": state.debug_mode,
                    },
                )
                return

            if path == "/api/identity":
                if not self._require_auth():
                    return
                self._handle_identity_get()
                return

            if path == "/api/personas":
                if not self._require_auth():
                    return
                self._handle_personas_get()
                return

            if path == "/api/subagents":
                if not self._require_auth():
                    return
                self._handle_subagents_get()
                return

            if path == "/api/tools":
                if not self._require_auth():
                    return
                self._handle_tools_get()
                return

            if path == "/api/rag/status":
                if not self._require_auth():
                    return
                self._handle_rag_status_get()
                return

            if path == "/api/rag/sources":
                if not self._require_auth():
                    return
                self._handle_rag_sources_get(include_deleted=False)
                return

            if path == "/api/rag/query":
                if not self._require_auth():
                    return
                if not self._require_rate():
                    return
                self._handle_rag_query_get(parsed)
                return

            if path == "/api/experience/status":
                if not self._require_auth():
                    return
                self._handle_experience_status_get()
                return

            # Static files (public)
            self._serve_static(path)

        def do_POST(self) -> None:
            if not self._request_guard(mutating=True):
                return
            parsed = urlparse(self.path)
            path = parsed.path

            if path.startswith("/v1/"):
                self._proxy("POST")
                return

            if path == "/api/session/unlock":
                self._handle_session_unlock()
                return

            # All other API POSTs need auth when required
            protected = path.startswith("/api/")
            if protected and path not in PUBLIC_POST_PREFIXES:
                if not self._require_auth():
                    return

            if path == "/api/identity":
                if not self._require_rate():
                    return
                self._handle_identity_post()
                return
            if path == "/api/identity/reset":
                if not self._require_rate():
                    return
                self._handle_identity_reset()
                return
            if path == "/api/tools":
                self._handle_tools_post()
                return
            if path == "/api/rag":
                if not self._require_rate():
                    return
                self._handle_rag_post()
                return
            if path == "/api/rag/query":
                if not self._require_rate():
                    return
                self._handle_rag_query_post()
                return
            if path == "/api/rag/index":
                if not self._require_rate():
                    return
                self._handle_rag_index_post()
                return
            if path == "/api/rag/sources/delete":
                if not self._require_rate():
                    return
                self._handle_rag_delete_source_post()
                return
            if path == "/api/rag/maintenance":
                if not self._require_rate():
                    return
                self._handle_rag_maintenance_post()
                return
            if path == "/api/experience/purge":
                if not self._require_rate():
                    return
                self._handle_experience_purge_post()
                return
            if path in ("/api/chat", "/api/agent"):
                if not self._require_rate(state.chat_rate_limit):
                    return
                self._handle_agent_chat()
                return
            if path == "/api/subagent":
                if not self._require_rate(state.chat_rate_limit):
                    return
                self._handle_subagent_post()
                return
            if path == "/api/experience/drain":
                if not self._require_rate():
                    return
                self._handle_experience_drain()
                return

            self._json(404, {"error": "not_found", "path": path})

        def _serve_static(self, path: str) -> None:
            if path in ("/", ""):
                rel = "index.html"
            else:
                rel = path.lstrip("/")
            try:
                target = (WEB_DIR / rel).resolve()
                target.relative_to(WEB_DIR.resolve())
            except (ValueError, OSError):
                self._json(403, {"error": "forbidden"})
                return
            if not target.exists() or not target.is_file():
                index = WEB_DIR / "index.html"
                if index.exists() and path == "/":
                    target = index
                else:
                    self._json(404, {"error": "not_found", "path": path})
                    return
            data = target.read_bytes()
            ctype = "text/html; charset=utf-8"
            if target.suffix == ".js":
                ctype = "application/javascript"
            elif target.suffix == ".css":
                ctype = "text/css"
            elif target.suffix == ".json":
                ctype = "application/json"
            elif target.suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
                ctype = f"image/{target.suffix.lstrip('.')}"
            self._send(200, data, ctype)

        def _handle_session_unlock(self) -> None:
            """Issue a token only for a verified credential or explicit bypass."""
            if not self._require_rate(state.unlock_rate_limit):
                return
            try:
                data = self._read_json()
            except ValueError as e:
                self._json(413 if "exceeds" in str(e) else 400, {"error": str(e)})
                return
            except Exception as e:
                self._json(400, {"error": f"invalid json: {e}"})
                return

            if state.debug_mode or state.insecure_mode:
                self._issue_unlock_token()
                return

            # A verified YubiKey at launch, or a session already unlocked by a
            # password in this process, can mint additional tokens.
            if state.unlocked():
                self._issue_unlock_token()
                return

            vault = state.vault
            if vault is None or not vault.exists():
                self._json(
                    403,
                    {
                        "error": "not_provisioned",
                        "message": (
                            "No owner vault configured. Run: "
                            "python launcher.py --set-password"
                        ),
                    },
                )
                return

            password = (data.get("password") or "").strip()
            if password and vault.verify(password):
                state.mark_unlocked()
                self._issue_unlock_token()
                return

            self._json(403, {"error": "invalid_credentials"})

        def _issue_unlock_token(self) -> None:
            if state.hooks is not None:
                state.hooks.run("session_start", {"source": "gateway"}, matcher="unlock")
            token = state.sessions.issue()
            self._json(
                200,
                {
                    "ok": True,
                    "token": token,
                    "auth_required": state.auth_required,
                    "unlocked": True,
                },
            )

        def _handle_identity_get(self) -> None:
            try:
                from persona import (
                    get_active_persona,
                    is_initial_setup,
                    load_identity,
                )

                identity = load_identity()
                needs_setup = is_initial_setup()
                persona_info = get_active_persona() if identity else None
                active = None
                if persona_info:
                    active = {
                        k: v
                        for k, v in persona_info.items()
                        if k != "system_base"
                    }
                    active["system_base"] = persona_info.get("system_base", "")
                self._json(
                    200,
                    {
                        "configured": identity is not None,
                        "needs_setup": needs_setup,
                        "debug_mode": state.debug_mode,
                        "identity": {
                            "owner": identity.get("owner", "") if identity else "",
                            "persona": identity.get("persona", "") if identity else "",
                            "setup_at": identity.get("setup_at", "") if identity else "",
                        }
                        if identity
                        else None,
                        "active_persona": active,
                    },
                )
            except Exception as e:
                self._json(500, {"error": str(e)})

        def _handle_personas_get(self) -> None:
            try:
                from persona import get_personas_list

                self._json(200, {"personas": get_personas_list()})
            except Exception as e:
                self._json(500, {"error": str(e)})

        def _handle_subagents_get(self) -> None:
            if not state.subagents_enabled or state.subagent_index is None:
                self._json(503, {"error": "subagents_disabled"})
                return
            self._json(
                200,
                {
                    "subagents": state.subagent_index.list_public(),
                    "errors": state.subagent_index.errors,
                },
            )

        def _handle_subagent_post(self) -> None:
            if not state.subagents_enabled or state.subagent_index is None:
                self._json(503, {"error": "subagents_disabled"})
                return
            try:
                data = self._read_json()
                name = str(data.get("name") or "").strip()
                message = str(data.get("message") or "").strip()
                if not name or not message:
                    self._json(400, {"error": "name and message are required"})
                    return
                agent = state.subagent_index.get(name)
                if agent is None:
                    self._json(404, {"error": "subagent_not_found"})
                    return
                if state.hooks is not None:
                    decision = state.hooks.run(
                        "user_prompt",
                        {"text": message, "subagent": name},
                        matcher=name,
                    )
                    if decision.blocked:
                        self._json(
                            403,
                            {"error": "blocked_by_hook", "message": decision.reason},
                        )
                        return
                from persona import load_identity, resolve_persona_tools
                from subagents import run_subagent
                from tools import get_toolbox

                identity = load_identity()
                persona_key = (identity or {}).get("persona") or "investigador"
                client = _HttpModelClient(state.llm_base, model=state.model_name)
                result = run_subagent(
                    agent,
                    message,
                    client,
                    model=str(data.get("model") or ""),
                    toolbox=get_toolbox(),
                    hooks=state.hooks,
                    permissions=state.permissions,
                    persona_tools=resolve_persona_tools(persona_key),
                )
                self._json(200, result)
            except ValueError as e:
                self._json(400, {"error": str(e)})
            except Exception as e:
                self._json(500, {"error": str(e)})

        def _handle_identity_post(self) -> None:
            try:
                from persona import get_active_persona, save_identity

                data = self._read_json()
                persona = data.get("persona", "")
                owner = data.get("owner", "")
                if not persona:
                    self._json(400, {"error": "persona es requerido"})
                    return
                identity = save_identity(persona, owner)
                persona_info = get_active_persona()
                active = {
                    k: v for k, v in persona_info.items() if k != "system_base"
                }
                active["system_base"] = persona_info.get("system_base", "")
                self._json(
                    200,
                    {
                        "ok": True,
                        "identity": {
                            "owner": identity["owner"],
                            "persona": identity["persona"],
                            "setup_at": identity["setup_at"],
                        },
                        "active_persona": active,
                    },
                )
            except ValueError as e:
                if "exceeds" in str(e):
                    self._json(413, {"error": str(e)})
                else:
                    self._json(400, {"error": str(e)})
            except Exception as e:
                self._json(400, {"error": str(e)})

        def _handle_identity_reset(self) -> None:
            try:
                from persona import reset_identity

                reset_identity()
                self._json(200, {"ok": True})
            except Exception as e:
                self._json(500, {"error": str(e)})

        def _handle_tools_get(self) -> None:
            try:
                from tools import get_toolbox, power_tools_enabled

                toolbox = get_toolbox()
                payload = {
                    "tools": toolbox.list_tool_names(),
                    "power_tools": power_tools_enabled(),
                }
                registry = state.tool_registry if state.mcp_enabled else None
                if registry is not None:
                    payload["mcp_tools"] = [
                        {
                            "name": spec.name,
                            "description": spec.description,
                            "source": spec.source,
                            "trust": spec.trust,
                        }
                        for spec in registry.specs()
                        if spec.name.startswith("mcp__")
                    ]
                self._json(200, payload)
            except Exception as e:
                self._json(503, {"error": str(e)})

        def _handle_tools_post(self) -> None:
            # Direct tool execution is high risk — disabled unless allow_direct_tools
            if not state.allow_direct_tools:
                self._json(
                    403,
                    {
                        "error": "direct_tools_disabled",
                        "message": (
                            "POST /api/tools is disabled. Use /api/chat agent loop. "
                            "Enable only with BONSAI_DEBUG=1."
                        ),
                    },
                )
                return
            if not self._require_rate():
                return
            try:
                from tools import (
                    execute_tool_calls,
                    format_tool_results,
                    get_toolbox,
                    parse_tool_calls,
                )

                data = self._read_json()
                if "tool" in data and "args" in data:
                    calls = [data]
                else:
                    calls = parse_tool_calls(data.get("text", ""))
                if not calls:
                    self._json(400, {"error": "no tool calls found"})
                    return
                toolbox = get_toolbox()
                registry = state.tool_registry if state.mcp_enabled else None
                results = execute_tool_calls(
                    calls,
                    toolbox,
                    registry=registry,
                    hooks=state.hooks,
                    permissions=state.permissions,
                )
                self._json(
                    200,
                    {
                        "results": results,
                        "formatted": format_tool_results(results),
                    },
                )
            except ValueError as e:
                code = 413 if "exceeds" in str(e) else 400
                self._json(code, {"error": str(e)})
            except Exception as e:
                self._json(500, {"error": str(e)})

        # -- RAG ---------------------------------------------------------

        def _feature_disabled(self, feature: str, message: str) -> None:
            self._json(
                503,
                {
                    "error": "feature_unavailable",
                    "feature": feature,
                    "message": message,
                },
            )

        def _rag_memory(self):
            from local_memory import get_memory

            return get_memory(state.db_path)

        def _rag_sources_payload(self, *, include_deleted: bool = False) -> None:
            mem = self._rag_memory()
            sources = mem.list_sources(include_deleted=include_deleted)
            self._json(
                200,
                {
                    "ok": True,
                    "rag_enabled": True,
                    "indexed": len(sources),
                    "sources": sources,
                    "files": sources,
                },
            )

        def _handle_rag_status_get(self) -> None:
            if not state.rag_enabled:
                self._feature_disabled("rag", "RAG is disabled in this build.")
                return
            try:
                self._json(
                    200,
                    {
                        "ok": True,
                        "enabled": True,
                        **self._rag_memory().status(),
                    },
                )
            except Exception as exc:
                self._json(500, {"error": str(exc)})

        def _handle_rag_sources_get(self, *, include_deleted: bool) -> None:
            if not state.rag_enabled:
                self._feature_disabled("rag", "RAG is disabled in this build.")
                return
            try:
                self._rag_sources_payload(include_deleted=include_deleted)
            except Exception as exc:
                self._json(500, {"error": str(exc)})

        def _do_rag_query(
            self,
            query: str,
            top_k,
            *,
            source_type: str | None = None,
            path_prefix: str | None = None,
        ) -> None:
            query = str(query or "").strip()
            if not query:
                self._json(400, {"error": "query required"})
                return
            if len(query) > 2000:
                self._json(413, {"error": "query exceeds 2000 characters"})
                return
            try:
                k = int(top_k if top_k is not None else state.rag_top_k)
            except (TypeError, ValueError):
                k = state.rag_top_k
            k = max(1, min(k, 20))
            source_type = str(source_type).strip() if source_type else None
            path_prefix = str(path_prefix).strip() if path_prefix else None
            if source_type and len(source_type) > 64:
                self._json(400, {"error": "source_type too long"})
                return
            if path_prefix and len(path_prefix) > 512:
                self._json(400, {"error": "path_prefix too long"})
                return
            from local_memory import format_context

            mem = self._rag_memory()
            results = mem.search(
                query,
                top_k=k,
                source_type=source_type,
                path_prefix=path_prefix,
            )
            context = format_context(results, max_chars=state.rag_max_chars)
            self._json(
                200,
                {
                    "ok": True,
                    "query": query,
                    "context": context,
                    "results": results,
                    "citations": [
                        {
                            "citation": r["citation"],
                            "path": r["path"],
                            "chunk": r["chunk"],
                            "score": r["score"],
                            "source_type": r["source_type"],
                        }
                        for r in results
                    ],
                    "rag_enabled": True,
                },
            )

        def _handle_rag_query_get(self, parsed) -> None:
            if not state.rag_enabled:
                self._feature_disabled("rag", "RAG is disabled in this build.")
                return
            try:
                params = parse_qs(parsed.query)
                query = (params.get("query") or params.get("q") or [""])[0]
                top_k = (params.get("top_k") or [state.rag_top_k])[0]
                source_type = (params.get("source_type") or [None])[0]
                path_prefix = (params.get("path_prefix") or [None])[0]
                self._do_rag_query(
                    query,
                    top_k,
                    source_type=source_type,
                    path_prefix=path_prefix,
                )
            except Exception as exc:
                self._json(500, {"error": str(exc)})

        def _handle_rag_query_post(self) -> None:
            if not state.rag_enabled:
                self._feature_disabled("rag", "RAG is disabled in this build.")
                return
            try:
                data = self._read_json()
                self._do_rag_query(
                    data.get("query", ""),
                    data.get("top_k"),
                    source_type=data.get("source_type"),
                    path_prefix=data.get("path_prefix"),
                )
            except ValueError as exc:
                code = 413 if "exceeds" in str(exc) else 400
                self._json(code, {"error": str(exc)})
            except Exception as exc:
                self._json(500, {"error": str(exc)})

        def _do_rag_index(self, data: dict) -> None:
            from local_memory import DOCS_DIR as MEM_DOCS

            raw_paths = data.get("paths")
            if raw_paths is None:
                raw_paths = [data.get("path") or str(MEM_DOCS)]
            if not isinstance(raw_paths, list) or not raw_paths or len(raw_paths) > 20:
                self._json(400, {"error": "paths must be a non-empty list (max 20)"})
                return
            targets = []
            for source in raw_paths:
                if not isinstance(source, str) or not source.strip():
                    self._json(400, {"error": "invalid path"})
                    return
                if len(source) > 1024:
                    self._json(400, {"error": "path too long"})
                    return
                confined = _confined_index_path(source)
                if confined is None:
                    self._json(
                        403,
                        {
                            "error": "path_forbidden",
                            "message": (
                                "RAG index only under docs/, workspace/, "
                                "or data/KAYAB_DATASETS"
                            ),
                        },
                    )
                    return
                targets.append(confined)

            if not state.rag_lock.acquire(blocking=False):
                self._json(409, {"error": "index_busy"})
                return
            try:
                mem = self._rag_memory()
                merged = {
                    "indexed": 0,
                    "skipped": 0,
                    "new_files": 0,
                    "updated_files": 0,
                    "errors": [],
                }
                for target in targets:
                    stats = mem.index_docs(target)
                    for key in ("indexed", "skipped", "new_files", "updated_files"):
                        merged[key] += int(stats.get(key, 0))
                    merged["errors"].extend(stats.get("errors", []))
                self._json(
                    200,
                    {"ok": True, **merged, "status": mem.status()},
                )
            finally:
                state.rag_lock.release()

        def _handle_rag_index_post(self) -> None:
            if not state.rag_enabled:
                self._feature_disabled("rag", "RAG is disabled in this build.")
                return
            try:
                data = self._read_json()
                self._do_rag_index(data)
            except ValueError as exc:
                code = 413 if "exceeds" in str(exc) else 400
                self._json(code, {"error": str(exc)})
            except Exception as exc:
                self._json(500, {"error": str(exc)})

        def _do_rag_delete_source(self, data: dict) -> None:
            try:
                document_id = int(data.get("id") or data.get("document_id") or 0)
            except (TypeError, ValueError):
                self._json(400, {"error": "invalid id"})
                return
            if document_id <= 0:
                self._json(400, {"error": "id required"})
                return
            purge = data.get("purge") is True
            deleted = self._rag_memory().delete_source(document_id, purge=purge)
            if not deleted:
                self._json(404, {"error": "source_not_found"})
                return
            self._json(200, {"ok": True, "deleted": document_id, "purged": purge})

        def _handle_rag_delete_source_post(self) -> None:
            if not state.rag_enabled:
                self._feature_disabled("rag", "RAG is disabled in this build.")
                return
            try:
                self._do_rag_delete_source(self._read_json())
            except ValueError as exc:
                code = 413 if "exceeds" in str(exc) else 400
                self._json(code, {"error": str(exc)})
            except Exception as exc:
                self._json(500, {"error": str(exc)})

        def _do_rag_maintenance(self, data: dict) -> None:
            if not state.rag_lock.acquire(blocking=False):
                self._json(409, {"error": "index_busy"})
                return
            try:
                from maintenance import get_metrics, maintenance_cycle

                # Destructive options require a real JSON boolean. A string
                # such as "false" must never enable a purge or disable dry-run.
                dry_run = data.get("dry_run", True)
                if not isinstance(dry_run, bool):
                    dry_run = True
                summary = maintenance_cycle(
                    db_path=state.db_path,
                    purge_documents=data.get("purge_documents") is True,
                    dry_run=dry_run,
                )
                metrics = get_metrics(state.db_path, log=False)
                self._json(
                    200,
                    {"ok": True, "maintenance": summary, "metrics": metrics},
                )
            finally:
                state.rag_lock.release()

        def _handle_rag_maintenance_post(self) -> None:
            if not state.rag_enabled:
                self._feature_disabled("rag", "RAG is disabled in this build.")
                return
            try:
                self._do_rag_maintenance(self._read_json())
            except ValueError as exc:
                code = 413 if "exceeds" in str(exc) else 400
                self._json(code, {"error": str(exc)})
            except Exception as exc:
                self._json(500, {"error": str(exc)})

        def _handle_rag_post(self) -> None:
            """Legacy single-endpoint API retained for UI/script compatibility."""
            if not state.rag_enabled:
                self._feature_disabled("rag", "RAG is disabled in this build.")
                return
            try:
                data = self._read_json()
                action = str(data.get("action", "query"))
                if action in ("list", "sources"):
                    self._rag_sources_payload(
                        include_deleted=bool(data.get("include_deleted", False))
                    )
                    return
                if action == "status":
                    self._json(
                        200,
                        {"ok": True, "enabled": True, **self._rag_memory().status()},
                    )
                    return
                if action == "index":
                    self._do_rag_index(data)
                    return
                if action == "query":
                    self._do_rag_query(
                        data.get("query", ""),
                        data.get("top_k"),
                        source_type=data.get("source_type"),
                        path_prefix=data.get("path_prefix"),
                    )
                    return
                if action == "delete":
                    self._do_rag_delete_source(data)
                    return
                if action == "maintenance":
                    self._do_rag_maintenance(data)
                    return
                self._json(400, {"error": f"unknown action: {action}"})
            except ValueError as exc:
                code = 413 if "exceeds" in str(exc) else 400
                self._json(code, {"error": str(exc)})
            except Exception as exc:
                self._json(500, {"error": str(exc)})

        # -- experience persistence --------------------------------------

        def _handle_experience_status_get(self) -> None:
            if not state.persistence_enabled:
                self._feature_disabled(
                    "persistence",
                    "Experience persistence is disabled in this build.",
                )
                return
            try:
                import experience_store

                self._json(
                    200,
                    {
                        "ok": True,
                        "enabled": True,
                        **experience_store.status(state.db_path),
                    },
                )
            except Exception as exc:
                self._json(500, {"error": str(exc)})

        def _handle_experience_drain(self) -> None:
            if not state.persistence_enabled:
                self._feature_disabled(
                    "persistence",
                    "Experience persistence is disabled in this build.",
                )
                return
            try:
                import experience_store
                from audit_worker import audit_worker

                data = self._read_json()
                try:
                    max_events = int(data.get("max_events", 100))
                except (TypeError, ValueError):
                    self._json(400, {"error": "max_events must be an integer"})
                    return
                max_events = max(1, min(max_events, 500))
                n = audit_worker(db_path=state.db_path, max_events=max_events)
                self._json(
                    200,
                    {
                        "ok": True,
                        "processed": n,
                        "max_events": max_events,
                        "status": experience_store.status(state.db_path),
                    },
                )
            except ValueError as exc:
                code = 413 if "exceeds" in str(exc) else 400
                self._json(code, {"error": str(exc)})
            except Exception as exc:
                self._json(500, {"error": str(exc)})

        def _handle_experience_purge_post(self) -> None:
            if not state.persistence_enabled:
                self._feature_disabled(
                    "persistence",
                    "Experience persistence is disabled in this build.",
                )
                return
            try:
                import experience_store

                data = self._read_json()
                days = float(data.get("older_than_days", 7))
                deleted = experience_store.purge(
                    db_path=state.db_path,
                    older_than_seconds=max(0, int(days * 86400)),
                )
                self._json(
                    200,
                    {
                        "ok": True,
                        "purged": deleted,
                        "status": experience_store.status(state.db_path),
                    },
                )
            except ValueError as exc:
                code = 413 if "exceeds" in str(exc) else 400
                self._json(code, {"error": str(exc)})
            except Exception as exc:
                self._json(500, {"error": str(exc)})

        def _handle_agent_chat(self) -> None:
            """Server-side agent loop with tools + optional RAG + output mask."""
            try:
                from agent_loop import TOOL_PROTOCOL_HINT, interactive_react
                from local_memory import format_context, get_memory
                from persona import (
                    build_persona_system,
                    load_identity,
                    resolve_persona_tools,
                )
                from tools import get_toolbox

                data = self._read_json()
                user_text = (data.get("message") or data.get("text") or "").strip()
                if not user_text:
                    self._json(400, {"error": "message required"})
                    return

                if state.hooks is not None:
                    decision = state.hooks.run(
                        "user_prompt",
                        {"text": user_text, "persona": data.get("persona")},
                        matcher="chat",
                    )
                    if decision.blocked:
                        self._json(
                            403,
                            {
                                "error": "blocked_by_hook",
                                "message": decision.reason,
                            },
                        )
                        return

                history = data.get("messages") or []
                clean_hist = [
                    {"role": m.get("role"), "content": m.get("content", "")}
                    for m in history
                    if m.get("role") in ("user", "assistant", "system")
                    and m.get("content") is not None
                ]

                identity = load_identity()
                persona_key = (identity or {}).get("persona") or data.get(
                    "persona", "investigador"
                )
                rag_ctx = ""
                rag_results: list[dict] = []
                if state.rag_enabled and data.get("rag", True):
                    rag_results = get_memory(state.db_path).search(
                        user_text, top_k=state.rag_top_k
                    )
                    rag_ctx = format_context(
                        rag_results, max_chars=state.rag_max_chars
                    )

                system = build_persona_system(
                    persona_key,
                    docs_context="",
                    extra_context=data.get("extra_context", ""),
                )

                agents_md = ""
                skills_index = ""
                active_skills: list[tuple[str, str]] = []
                if state.agents_md_enabled:
                    try:
                        from agents_md import load_agents_md

                        agents_md = load_agents_md(ROOT)
                    except Exception:
                        agents_md = ""
                if state.skills_enabled:
                    try:
                        from skills import get_skill_index, skills_enabled

                        if skills_enabled():
                            index = get_skill_index(ROOT)
                            skills_index = index.index_text()
                            if user_text.startswith("/"):
                                requested = user_text.split(None, 1)[0][1:]
                                skill = index.get(requested)
                                if skill is not None:
                                    active_skills.append((skill.name, skill.body))
                    except Exception:
                        skills_index = ""

                from context_builder import build_system_prompt

                mcp_catalog = ""
                if state.mcp_enabled and state.tool_registry is not None:
                    try:
                        mcp_catalog = state.tool_registry.mcp.catalog_text()
                    except Exception:
                        mcp_catalog = ""

                system = build_system_prompt(
                    system,
                    tool_hint=TOOL_PROTOCOL_HINT,
                    agents_md=agents_md,
                    skills_index=skills_index,
                    active_skills=active_skills,
                    mcp_catalog=mcp_catalog,
                    rag_context=rag_ctx,
                )

                messages = [{"role": "system", "content": system}]
                for m in clean_hist:
                    if m["role"] != "system":
                        messages.append(m)

                client = _HttpModelClient(state.llm_base, model=state.model_name)
                persona_tools = resolve_persona_tools(persona_key)
                registry = state.tool_registry if state.mcp_enabled else None
                if registry is not None:
                    persona_tools = frozenset(
                        set(persona_tools) | set(registry.mcp_names())
                    )

                max_iter = min(int(data.get("max_iterations", 5)), 8)
                answer, new_msgs, tool_results = interactive_react(
                    user_text,
                    client,
                    messages,
                    model=data.get("model") or "bonsai",
                    toolbox=get_toolbox(),
                    persona_tools=persona_tools,
                    registry=registry,
                    hooks=state.hooks,
                    permissions=state.permissions,
                    max_iterations=max_iter,
                )

                redacted = []
                if state.mask_outputs:
                    try:
                        from mask_machine import MaskService

                        answer, redacted = MaskService().sanitize(answer)
                    except Exception:
                        pass

                if state.persistence_enabled:
                    try:
                        import uuid

                        from agent_loop import drain_experience_queue, enqueue_experience

                        any_fail = any(not t.get("ok", True) for t in tool_results)
                        enqueue_experience(
                            {
                                "query": user_text,
                                "plan": "",
                                "result": json.dumps(
                                    [
                                        {"tool": t.get("tool"), "ok": t.get("ok")}
                                        for t in tool_results
                                    ]
                                ),
                                "final": answer,
                                "reflection": "web chat turn",
                                "success": (not any_fail) if tool_results else True,
                                "model": data.get("model") or state.model_name,
                                "tool_results": [
                                    {"tool": t.get("tool"), "ok": t.get("ok")}
                                    for t in tool_results
                                ],
                            },
                            db_path=state.db_path,
                            idempotency_key=uuid.uuid4().hex,
                        )
                        drain_experience_queue(
                            db_path=state.db_path, max_events=5
                        )
                    except Exception:
                        pass

                self._json(
                    200,
                    {
                        "ok": True,
                        "reply": answer,
                        "tools": [
                            {
                                "tool": t.get("tool"),
                                "ok": t.get("ok"),
                                "error": t.get("error"),
                                "message": t.get("message"),
                            }
                            for t in tool_results
                        ],
                        "rag_used": bool(rag_ctx),
                        "citations": [
                            {
                                "citation": r["citation"],
                                "path": r["path"],
                                "chunk": r["chunk"],
                                "score": r["score"],
                                "source_type": r["source_type"],
                            }
                            for r in rag_results
                        ],
                        "redacted": redacted,
                        "messages": [
                            m
                            for m in new_msgs
                            if m.get("role") in ("user", "assistant")
                        ][-20:],
                    },
                )
            except ValueError as e:
                code = 413 if "exceeds" in str(e) else 400
                self._json(code, {"error": str(e)})
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")[:500]
                self._json(
                    502,
                    {"error": "llm_http_error", "message": f"{e.code}: {body}"},
                )
            except Exception as e:
                self._json(500, {"error": str(e)})

    return Handler


def start_gateway(
    state: GatewayState,
    *,
    background: bool = True,
) -> ThreadingHTTPServer:
    handler = make_handler(state)
    server = ThreadingHTTPServer((state.host, state.port), handler)
    state.port = int(server.server_address[1])
    state.host = str(server.server_address[0])
    if background:
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
    return server
