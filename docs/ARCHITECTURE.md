# Arquitectura — Bonsai Portable (CRS) · RC.5

## Visión
Bonsai Portable es un runtime cognitivo local/edge: chat, herramientas sandbox y
un gateway único para UI/API, sin dependencias externas en el núcleo.

## Alcance de rc.5
Esta release es de **seguridad, honestidad y completitud**. La autenticación es
fail-closed, el parser de herramientas es estricto y las superficies opcionales
están apagadas por defecto. RAG (FTS5/BM25, schema v2) y persistencia de
experiencias (cola durable con leases) están **implementados y probados**, pero
siguen siendo opt-in por privacidad.

## Threat model (honesto)
| Actor | Asunción |
|-------|----------|
| Usuario local de confianza | Caso de uso principal (USB / laptop personal) |
| Proceso local malicioso con acceso al puerto | Mitigado: bearer + Origin/Host + rate limit |
| Página web visitada por el usuario | Mitigado: CORS restringido, CSP `script-src 'self'`, sin handlers inline |
| Modelo / prompt injection | Solo ejecuta bloques `<tool_call>` y tools en allowlist de persona + power gate |
| Documentos/RAG envenenados | Mitigado: bloques no confiables delimitados; nunca conceden permisos |
| Atacante en LAN | Mitigado: bind loopback por defecto; LAN no soportada en rc.5 (Host rechaza clientes no loopback) |
| Atacante con filesystem | Parcial: permisos `0600`; cifrado en reposo fuera de alcance (usar FDE) |

**No es** multi-tenant cloud ni sandbox OS (seccomp/container).
Detalle y mapeo amenaza→test: `THREAT_MODEL.md`.

## Superficie de producto
| Componente | Rol |
|------------|-----|
| `launcher.py` / `./start.sh` / `start.ps1` | Entrypoint; detección, vault, clamp de bind, arranque, `--doctor` |
| `runtime.py` | Raíz de composición compartida (plugins, MCP, hooks, permisos, subagentes) |
| `gateway.py` | Origen único: UI + `/api/*` + proxy `/v1/*` + auth + CORS/Host + CSP |
| `session_auth.py` | Tokens opacos en RAM + `Vault` v2 (owner.lock único, KDF+salt+canary) |
| `crypto.py` | Vault Encrypt-then-MAC v1; legacy v0 rechazado |
| `model_client.py` | Cliente HTTP OpenAI-compatible compartido por gateway y ACP |
| `config_schema.py` | Validación fail-fast de `config.json` |
| `doctor.py` | Diagnóstico local (Python, modelo, binario, vault, DB, puerto) |
| `bonsai_logging.py` | Logs JSON con redacción de secretos y rotación |
| `web/index.html` + `web/app.js` + `web/styles.css` | UI (OOBE, chat, tools timeline, citas RAG) |
| `agent_loop.py` | Protocolo canónico de tools + ReAct + wrappers de la cola |
| `tools.py` | Sandbox de herramientas (path + AST + power gate) + parser estricto |
| `agents_md.py` | Carga jerárquica de AGENTS.md (root→cwd, tope 32 KB) |
| `skills.py` | Agent Skills: frontmatter YAML mínimo, discovery y progressive disclosure |
| `context_builder.py` | Orden fijo del system prompt + delimitadores de contenido no confiable |
| `mcp_client.py` | Cliente MCP stdio (JSON-RPC), namespacing y allowlists |
| `tool_registry.py` | Registro unificado de tools locales + MCP |
| `hooks.py` | Hooks de ciclo de vida (stdin JSON, timeout, fail-closed) |
| `permissions.py` | Política allow/ask/deny por tool |
| `plugin_adapters.py` | Lectura declarativa de plugins Claude/Gemini/Hermes/Antigravity |
| `subagents.py` | Agentes Markdown + ejecución con tools restringidas |
| `acp_server.py` | Servidor ACP v1 (JSON-RPC por stdio) |
| `local_memory.py` | RAG local: documents/chunks, FTS5/BM25, citas, migraciones |
| `experience_store.py` | Cola durable: idempotencia, claim/lease, backoff, dead-letter |
| `audit_worker.py` | Consumidor de experiencias → chunks RAG |
| `maintenance.py` | Scoring de retención, soft-kill, GC seguro y métricas |
| `persona.py` | Arquetipos + allowlists canónicos |

## Contexto externo
El prompt se compone en orden fijo:
1. persona/política (confiable)
2. protocolo de tools (confiable)
3. AGENTS.md (no confiable)
4. índice de skills (no confiable)
5. cuerpo de skill activa (no confiable)
6. catálogo MCP (no confiable)
7. contexto RAG (no confiable)

