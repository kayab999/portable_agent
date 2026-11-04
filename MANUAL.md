# Bonsai Portable — Manual (rc.5)

## Inicio rápido

```bash
# 1) Dependencias: Python 3.11+, binario llama-server y un .gguf
python3 launcher.py --setup          # descarga binario + modelos (interactivo)

# 2) Crear el vault del propietario (solo la primera vez)
python3 launcher.py --set-password

# 3) Arrancar
./start.sh                           # gateway UI+API en :8080, LLM interno
./start.sh --cli                     # solo terminal
./start.sh -m /ruta/otro.gguf        # cualquier .gguf
BONSAI_DEBUG=1 ./start.sh            # dev: auth off, power tools on
BONSAI_POWER_TOOLS=1 ./start.sh      # python_exec / shell / fetch_url

# Override de configuración
BONSAI_CONFIG=/ruta/otro-config.json ./start.sh
```

En Windows: `.\start.ps1` (requiere Python 3.11+ en PATH).

**Nota de arquitectura:** el navegador habla con un único gateway
(`host:port`). El gateway sirve `web/`, `/api/*` y hace proxy de `/v1/*` y
`/health` hacia llama-server en un puerto interno (`port + llm_port_offset`,
default +100).

## Autenticación

- Sin `owner.lock` no se puede desbloquear: `POST /api/session/unlock` responde
  `403 not_provisioned`. No hay contraseña por defecto.
- `--set-password` crea un único `owner.lock` (0600) con parámetros KDF, salt y
  canary Encrypt-then-MAC. La rotación es un solo `os.replace` atómico.
- PBKDF2-HMAC-SHA256 usa 600.000 iteraciones. Los vaults rc.3 (salt/canary
  separados, 100k) siguen funcionando y se migran al primer desbloqueo.
- El desbloqueo tiene rate limit (5 intentos/minuto).
- `/api/*` y `/v1/*` requieren bearer. `/health` y `/api/health` son públicos.
- CORS y Host se limitan al origen loopback del gateway.
- `--yubikey` verifica la respuesta contra el vault; si falla, el servidor queda
  bloqueado. Solo funciona si el vault se creó con esa misma respuesta: trátalo
  como experimental.
- `BONSAI_INSECURE=1` es el único bypass de auth y lo anuncia en consola.
- `--cli` pide la contraseña del vault si el servidor está bloqueado.
- Una sesión ya desbloqueada puede emitir tokens nuevos (nueva pestaña/CLI) sin
  repetir la contraseña hasta reiniciar el proceso.

## Estado de funcionalidades

| Capacidad | Estado |
|---|---|
| Arranque Linux/macOS | Funciona |
| Arranque Windows (`start.ps1`) | Funciona con Python; parse test en CI |
| Gateway UI + `/api/*` + `/v1` | Funciona |
| Vault / auth de sesión | Funciona |
| CORS/Host endurecido | Funciona |
| Herramientas read/write/list/stats | Funciona |
| `python_exec` (AST sandbox) | Funciona, restrictivo a propósito |
| `execute_command` / `fetch_url` | Funciona, power-gated |
| Allowlists de persona | Funciona |
| Protocolo `<tool_call>` | Funciona, estricto por defecto |
| AGENTS.md | Funciona, solo lectura, tope 32 KB |
| Agent Skills (`SKILL.md`) | Funciona, solo lectura; scripts nunca se ejecutan |
| MCP stdio | Opt-in (`mcp_enabled`), namespaced y allowlisted |
| MCP HTTP/OAuth | No implementado |
| Hooks de ciclo de vida | Opt-in (`hooks.enabled`), sin shell, pre-eventos fail-closed |
| Permisos por tool (`allow`/`ask`/`deny`) | Funciona; `ask` falla cerrado en headless |
| Plugins (Claude/Gemini/Hermes/Antigravity) | Solo manifiestos declarativos; código nunca se ejecuta |
| Subagentes (`agents/*.md`) | Funciona, intersección restrictiva de tools |
| ACP v1 (stdio) | Opt-in (`launcher.py --acp`) |
| A2A | **Fuera de alcance** (producto local) |
| RAG | **Experimental, apagado por defecto** |
| Persistencia de experiencias | **Experimental, apagada por defecto** |
| Mantenimiento/GC | **Experimental, no programado** |
| Knowledge DB / linter | Sin cablear |

## Memoria y RAG (experimental)

rc.3 tenía dos fallos que rc.5 contiene en lugar de anunciar como resueltos:

1. La base `data/vector_db/memory.db` podía quedar sin chunks mientras
   `doc_stats` conservaba los hashes, impidiendo el reindexado.
2. El ciclo de mantenimiento borraba chunks pero no `doc_stats`.

Por eso `rag_enabled` es `false` por defecto y `POST /api/rag` responde
`503 feature_unavailable`. La persistencia de experiencias también está apagada:
el drain de rc.3 podía bloquearse tras el primer evento y duplicar escrituras.

Para desarrollarla, usa una base temporal y `TFIDFIndex` directamente. No actives
estas flags en una distribución.

## Loop de ejecución

1. **Percepción**: input del usuario.
2. **Retrieval**: contexto de memoria (solo si RAG está activo).
3. **Planning**: LLM vía llama-server (catálogo Bonsai o cualquier `.gguf`).
4. **Execution**: `<tool_call>{"tool": "...", "args": {...}}</tool_call>` filtrado
   por persona y `ALLOWED_TOOLS`.
5. **Reflection**: JSON `{success, reflection}` en el path de agente.
6. **Persistence**: cola de experiencias (apagada por defecto).

