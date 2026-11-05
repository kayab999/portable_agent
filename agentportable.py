#!/usr/bin/env python3
"""
Bonsai Portable — Launcher Universal
Detecta OS/arch, selecciona el binario correcto, inicia llama-server
y abre la interfaz web. Funciona en Linux, Windows y macOS.
"""

import os

if os.environ.get("BONSAI_LEGACY_PATHS") != "1" and __name__ != "__main__":
    raise RuntimeError(
        "Legacy module disabled by default. Set BONSAI_LEGACY_PATHS=1 to load it."
    )
import sys
import json
import time
import platform
import subprocess
import shutil
import atexit
import sqlite3
from pathlib import Path

import runtime_core

# ─── COLORES ANSI ─────────────────────────────────────────────────────────────
R = "\033[0;31m"
G = "\033[0;32m"
Y = "\033[1;33m"
C = "\033[0;36m"
B = "\033[1;34m"
M = "\033[0;35m"
W = "\033[1;37m"
NC = "\033[0m"
BOLD = "\033[1m"


def p(msg, color=W):
    print(f"{color}{msg}{NC}")


def ok(msg):
    print(f"{G}  ✓  {NC}{msg}")


def err(msg):
    print(f"{R}  ✗  {NC}{msg}")
    sys.exit(1)


def warn(msg):
    print(f"{Y}  !  {NC}{msg}")


def info(msg):
    print(f"{C}  ·  {NC}{msg}")


BANNER = f"""
{B}{BOLD}
  ╔══════════════════════════════════════╗
  ║   🌿 Bonsai Portable AI Runtime     ║
  ║   PrismML 1-bit · Sin instalación   ║
  ╚══════════════════════════════════════╝
{NC}"""

# ─── PATHS ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.resolve()
BIN_DIR = ROOT / "bin"
MODELS = ROOT / "models"
WEB_DIR = ROOT / "web"
CFG_FILE = runtime_core.config_file(ROOT / "config.json")
DOCS_DIR = ROOT / "docs"
OWNER_LOCK = ROOT / "owner.lock"
BONSAI_LOCK = ROOT / "bonsai.lock"
DEBUG_MODE = True

# ─── LOCAL MEMORY (RAG) — zero external deps ─────────────────────────────────

# ─── RATE LIMITING & RETRY — zero external deps ──────────────────────────────

# ─── TOOLBOX (herramientas reales del agente) ────────────────────────────────
try:
    from tools import get_toolbox

    _HAS_TOOLS = True
except ImportError:
    _HAS_TOOLS = False

# ─── ORCHESTRATOR ─────────────────────────────────────────────────────────────

