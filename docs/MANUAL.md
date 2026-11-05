# Bonsai Portable — Manual (rc.5)

## Inicio rápido

```bash
# 1) Dependencias: Python 3.11.4+, binario llama-server y un .gguf
python3 launcher.py --setup          # descarga binario + modelos (interactivo, verifica SHA-256)

# 2) Crear el vault del propietario (solo la primera vez)
python3 launcher.py --set-password

# 3) Arrancar
./start.sh                           # gateway UI+API en :8080, LLM interno
./start.sh --cli                     # solo terminal
./start.sh -m /ruta/otro.gguf        # cualquier .gguf
BONSAI_DEBUG=1 ./start.sh            # dev: auth off, power tools on
BONSAI_POWER_TOOLS=1 ./start.sh      # shell / fetch_url
BONSAI_UNSAFE_PYTHON_EXEC=1 ./start.sh  # además permite python_exec sin aislamiento

# Diagnóstico local (Python, modelo, binario, vault, RAG, puerto)
python3 launcher.py --doctor
python3 doctor.py --json

# Override de configuración
BONSAI_CONFIG=/ruta/otro-config.json ./start.sh
```

En Windows: `.\start.ps1` (requiere Python 3.11+ en PATH).

**Nota de arquitectura:** el navegador habla con un único gateway
(`host:port`). El gateway sirve `web/`, `/api/*` y hace proxy de `/v1/*` y
`/health` hacia llama-server en un puerto interno (`port + llm_port_offset`,
default +100). La composición de runtime (plugins, MCP, hooks, permisos,
subagentes) vive en `runtime.py` y la comparten el launcher y ACP.

## Autenticación

- Sin `owner.lock` no se puede desbloquear: `POST /api/session/unlock` responde
  `403 not_provisioned`. No hay contraseña por defecto.
- `--set-password` crea un único `owner.lock` (0600) con parámetros KDF, salt y
  canary Encrypt-then-MAC. La rotación es un solo `os.replace` atómico.
- PBKDF2-HMAC-SHA256 usa 600.000 iteraciones. Los vaults rc.3 (salt/canary
  separados, 100k) siguen funcionando y se migran al primer desbloqueo.
- El desbloqueo tiene rate limit (5 intentos/minuto) por cliente.
- `/api/*` y `/v1/*` requieren bearer. `/health` y `/api/health` son públicos.
- Emitir un token requiere un bearer válido o la contraseña del vault. Un
  proceso meramente desbloqueado (por ejemplo YubiKey) no autoriza a clientes
  locales a emitir tokens.
- Para desbloqueo por hardware, el launcher entrega un token de corta duración
  solo al CLI/navegador que arranca.
- CORS y Host se limitan al origen loopback del gateway.
- `--yubikey` verifica la respuesta contra el vault; si falla, el servidor queda
  bloqueado. Solo funciona si el vault se creó con esa misma respuesta: trátalo
  como experimental.
- `BONSAI_INSECURE=1` es el único bypass de auth y lo anuncia en consola.
- `--cli` pide la contraseña del vault si el servidor está bloqueado.

## Estado de funcionalidades

| Capacidad | Estado |
|---|---|
| Arranque Linux/macOS | Funciona |
| Arranque Windows (`start.ps1`) | Funciona con Python; parse test en CI |
| Gateway UI + `/api/*` + `/v1` | Funciona |
| Vault / auth de sesión | Funciona |
| CORS/Host endurecido | Funciona |
| CSP sin scripts inline | Funciona (`script-src 'self'`) |
| Herramientas read/write/list/stats | Funciona |
| `python_exec` (opt-in, sin aislamiento OS) | Funciona; requiere gate unsafe aparte |
| `knowledge_lookup` (BD de conocimiento opt-in) | Funciona; `knowledge_db_enabled` |
| `execute_command` / `fetch_url` | Funciona, power-gated |
| Allowlists de persona | Funciona |
| Protocolo `<tool_call>` | Funciona, estricto por defecto |
| AGENTS.md | Funciona, solo lectura, tope 32 KB |
| Agent Skills (`SKILL.md`) | Funciona, solo lectura; scripts nunca se ejecutan |
| MCP stdio | Opt-in (`mcp_enabled`), protocolo `2026-07-28` + fallback legacy, namespaced y allowlisted |
| MCP HTTP/OAuth | No implementado |
| Hooks de ciclo de vida | Opt-in (`hooks.enabled`), sin shell, pre-eventos fail-closed |
| Permisos por tool (`allow`/`ask`/`deny`) | Funciona; `ask` falla cerrado en headless |
| Plugins (Claude/Gemini/Hermes/Antigravity) | Solo manifiestos declarativos; código nunca se ejecuta |
| Subagentes (`agents/*.md`) | Funciona, intersección agente ∩ global ∩ persona |
| ACP v1 (stdio) | Opt-in (`launcher.py --acp`) |
| A2A | **Fuera de alcance** (producto local) |
| RAG (FTS5/BM25, schema v2) | Funciona, opt-in (`rag_enabled`) |
| Persistencia de experiencias (lease/retry) | Funciona, opt-in (`experience_persistence`) |
| Mantenimiento/GC | Funciona; dry-run y purga segura de documentos |
| Knowledge DB / linter | Legacy, no cableado a producción |