El path web de rc.5 no fabrica reflexión ni escribe en la cola.

## Sandbox de herramientas

- Escritura: solo `workspace/`.
- Lectura: `workspace/`, `docs/` y `data/KAYAB_DATASETS` si existe.
- `execute_command`: whitelist `cat/ls/grep/echo/head/tail/wc`, cwd workspace,
  sin shell, argumentos con path-jail.
- `python_exec`: AST allowlist, imports limitados, `python -I`; bloquea
  `os`, `open`, `eval`, `exec`, dunders.
- `fetch_url`: bloquea loopback/privado/link-local/metadata y revalida redirects.
- Power tools requieren `BONSAI_POWER_TOOLS=1` o `BONSAI_DEBUG=1`.
- Skills: los directorios de skills son raíces de lectura para `read_file`
  (`references/`, `assets/`); `scripts/` no se ejecuta.
- MCP: procesos hijos con entorno saneado, sin shell, cwd fijado, timeout y
  tope de salida. Las descripciones y resultados MCP son contenido no confiable.
- Hooks: comandos locales declarados solo por el propietario, sin shell, con
  entorno saneado (no se filtran secretos), timeout y tope de salida. `user_prompt`,
  `pre_llm` y `pre_tool` fallan cerrados ante timeout o error de arranque.
- Permisos: `ask` no se puede responder en gateway/ACP headless, así que se
  trata como `deny` salvo que la tool esté en `auto_approve`.
- Plugins: los adaptadores leen únicamente manifiestos y componentes
  declarativos. `index.js`, `index.ts`, `main.py` y similares nunca se ejecutan.
  Los servidores MCP de un plugin siguen apagados hasta `plugins_enable_mcp`.
- Subagentes: las tools declaradas son un techo; se intersectan con
  `ALLOWED_TOOLS` y el gate de power tools.

## Hooks, permisos y subagentes

```json
{
  "hooks": {
    "enabled": true,
    "pre_tool": [
      {"command": "python3", "args": ["hooks/check.py"], "matcher": "write_file"}
    ]
  },
  "tool_permissions": {
    "default": "allow",
    "rules": {"write_file": "ask", "mcp__*": "deny"},
    "auto_approve": []
  },
  "plugins_enabled": false,
  "plugin_paths": [],
  "plugins_enable_mcp": false,
  "subagents_enabled": false
}
```

Eventos de hook: `session_start`, `user_prompt`, `pre_llm`, `pre_tool`,
`post_tool`, `session_end`. El payload llega como JSON por stdin; salir con
código `2` o imprimir `{"decision":"block","reason":"..."}` bloquea.

Un subagente es un Markdown con frontmatter:

```markdown
---
name: reviewer
description: Revisa un cambio sin modificarlo
tools: [read_file, list_dir, grep]
---

Eres un revisor cuidadoso.
```

Se descubren en `agents/`, `.agents/agents/` y `.claude/agents/`. El gateway los
expone en `GET /api/subagents` y `POST /api/subagent` si `subagents_enabled`.
Están **apagados por defecto**; actívalos después de revisar los archivos.

## ACP (Agent Client Protocol)

```bash
python3 launcher.py --acp
# o contra un llama-server ya iniciado:
BONSAI_LLM_BASE=http://127.0.0.1:8180 python3 acp_server.py
```

ACP v1 habla JSON-RPC 2.0 por stdin/stdout: `initialize`, `authenticate`,
`session/new`, `session/prompt`, `session/cancel` y notificaciones
`session/update`. No anuncia filesystem, terminal, imagen, audio ni contexto
embebido. `session/new` rechaza servidores MCP enviados por el cliente y las
tools siguen pasando por hooks, permisos y el sandbox. A2A no se implementa: el
producto es local y no tiene transporte remoto entre agentes.

## Mantenimiento

`maintenance.py` existe y tiene tests, pero **no se ejecuta automáticamente** en
rc.5. No lo ejecutes sobre una base que quieras conservar: el scoring de rc.3
sobrescribía `quality_score` y podía degradar todos los chunks.

## Limitaciones conocidas

- RAG y persistencia apagados; reflexión real, scoring, accent folding y
  migraciones de esquema quedan para la próxima release.
- `knowledge_db.py` y `linter.py` no son alcanzables desde producción.
- `data/KAYAB_DATASETS` es opcional; elimina el symlink si no está montado.
- El panel de motor/modelo y la librería de fuentes de la UI no están cableados.
- Sin modo LAN, sin multi-usuario, sin sandbox de OS. La validación de Host
  rechaza clientes no loopback, así que `BONSAI_ALLOW_LAN=1` no basta para usar
  el gateway en red.
- IPv6 no soportada: `::1` se rechaza al arrancar (el servidor stdlib es IPv4).
- MCP solo stdio; HTTP/SSE y OAuth quedan para una próxima release. El pinning
  de esquemas entre reinicios todavía no se persiste.
- Los plugins de Claude/Gemini/Hermes/Antigravity se leen como manifiestos
  declarativos; su código no se ejecuta y sus servidores MCP quedan apagados por
  defecto.
- ACP es la única integración de editor y es solo stdio. A2A queda fuera de
  alcance porque el producto no expone transporte remoto entre agentes.
- `ask` no tiene flujo interactivo en gateway/ACP: sin `auto_approve` se deniega.

## Tests

```bash
./scripts/ci.sh
python3 -m unittest discover -s tests -v
python3 redteam/run_all.py
```

Suite rc.5: **220 tests, 219 pasando + 1 skip** (`pwsh` ausente). Incluye hooks,
permisos, adaptadores de plugins, subagentes, ACP y los controles de rc.4.
