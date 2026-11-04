# Arquitectura — Bonsai Portable (CRS) · RC.5

## Visión
Bonsai Portable es un runtime cognitivo local/edge: chat, herramientas sandbox y
un gateway único para UI/API, sin dependencias externas en el núcleo.

## Alcance de rc.5
Esta release es de **seguridad y honestidad**. La autenticación es fail-closed y
el parser de herramientas es estricto. RAG y persistencia de experiencias están
**deshabilitados por defecto** porque las implementaciones de rc.3 no eran
fiables; el código permanece detrás de flags de configuración.

## Threat model (honest)
| Actor | Asunción |
|-------|----------|
| Usuario local de confianza | Caso de uso principal (USB / laptop personal) |
| Proceso local malicioso con acceso al puerto | Mitigado: bearer + Origin/Host + rate limit |
| Página web visitada por el usuario | Mitigado: CORS restringido, sin `Access-Control-Allow-Origin: *` |
| Modelo / prompt injection | Solo ejecuta bloques `<tool_call>` y tools en allowlist de persona + power gate |
| Atacante en LAN | Mitigado: bind loopback por defecto; LAN no soportada en rc.5 (Host rechaza clientes no loopback) |
| Atacante con filesystem | Fuera de alcance (USB robado) |

**No es** multi-tenant cloud ni sandbox OS (seccomp/container).

## Superficie de producto
| Componente | Rol |
|------------|-----|
| `launcher.py` / `./start.sh` / `start.ps1` | Entrypoint; detección, vault, clamp de bind, arranque |
| `gateway.py` | Origen único: UI + `/api/*` + proxy `/v1/*` + auth + CORS/Host |
| `session_auth.py` | Tokens opacos en RAM + `Vault` v2 (owner.lock único, KDF+salt+canary) |
| `crypto.py` | Vault Encrypt-then-MAC v1; legacy v0 rechazado |
| `web/index.html` | UI (OOBE, chat, tools timeline) |
| `agent_loop.py` | Protocolo canónico de tools + ReAct |
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
| `local_memory.py` | CRS (chunks, FTS5, scoring) — conexión por operación |
| `persona.py` | Arquetipos + allowlists canónicos |
| `audit_worker.py` | Cola de experiencias → memoria (experimental, apagado) |

## Contexto externo (rc.5)
El prompt se compone en orden fijo:
1. persona/política (confiable)
2. protocolo de tools (confiable)
3. AGENTS.md (no confiable)
4. índice de skills (no confiable)
5. cuerpo de skill activa (no confiable)
6. catálogo MCP (no confiable)

Cada bloque externo va delimitado y con una regla explícita: puede influir en
lo que el modelo dice, nunca en lo que el runtime permite. `allowed-tools` de
una skill es una sugerencia; la intersección con la allowlist de persona y
`ALLOWED_TOOLS` sigue siendo obligatoria.

Skills: `skills/`, `.agents/skills/`, `.claude/skills/`, `.opencode/skills/`,
`.gemini/skills/`, `~/.agents/skills/` (el primero gana; conflictos se reportan).
Los scripts de skill no se ejecutan.

MCP: solo stdio; tools como `mcp__server__tool`; servidores declarados en
`config.json` con `enabled: true` y `tools_allow` opcional. Entorno saneado,
sin shell, timeout y tope de salida.

## Extensiones (rc.5)

- **Hooks** (`hooks.py`): comandos locales declarados por el propietario. Se
  ejecutan sin shell, con entorno allowlisted, timeout y tope de 4 KB. Entrada
  JSON por stdin; salida 2 o `{"decision":"block"}` bloquea. Los eventos
  `user_prompt`, `pre_llm` y `pre_tool` fallan cerrados.
- **Permisos** (`permissions.py`): reglas `allow`/`ask`/`deny` con `mcp__*` como
  wildcard. `ask` solo se aprueba si la tool está en `auto_approve`; en gateway
  y ACP (headless) el resto se deniega.