## Memoria y RAG

RAG es **local y sin dependencias**: SQLite FTS5 + BM25 sobre `data/vector_db/memory.db`.
Sigue apagado por defecto porque indexar archivos locales es una decisión de
privacidad. Actívalo de forma explícita:

```json
{
  "rag_enabled": true,
  "rag_max_chars": 6000,
  "rag_top_k": 3
}
```

Qué garantiza el schema v2:

- `documents` es la fuente de verdad; las rutas son relativas a la raíz, así que
  `a/notes.md` y `b/notes.md` no colisionan.
- `chunks_fts` se mantiene con triggers; nunca se edita a mano.
- La ingesta es idempotente: contenido sin cambios se omite.
- Un archivo borrado del disco se marca como `deleted` y no se devuelve.
- Un archivo borrado por el usuario **no resucita** al reindexar.
- La búsqueda escapa operadores FTS (`*`, `:`, `NEAR`, paréntesis) y devuelve
  `[]` si no hay coincidencias; no hay fallback arbitrario.
- El contexto se acota con `rag_max_chars` y lleva citas
  `[source: ruta#chunk]`.
- Las bases rc.5 (schema v1) migran automáticamente al abrirse.

API principal (todas requieren auth):

```bash
GET  /api/rag/status
GET  /api/rag/sources
GET  /api/rag/query?q=...&top_k=3
POST /api/rag/query        {"query": "...", "top_k": 3}
POST /api/rag/index        {"path": "docs"} o {"paths": ["docs","workspace"]}
POST /api/rag/sources/delete {"id": 1, "purge": false}
POST /api/rag/maintenance  {"dry_run": true, "purge_documents": false}
POST /api/rag              {"action": "list|status|index|query|delete|maintenance"}
```

Solo puede correr un indexado/mantenimiento a la vez (`409 index_busy`). El
contexto recuperado entra al prompt como bloque no confiable
`<<<BONSAI_RAG_CONTEXT>>>` y `/api/chat` devuelve `citations`.

Detalle completo: `docs/RAG.md`.

## Persistencia de experiencias

Opt-in (`experience_persistence: true`). El turno completo (query, tools,
respuesta, éxito) se guarda en una cola SQLite con:

- claves de idempotencia (no duplica eventos);
- claim por lote con lease (varios drainers no repiten trabajo);
- recuperación de leases caducados tras un crash;
- reintentos con backoff exponencial y dead-letter tras 5 intentos;
- purga de eventos terminales.

```bash
GET  /api/experience/status
POST /api/experience/drain   {"max_events": 50}
POST /api/experience/purge   {"older_than_days": 7}
```

El worker indexa cada evento como `experience/<id>` (`source_type=experience`).
Detalle completo: `docs/PERSISTENCE.md`.

## Loop de ejecución

1. **Percepción**: input del usuario.
2. **Retrieval**: contexto de memoria (solo si RAG está activo).
3. **Planning**: LLM vía llama-server (catálogo Bonsai o cualquier `.gguf`).
4. **Execution**: `<tool_call>{"tool": "...", "args": {...}}</tool_call>` filtrado
   por persona y `ALLOWED_TOOLS`.
5. **Reflection**: JSON `{success, reflection}` en el path de agente.
6. **Persistence**: cola durable de experiencias (solo si está activa).

En el path web, cuando la persistencia está activa, se guarda el turno completo
con una clave de idempotencia; la reflexión no se inventa.

## Sandbox de herramientas

- Escritura: solo `workspace/`.
- Lectura: `workspace/`, `docs/` y `data/KAYAB_DATASETS` si existe.
- `execute_command`: whitelist `cat/ls/grep/echo/head/tail/wc`, cwd workspace,
  sin shell, flags allowlisted y cada argumento de path (incluidos nombres
  relativos sin `/` y symlinks) se resuelve bajo las raíces de lectura.
- `python_exec`: deshabilitado por defecto; requiere
  `BONSAI_UNSAFE_PYTHON_EXEC=1` además de power tools. Usa un pre-filtro AST,
  imports limitados y `python -I`, pero no es un sandbox de sistema operativo:
  el filtro puede evadirse y no es una frontera de seguridad.
- `fetch_url`: bloquea loopback/privado/link-local/CGNAT y otras IPs no globales,
  revalida redirects y limita el cuerpo de respuesta a 1 MiB con marca de truncado.
- Power tools requieren `BONSAI_POWER_TOOLS=1` o `BONSAI_DEBUG=1`. `python_exec`
  además requiere `BONSAI_UNSAFE_PYTHON_EXEC=1`.