Cada bloque externo va delimitado y con una regla explícita: puede influir en
lo que el modelo dice, nunca en lo que el runtime permite. `allowed-tools` de
una skill es una sugerencia; la intersección con la allowlist de persona y
`ALLOWED_TOOLS` sigue siendo obligatoria. Lo mismo aplica a subagentes: su set
efectivo es agente ∩ global ∩ persona activa.

Skills: `skills/`, `.agents/skills/`, `.claude/skills/`, `.opencode/skills/`,
`.gemini/skills/`, `~/.agents/skills/` (el primero gana; conflictos se reportan).
Los scripts de skill no se ejecutan.

MCP: solo stdio; tools como `mcp__server__tool`; servidores declarados en
`config.json` con `enabled: true` y `tools_allow` opcional. El cliente habla
MCP `2026-07-28` (`server/discover` y `_meta` por petición) y cae al handshake
legacy `initialize` solo con servidores que no implementan la revisión moderna.
Un `tools_allow: []` explícito no expone ninguna tool; omitirlo expone el
catálogo anunciado. Entorno saneado, sin shell, timeout y tope de salida.

## RAG y persistencia

### RAG (schema v2)
- `documents` es autoritativa (`source_type, root, path` únicos); las rutas son
  relativas a la raíz de indexado.
- `chunks` referencia `documents`; `chunks_fts` es FTS5 external-content y se
  mantiene con triggers.
- Ingesta idempotente por hash de contenido; cambio de archivo reemplaza chunks
  atómicamente; archivo ausente → `deleted` (soft); borrado por usuario no se
  resucita.
- Búsqueda BM25 con tokens entrecomillados (anti-inyección FTS), filtro
  `is_active=1` + documento activo, dedupe por hash y diversidad por documento.
- `quality_base` inmutable; `quality_score` es solo una foto de mantenimiento.
- Migración v1→v2 transaccional y cubierta por tests.

### Persistencia de experiencias
- Tabla `events` con `idempotency_key UNIQUE`, `status`, `attempts`,
  `available_at`, `locked_by/locked_at`, `last_error`.
- `BEGIN IMMEDIATE` para reclamar lotes; leases caducados se recuperan.
- Backoff exponencial (30 s × 2^n, máx 1 h); dead-letter a los 5 intentos.
- El worker indexa `experience/<id>` en RAG, por lo que un reintento no duplica
  documentos.

## Extensiones

- **Hooks** (`hooks.py`): comandos locales declarados por el propietario. Se
  ejecutan sin shell, con entorno allowlisted, timeout y tope de 4 KB. Entrada
  JSON por stdin; salida 2 o `{"decision":"block"}` bloquea. Los eventos
  `user_prompt`, `pre_llm` y `pre_tool` fallan cerrados.
- **Permisos** (`permissions.py`): reglas `allow`/`ask`/`deny` con `mcp__*` como
  wildcard. `ask` solo se aprueba si la tool está en `auto_approve`; en gateway
  y ACP (headless) el resto se deniega.
- **Plugins** (`plugin_adapters.py`): manifiestos Claude, Gemini, Hermes y
  Antigravity. Solo se importan skills, agentes prompt-only, definiciones MCP y
  hooks declarativos. El código JS/TS/Python se reporta como no soportado y
  nunca se ejecuta. Los MCP de plugin quedan apagados hasta `plugins_enable_mcp`.
- **Subagentes** (`subagents.py`): Markdown con frontmatter. El set efectivo es
  agente ∩ `ALLOWED_TOOLS` ∩ power gate ∩ allowlist de la persona activa; nunca
  amplía permisos. Superficie opt-in (`subagents_enabled`, apagada por defecto).
- **ACP** (`acp_server.py`): JSON-RPC 2.0 por stdio con `initialize`,
  `session/new`, `session/prompt`, `session/cancel` y `session/update`. No
  ofrece filesystem, terminal ni contexto embebido; rechaza MCP del cliente.
  A2A no se implementa: no hay transporte remoto entre agentes.

## Loop de ejecución
1. **Percepción** — input del usuario
2. **Retrieval** — `retrieve()`/`search()` sobre `memory.db` (solo si `rag_enabled`)
3. **Planning** — LLM vía llama-server (OpenAI-compatible)
4. **Tools** — `<tool_call>{"tool","args"}</tool_call>` → persona allowlist ∩ ALLOWED_TOOLS
5. **Reflection** — JSON `{success, reflection}` en el path de agente
6. **Persistence** — `experience_store` → `audit_worker` (solo si `experience_persistence`)