class BonsaiOrchestrator:
    """
    Controlador central del Agente Soberano.
    Gestiona memoria persistente, enrutamiento dinámico de modelos y el bucle ReAct.
    """
    def __init__(self, cfg, tools_module):
        self.cfg = cfg
        self.tools = tools_module
        self.data_dir = ROOT / "data"
        self.snapshots_dir = self.data_dir / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        
        # Snapshot configuration
        self.active_snapshot = self.cfg.get("active_snapshot", "v1")
        self.snapshot_path = self.snapshots_dir / self.active_snapshot
        self.snapshot_path.mkdir(parents=True, exist_ok=True)
        
        self.memory_file = self.snapshot_path / "session_memory.json"
        self.active_memory = self._load_memory()
        self.current_model = "bonsai-8b"
        self.max_react_iterations = 5

    def _load_memory(self):
        if self.memory_file.exists():
            try:
                with open(self.memory_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return []
        return []

    def _save_memory(self):
        with open(self.memory_file, 'w', encoding='utf-8') as f:
            json.dump(self.active_memory, f, indent=2, ensure_ascii=False)

    def route_intent(self, user_input: str) -> str:
        """Select model key. Multimodal routes are feature-flagged (unavailable by default)."""
        text = user_input.lower()
        experimental = bool(self.cfg.get("experimental_multimodal", False))
        if experimental:
            if any(w in text for w in ["audio", "escucha", "transcribe", "dictado"]):
                return self.cfg.get("asr_model", "bonsai-8b")
            if any(w in text for w in ["imagen", "ocr", "foto", "captura"]):
                return self.cfg.get("ocr_model", "bonsai-8b")
        # Prefer configured model, then catalog defaults
        return self.cfg.get("last_model") or self.current_model or "bonsai-8b"

    def get_dynamic_system_prompt(self, project_tree: str = "") -> str:
        """Construye el prompt del sistema inyectando la máscara activa + protocolo de tools."""
        from agent_loop import TOOL_PROTOCOL_HINT

        persona_name = self.cfg.get("active_persona", "default")
        personas_dir = self.data_dir / "personas"
        persona_path = personas_dir / f"{persona_name}.txt"

        if persona_path.exists():
            mask_content = persona_path.read_text(encoding="utf-8")
        else:
            # Prefer persona arquetypes when active_persona matches
            try:
                from persona import PERSONAS, build_persona_system

                if persona_name in PERSONAS:
                    mask_content = build_persona_system(persona_name)
                else:
                    mask_content = (
                        "Eres Bonsai, un agente IA soberano, técnico y eficiente."
                    )
            except Exception:
                mask_content = (
                    "Eres Bonsai, un agente IA soberano, técnico y eficiente."
                )

        system_prompt = f"{mask_content}\n\n{TOOL_PROTOCOL_HINT}\n"
        if project_tree:
            system_prompt += f"\n=== CONTEXTO DEL PROYECTO ===\n{project_tree}\n"
        return system_prompt

    def get_available_personas(self) -> list:
        """Retorna una lista de las máscaras disponibles."""
        personas_dir = self.data_dir / "personas"
        if not personas_dir.exists():
            return ["default"]
        return [f.stem for f in personas_dir.glob("*.txt")]

    def update_persona(self, persona_name: str, persona_content: str) -> str:
        """Guarda una nueva máscara semántica y la activa."""
        personas_dir = self.data_dir / "personas"
        personas_dir.mkdir(parents=True, exist_ok=True)
        
        safe_name = "".join([c for c in persona_name if c.isalpha() or c.isdigit() or c==' ']).rstrip().replace(" ", "_").lower()
        file_path = personas_dir / f"{safe_name}.txt"
        file_path.write_text(persona_content, encoding='utf-8')
        
        self.cfg["active_persona"] = safe_name
        save_config(self.cfg)
        return f"MÁSCARA ACTUALIZADA: He asimilado la personalidad '{safe_name}'."

    def reflect(self, query: str, answer: str, answer2: str, source_ids: list, success: bool):
        """Reflection Pass: Adjusts reward/failure signals using confidence-weighted logic."""
        from cognitive_diff import jaccard_similarity
        from distill import reasoning_density

        density = min(1.0, reasoning_density(answer) * 5)
        stability = jaccard_similarity(answer, answer2)

        conn = sqlite3.connect(str(ROOT / "data" / "vector_db" / "memory.db"))
        try:
            cursor = conn.cursor()
            for sid in (source_ids or [])[:3]:
                row = cursor.execute(
                    "SELECT usage_count FROM chunks WHERE id = ?", (sid,)
                ).fetchone()
                if not row:
                    continue
                usage = row[0] or 0
                reuse = min(1.0, usage / 10)
                confidence = (density * 0.4) + (stability * 0.3) + (reuse * 0.3)
                reward_delta = 0.03 + (confidence * 0.2)
                if success:
                    cursor.execute(
                        """UPDATE chunks SET reward = MIN(reward + ?, 5.0),
                           usage_count = usage_count + 1 WHERE id = ?""",
                        (reward_delta, sid),
                    )
                else:
                    cursor.execute(
                        """UPDATE chunks SET failure_count = failure_count + 1,
                           reward = reward * 0.8, decay = decay * 0.9 WHERE id = ?""",
                        (sid,),
                    )
            conn.commit()
        finally:
            conn.close()

    def contradiction_challenge(self, chunk_id: int, model_client):
        """Challenge high-ranking chunks and suppress semantic neighborhood (distributed) to detect bias."""
        conn = sqlite3.connect(str(ROOT / "data" / "vector_db" / "memory.db"))
        try:
            row = conn.execute("SELECT content, hash FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
            if not row:
                return
            content, chash = row
            
            # Generar desafío
            challenge = model_client.chat_completion("bonsai-8b", [{"role": "user", "content": f"Refute this reasoning:\n{content}"}])
            
            # Refinamiento: evaluar refutación por densidad, no por string literal
            from distill import reasoning_density
            confidence = reasoning_density(challenge.get("content", ""))
            
            if confidence > 0.05: # Umbral de refutación lógica
                 # Castigo principal
                 conn.execute("UPDATE chunks SET failure_count = failure_count + 2, reward = reward * 0.7 WHERE id = ?", (chunk_id,))
                 # Supresión distribuida por fingerprint semántico
                 conn.execute("UPDATE chunks SET reward = reward * 0.9, failure_count = failure_count + 1 WHERE hash = ? AND id != ?", (chash, chunk_id))
            conn.commit()
        finally:
            conn.close()

    def _persona_tool_allowlist(self) -> set[str] | frozenset[str] | None:
        """Canonical persona allowlist via persona.resolve_persona_tools (no silent elevation)."""
        try:
            from persona import PERSONAS, resolve_persona_tools

            pkey = self.cfg.get("active_persona")
            if pkey not in PERSONAS:
                pkey = "investigador"
            return resolve_persona_tools(pkey)
        except Exception:
            # Fail closed: None → agent_loop drops POWER_TOOLS
            return None

    def process_interaction(self, user_input: str, model_client) -> str:
        """Unified ReAct loop using XML tool_call protocol (agent_loop)."""
        from agent_loop import interactive_react

        current_system_prompt = self.get_dynamic_system_prompt()
        if not self.active_memory or self.active_memory[0].get("role") != "system":
            self.active_memory.insert(
                0, {"role": "system", "content": current_system_prompt}
            )
        else:
            self.active_memory[0]["content"] = current_system_prompt

        target_model = self.route_intent(user_input)
        self.current_model = target_model

        persona_tools = self._persona_tool_allowlist()
        toolbox = self.tools if hasattr(self.tools, "read_file") else get_toolbox()

        answer, msgs, tool_results = interactive_react(
            user_input,
            model_client,
            self.active_memory,
            model=target_model,
            toolbox=toolbox,
            persona_tools=persona_tools,
            max_iterations=self.max_react_iterations,
        )
        self.active_memory = msgs
        self._save_memory()
        if tool_results:
            print(f"[Tools] {len(tool_results)} call(s)")
        return answer


# ─── (Resto del código original sin cambios, omitido por brevedad en edición) ────────────────

def acquire_lock():
    """Garantiza instancia única mediante un PID file de forma robusta."""
    if BONSAI_LOCK.exists():
        try:
            pid = int(BONSAI_LOCK.read_text().strip())
            os.kill(pid, 0)
            
            is_alive = True
            if platform.system() != "Windows":
                try:
                    cmdline_path = Path(f"/proc/{pid}/cmdline")
                    if cmdline_path.exists():
                        cmdline = cmdline_path.read_text().lower()
                        if "python" not in cmdline and "bonsai" not in cmdline:
                            is_alive = False
                except Exception:
                    pass
            
            if is_alive:
                err(
                    f"Ya existe una instancia de Bonsai en ejecución (PID: {pid}).\n"
                    f"  Para forzar el inicio, cierra la otra instancia o borra: {BONSAI_LOCK}"
                )
            else:
                warn(f"PID {pid} en lockfile no parece ser Bonsai. Limpiando...")
                BONSAI_LOCK.unlink(missing_ok=True)
                
        except (ValueError, OSError, ProcessLookupError):
            warn("Bonsai detectó un archivo lock antiguo o proceso inexistente. Limpiando...")
            BONSAI_LOCK.unlink(missing_ok=True)

    BONSAI_LOCK.write_text(str(os.getpid()))
    atexit.register(lambda: BONSAI_LOCK.unlink(missing_ok=True))

# Unlock state kept ONLY in RAM — never written to disk
_server_unlocked = False
_server_password: str | None = None


def try_yubikey_unlock() -> str | None:
    """Attempt YubiKey challenge-response to get auto-unlock password.

    Calls: ykchalresp -2 'Kayab-Node-Unlock'
    Returns the response string if successful, None if failed.
    """
    if not shutil.which("ykchalresp"):
        warn("YubiKey: binario 'ykchalresp' no encontrado. Desbloqueo por hardware saltado.")
        return None

    try:
        result = subprocess.run(
            ["ykchalresp", "-2", "Kayab-Node-Unlock"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            response = result.stdout.strip()
            ok("YubiKey: desbloqueado vía hardware token")
            return response
        else:
            stderr = result.stderr.strip() if result.stderr else "Error desconocido"
            warn(f"YubiKey: fallo en el desafío (code {result.returncode}): {stderr}")
    except subprocess.TimeoutExpired:
        warn("YubiKey: el hardware no respondió a tiempo (timeout).")
    except Exception as e:
        warn(f"YubiKey: error inesperado: {e}")
    return None


def set_server_unlocked(password: str | None):
    """Set unlock state - password stays in RAM only."""
    global _server_unlocked, _server_password
    _server_unlocked = True
    _server_password = password


def is_server_unlocked() -> bool:
    """Check if server is unlocked (RAM only)."""
    return _server_unlocked


def get_server_password() -> str | None:
    """Get unlock password from RAM only. NEVER from disk."""
    return _server_password


# ─── MODELOS DISPONIBLES ──────────────────────────────────────────────────────
MODEL_CATALOG = {
    "gemma-4-e2b": {
        "file": "Gemma-4-E2B-Uncensored-HauhauCS-Aggressive-IQ3_M.gguf",
        "name": "Gemma 4 E2B Multimodal (Uncensored)",
        "profile": "general",
        "ctx": 32768,
        "hf_url": None,
    },
    "bonsai-8b": {
        "file": "Bonsai-8B.gguf",
        "name": "Bonsai 8B (1.15 GB)",
        "profile": "thesis",
        "ctx": 65536,
        "hf_url": "https://huggingface.co/prism-ml/Bonsai-8B-gguf/resolve/main/Bonsai-8B.gguf",
        "sha256": "284a335aa3fb2ced3b1b01fcb40b08aa783e3b70832767f0dd2e3fdfa134bd54",
    },
}

# Contexto por defecto para .gguf fuera del catálogo (descubrimiento libre)
DEFAULT_MODEL_CTX = runtime_core.DEFAULT_MODEL_CTX

# ─── DETECCIÓN DE PLATAFORMA ──────────────────────────────────────────────────


def detect_platform() -> tuple[str, str]:
    """Retorna (os_key, arch_key) — implementación compartida en runtime_core."""
    return runtime_core.detect_platform()


def get_server_binary(os_key: str, arch_key: str) -> Path:
    """Encuentra el binario llama-server para esta plataforma."""
    try:
        found, is_fallback = runtime_core.find_server_binary(BIN_DIR, os_key, arch_key)
    except LookupError:
        err(
            f"No se encontró llama-server para {os_key}/{arch_key}.\n"
            f"  Ejecuta: python launcher.py --setup\n"
            f"  O descarga el asset llama-*-bin-*.tar.gz desde: "
            f"https://github.com/{runtime_core.BIN_RELEASE_REPO}/releases"
        )
    if is_fallback:
        warn(f"Binario local no encontrado. Usando sistema: {found}")
    return found


# ─── SELECCIÓN DE MODELO ──────────────────────────────────────────────────────


def find_available_models() -> list[tuple[str, dict]]:
    """Catálogo oficial + cualquier .gguf en /models (runtime_core)."""
    return runtime_core.find_available_models(MODELS, MODEL_CATALOG)


def pick_model(requested: str | None) -> tuple[str, dict, Path]:
    """Resuelve el modelo a usar (runtime_core, mensajes propios)."""
    return runtime_core.pick_model(
        requested,
        models_dir=MODELS,
        catalog=MODEL_CATALOG,
        error=err,
        warn=warn,
        no_models_msg=(
            "No hay modelos descargados.\n"
            "  Ejecuta: python launcher.py --setup\n"
            "  O copia un .gguf a la carpeta /models/"
        ),
    )


# ─── CARGA DE CONFIGURACIÓN ───────────────────────────────────────────────────


def security_active() -> bool:
    """True solo si owner.lock existe Y DEBUG_MODE=False."""
    return not DEBUG_MODE and OWNER_LOCK.exists()


def load_config() -> dict:
    """Config persistente + política de bind (runtime_core)."""
    return runtime_core.load_config(CFG_FILE)


def save_config(cfg: dict):
    runtime_core.save_config(CFG_FILE, cfg)


# ─── SERVIDOR ─────────────────────────────────────────────────────────────────


def wait_for_server(host: str, port: int, timeout: int = 30, proc: subprocess.Popen = None) -> bool:
    import urllib.request

    url = f"http://{host}:{port}/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc and proc.poll() is not None:
            # El proceso murió prematuramente
            return False
            
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def build_server_cmd(
    binary: Path, model_path: Path, meta: dict, cfg: dict
) -> list[str]:
    cmd = [
        str(binary),
        "-m",
        str(model_path),
        "--host",
        cfg["host"],
        "--port",
        str(cfg["port"]),
        "--ctx-size",
        str(meta["ctx"]),
        "--log-disable",
    ]
    if cfg.get("ngl", 0) != 0:
        cmd += ["-ngl", str(cfg["ngl"])]
    if cfg.get("threads", 0) > 0:
        cmd += ["-t", str(cfg["threads"])]
    return cmd

# ... (seguimiento del resto del archivo omitido por brevedad, se mantiene igual)

def main():
    """Compatibility entry: delegate to unified root launcher."""
    from launcher import main as launcher_main

    return launcher_main()


if __name__ == "__main__":
    main()