- **Plugins** (`plugin_adapters.py`): manifiestos Claude, Gemini, Hermes y
  Antigravity. Solo se importan skills, agentes prompt-only, definiciones MCP y
  hooks. El código JS/TS/Python se reporta como no soportado y nunca se ejecuta.
  Los MCP de plugin quedan apagados hasta `plugins_enable_mcp`.
- **Subagentes** (`subagents.py`): Markdown con frontmatter. Las tools declaradas
  se intersectan con `ALLOWED_TOOLS` y el gate de power tools; nunca amplían la
  allowlist. Superficie opt-in (`subagents_enabled`, apagada por defecto).
- **ACP** (`acp_server.py`): JSON-RPC 2.0 por stdio con `initialize`,
  `session/new`, `session/prompt`, `session/cancel` y `session/update`. No
  ofrece filesystem, terminal ni contexto embebido; rechaza MCP del cliente.
  A2A no se implementa: no hay transporte remoto entre agentes.

## Loop de ejecución
1. **Percepción** — input del usuario
2. **Retrieval** — `retrieve()` sobre `memory.db` (solo si `rag_enabled`)
3. **Planning** — LLM vía llama-server (OpenAI-compatible)
4. **Tools** — `<tool_call>{"tool","args"}</tool_call>` → persona allowlist ∩ ALLOWED_TOOLS
5. **Reflection** — JSON `{success, reflection}` en el path de agente
6. **Persistence** — `event_queue` → `audit_worker` (solo si `experience_persistence`)

En el path web de rc.5 la persistencia está apagada y no se escribe reflexión
fabricada. La reflexión real y el drain transaccional quedan para la próxima
release.

## Seguridad
- **Auth fail-closed**: sin vault, `POST /api/session/unlock` no emite token.
- **Sesión**: un desbloqueo verificado permite emitir tokens a la UI/CLI hasta reiniciar.
- **Vault**: PBKDF2-SHA256 600k + canary Encrypt-then-MAC; un solo `owner.lock` 0600 con rotación atómica. Vaults rc.3 se migran al verificar.
- **IPv6**: rechazada al arrancar; el servidor stdlib es IPv4-only.
- **Tokens**: bearer en RAM, TTL 24 h, revocables al reiniciar.
- **CORS**: solo el origen exacto del gateway; nunca `*`.
- **Host**: validado contra la dirección de bind (anti DNS-rebinding).
- **Rate limits**: 90 req/min general, 30/min chat, 5/min unlock.
- **Escritura**: solo `workspace/`.
- **Lectura**: `workspace/` + `docs/` + `data/KAYAB_DATASETS` si existe.
- **Subprocess**: cwd=workspace, whitelist sin intérpretes, sin shell.
- **`python_exec`**: AST allowlist + `python -I`; power-gated.
- **SSRF**: bloqueo loopback/privado/link-local + recheck post-redirect.
- **Parser**: solo `<tool_call>`; fences/JSON suelto requieren
  `BONSAI_LEGACY_TOOL_PARSING=1`.
- **Hooks/permisos**: sin shell, entorno saneado, timeout; pre-eventos fallan
  cerrados. `ask` se deniega en headless sin `auto_approve`.
- **Plugins**: solo metadatos declarativos; código de terceros nunca se ejecuta.
- **ACP**: stdio local, sin fs/terminal/MCP del cliente; mismas tools, hooks y
  permisos que el gateway.
- **Body limit**: 2 MiB + headers de seguridad (CSP, nosniff, frame-deny).

## Variables de entorno
| Var | Efecto |
|-----|--------|
| `BONSAI_DEBUG=1` | Auth off, direct tools on, power tools on (desarrollo) |
| `BONSAI_INSECURE=1` | Bypass explícito: emite token sin contraseña; anuncia warning |
| `BONSAI_POWER_TOOLS=1` | Habilita python_exec / execute_command / fetch_url |
| `BONSAI_ALLOW_LAN=1` | Permite host no-loopback en config |
| `BONSAI_HTTP_LOG=1` | Access log del gateway |
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
python3 redteam/run_all.py
```