El path web guarda el turno completo con clave de idempotencia cuando la
persistencia está activa y devuelve `citations` cuando RAG está activo.

## Seguridad
- **Auth fail-closed**: sin vault, `POST /api/session/unlock` no emite token.
- **Sesión**: emitir token requiere bearer válido o contraseña verificada; el estado “unlocked” por sí solo no autoriza. YubiKey entrega un token corto solo al cliente que arranca el launcher.
- **Vault**: PBKDF2-SHA256 600k + canary Encrypt-then-MAC; un solo `owner.lock` 0600 con rotación atómica. Vaults rc.3 se migran al verificar.
- **IPv6**: rechazada al arrancar; el servidor stdlib es IPv4-only.
- **Proxy LLM**: sin redirects ni proxies implícitos; timeout por petición.
- **CORS**: solo el origen exacto del gateway; nunca `*`.
- **Host**: validado contra la dirección de bind (anti DNS-rebinding).
- **Rate limits**: 90 req/min general, 30/min chat, 5/min unlock per client.
- **Escritura**: solo `workspace/`.
- **Lectura**: `workspace/` + `docs/` + `data/KAYAB_DATASETS` si existe.
- **Subprocess**: cwd=workspace, whitelist sin intérpretes, flags allowlisted, sin shell; cada path (incluidos nombres relativos y symlinks) se resuelve bajo las raíces de lectura; límites de recursos best-effort.
- **`knowledge_lookup`**: read-only structured lookup, disabled by default, gated by config, permissions, persona allowlists, and hooks.
- **SSRF**: bloqueo loopback/privado/link-local/CGNAT y otras IPs no globales + recheck post-redirect; cuerpo de respuesta limitado a 1 MiB.
- **Parser**: solo `<tool_call>`; fences/JSON suelto requieren
  `BONSAI_LEGACY_TOOL_PARSING=1`.
- **Hooks/permisos**: sin shell, entorno saneado, timeout; pre-eventos fallan
  cerrados. `ask` se deniega en headless sin `auto_approve`.
- **Plugins**: solo metadatos declarativos; código de terceros nunca se ejecuta.
- **ACP**: stdio local, sin fs/terminal/MCP del cliente; mismas tools, hooks y
  permisos que el gateway.
- **CSP**: `script-src 'self'` (sin inline), `object-src 'none'`,
  `base-uri 'self'`, `form-action 'self'`, `frame-ancestors 'none'`; UI en
  `/app.js` + `/styles.css` con delegación `data-action`.
- **Body limit**: 2 MiB + headers `nosniff`, frame-deny, COOP/CORP,
  Permissions-Policy.
- **Logs**: `BONSAI_LOG_JSON=1` escribe JSON rotado con redacción de tokens.

## Variables de entorno
| Var | Efecto |
|-----|--------|
| `BONSAI_DEBUG=1` | Auth off, direct tools on, power tools on (desarrollo) |
| `BONSAI_INSECURE=1` | Bypass explícito: emite token sin contraseña; anuncia warning |
| `BONSAI_POWER_TOOLS=1` | Habilita execute_command / fetch_url / python_exec (gate inicial) |
| `BONSAI_UNSAFE_PYTHON_EXEC=1` | Opt-in adicional para python_exec sin aislamiento OS |
| `knowledge_db_enabled` | Habilita `knowledge_lookup` y `/api/knowledge/*` |
| `agent_runs_enabled` | Persiste ejecuciones de agente (opt-in) |
| `agent_tracing_enabled` | Persiste trazas redactadas (opt-in) |
| `BONSAI_BIN_SHA256` | SHA-256 explícito para `llama-server` (`--setup`; falla cerrada) |
| `BONSAI_HTTP_LOG=1` | Access log del gateway |
| `BONSAI_LOG_JSON=1` | Logs estructurados en `runtime/bonsai.log` |
| `BONSAI_CONFIG=<path>` | Override de `config.json` |
| `BONSAI_VAULT_ROOT=<path>` | Override de la raíz del vault (tests/portable) |
| `BONSAI_LEGACY_TOOL_PARSING=1` | Acepta fences/JSON suelto (legacy) |
| `BONSAI_SKILLS=0` | Deshabilita Agent Skills |
| `BONSAI_LLM_BASE=<url>` | Backend para `acp_server.py` standalone |

## Puertos
- **Gateway** (público): `config.port` (default 8080)
- **llama-server** (interno): `port + llm_port_offset` (default +100)

## Tests
```bash
./scripts/ci.sh
python3 scripts/smoke_boot.py
python3 -m coverage run --branch -m unittest discover -s tests -p 'test_*.py'
python3 scripts/check_coverage.py
python3 redteam/run_all.py
```