- `execute_command` y `python_exec` aplican límites de CPU, memoria, tamaño de
  archivo, procesos y descriptores cuando la plataforma los soporta.
- Skills: los directorios de skills son raíces de lectura para `read_file`
  (`references/`, `assets/`); `scripts/` no se ejecuta.
- MCP: procesos hijos con entorno saneado, sin shell, cwd fijado, timeout y
  tope de salida. El cliente usa `server/discover` y `_meta` por petición en
  `2026-07-28`, con fallback al handshake legacy `initialize`. Un
  `tools_allow: []` explícito no expone ninguna tool. Las descripciones y
  resultados MCP son contenido no confiable.
- Hooks: comandos locales declarados solo por el propietario, sin shell, con
  entorno saneado (no se filtran secretos), timeout y tope de salida. `user_prompt`,
  `pre_llm` y `pre_tool` fallan cerrados ante timeout o error de arranque.
- Permisos: `ask` no se puede responder en gateway/ACP headless, así que se
  trata como `deny` salvo que la tool esté en `auto_approve`.
- Plugins: los adaptadores leen únicamente manifiestos y componentes
  declarativos. `index.js`, `index.ts`, `main.py` y similares nunca se ejecutan.
  Los servidores MCP de un plugin siguen apagados hasta `plugins_enable_mcp`.
- Subagentes: las tools declaradas son un techo; se intersectan con
  `ALLOWED_TOOLS`, el gate de power tools y la allowlist de la persona activa.

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
Detalle: `docs/ACP.md`.

## Mantenimiento

`maintenance.py` calcula retención desde `quality_base` (inmutable) y **nunca**
usa su propia salida como entrada. Por defecto:

- solo hace GC duro de chunks de experiencias;
- los documentos del usuario solo se desactivan (soft-kill);
- `purge_documents=true` es obligatorio para borrarlos;
- `dry_run=true` calcula el resumen sin escribir.

Las métricas se registran en `data/maintenance_log.jsonl`. No hay scheduler
automático en rc.5; ejecútalo desde `/api/rag/maintenance` o manualmente.

## Backup y recuperación

```bash
scripts/backup.sh                     # backup/bonsai-backup-<ts>.tar.gz (0600)
scripts/restore.sh backup/....tar.gz  # rechaza rutas absolutas y ..
```

El backup incluye `config.json`, `identity.json`, `owner.lock` y `data/`; contiene
credenciales, así que guárdalo cifrado. Para migrar de versión: backup →
reemplazar código → conservar config/identity/vault/data/models → `--doctor` →
abrir el gateway (el schema RAG migra solo).

Detalle operativo: `docs/OPERATIONS.md`.

## Limitaciones conocidas

- **RAG y persistencia son opt-in**, no experimentales. Están completos pero
  apagados por defecto por privacidad (`rag_enabled: false`,
  `experience_persistence: false`).
- **RAG es léxico.** FTS5/BM25 es la base; embeddings locales no forman parte de
  1.0.
- `knowledge_db.py` se promovió a capability opt-in (`knowledge_db_enabled`);
  `linter.py` sigue siendo legacy y no alcanzable desde producción.
- `data/KAYAB_DATASETS` es opcional; elimina el symlink si no está montado.
- Sin cifrado en reposo para `data/`: usa cifrado de disco completo si el equipo
  puede perderse.
- Sin modo LAN, sin multi-usuario, sin sandbox de OS. La validación de Host
  rechaza clientes no loopback, así que `BONSAI_ALLOW_LAN=1` no basta para usar
  el gateway en red.
- IPv6 no soportada: `::1` se rechaza al arrancar (el servidor stdlib es IPv4).
- MCP solo stdio; HTTP/SSE y OAuth quedan fuera. El pinning de esquemas entre
  reinicios todavía no se persiste.
- Los plugins de Claude/Gemini/Hermes/Antigravity se leen como manifiestos
  declarativos; su código no se ejecuta y sus servidores MCP quedan apagados por
  defecto.
- ACP es la única integración de editor y es solo stdio. A2A queda fuera de
  alcance porque el producto no expone transporte remoto entre agentes.
- `ask` no tiene flujo interactivo en gateway/ACP: sin `auto_approve` se deniega.
- Windows/macOS son best-effort; Linux es la plataforma principal. `start.ps1`
  requiere un runner Windows real para considerarse verificado.

## Tests

```bash
./scripts/ci.sh
python3 -m unittest discover -s tests -v
python3 -m coverage run --branch -m unittest discover -s tests -p 'test_*.py'
python3 scripts/check_coverage.py
python3 redteam/run_all.py
```

Suite rc.5: **295 tests, 1 skip** (`pwsh` ausente), con pisos de cobertura por
módulo, contratos de importación, comprobaciones de CSP/UI, migración
v1→v2 de RAG, semántica de leases/reintentos y probes de producción.
