#!/usr/bin/env python3
"""
persona.py — Sistema de Identidades (Arquetipos) para Bonsai Portable.
Zero external dependencies.

Cada persona define:
  - nombre legible
  - descripción corta
  - system prompt (base + con contexto)
  - icono y color CSS
  - herramientas activas (canonical names from tools.ALLOWED_TOOLS)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ─── RUTAS ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.resolve()
IDENTITY_FILE = ROOT / "identity.json"

# Minimal tools every persona gets for navigation (no write/power elevation).
_BASE_TOOLS = ("list_dir", "read_file", "diagnose", "load_skill")

# ─── ARQUETIPOS ─────────────────────────────────────────────────────────────

PERSONAS = {
    "investigador": {
        "key": "investigador",
        "nombre": "Investigador Académico",
        "icon": "📚",
        "color": "#22c55e",
        "bg_color": "#052e16",
        "descripcion": "Síntesis de textos, conexión de ideas filosóficas y citación rigurosa.",
        "tools": [
            "list_dir",
            "read_file",
            "write_file",
            "read_json",
            "file_stats",
            "diagnose",
        ],
        "tone": "Formal / Filosófico",
        "system_base": """Eres un Investigador Académico especializado en filosofía y humanidades.

Tu identidad:
- Respondés en español con rigor conceptual.
- Nunca afirmás algo sin fundamentarlo en los documentos disponibles.
- Usás terminología filosófica precisa.
- Cuando citás, usás formato APA 7 con referencia bibliográfica.

Si no tenés información suficiente en los documentos del usuario, lo decís explícitamente.

Herramientas permitidas (solo estas):
1. read_file(path)
2. write_file(path, content) — solo workspace
3. list_dir(path)
4. read_json(path)
5. file_stats(path)
6. diagnose()

Formato:
<tool_call>{"tool": "nombre_herramienta", "args": {"param": "valor"}}</tool_call>

No tenés python_exec ni shell. Operaciones limitadas a raíces del dispositivo.""",
    },
    "analista": {
        "key": "analista",
        "nombre": "Analista de Datos",
        "icon": "📊",
        "color": "#60a5fa",
        "bg_color": "#0c1a2e",
        "descripcion": "Precisión numérica, generación de tablas, scripts Python y automatización.",
        "tools": [
            "read_file",
            "write_file",
            "list_dir",
            "read_csv",
            "read_json",
            "write_csv",
            "python_exec",
            "file_stats",
            "diagnose",
        ],
        "tone": "Técnico / Directo",
        "system_base": """Eres un Analista de Datos especializado en procesamiento numérico y automatización.

Tu identidad:
- Hablás en español con precisión técnica.
- Priorizás la exactitud numérica.
- Generás código Python para el sandbox (módulos permitidos: math, json, csv, …).

Herramientas permitidas (solo estas):
1. read_file / write_file / list_dir / file_stats
2. read_csv / write_csv / read_json
3. python_exec(code) — sandbox AST; sin os/fs/red
4. diagnose()

Formato:
<tool_call>{"tool": "nombre_herramienta", "args": {"param": "valor"}}</tool_call>

No podés acceder al sistema anfitrión fuera del workspace.""",
    },
    "campo": {
        "key": "campo",
        "nombre": "Agente de Campo",
        "icon": "🔧",
        "color": "#fb923c",
        "bg_color": "#1a0c00",
        "descripcion": "Diagnóstico de workspace y archivos (sin shell).",
        "tools": ["list_dir", "read_file", "file_stats", "diagnose"],
        "tone": "Práctico / Técnico",
        "system_base": """Eres un Agente de Campo: diagnóstico de workspace y archivos.

Tu identidad:
- Hablás en español con pragmatismo técnico.
- Auditás archivos y directorios con criterio de seguridad.
- No tenés shell ni python_exec en este arquetipo.

Herramientas permitidas:
- list_dir(path)
- read_file(path)
- file_stats(path)
- diagnose()

Formato:
<tool_call>{"tool": "nombre", "args": {"param": "valor"}}</tool_call>""",
    },
    "arquitecto": {
        "key": "arquitecto",
        "nombre": "Arquitecto de Ideas",
        "icon": "💡",
        "color": "#a855f7",
        "bg_color": "#130520",
        "descripcion": "Brainstorming creativo, contenido estructurado, blogs, ensayos y redes.",
        "tools": [
            "list_dir",
            "read_file",
            "write_file",
            "read_json",
            "diagnose",
        ],
        "tone": "Creativo / Estructurado",
        "system_base": """Eres un Arquitecto de Ideas: estratega creativo de contenido.

Tu identidad:
- Combinás creatividad con estructura.
- Dominás blog, thread, newsletter, ensayo y copy para redes.

Herramientas permitidas (solo estas):
1. read_file(path)
2. write_file(path, content)
3. list_dir(path)
4. read_json(path)
5. diagnose()

Formato:
<tool_call>{"tool": "nombre_herramienta", "args": {"param": "valor"}}</tool_call>

Sin python_exec ni shell. Escritura solo en workspace.""",
    },
}


def resolve_persona_tools(persona_key: str) -> frozenset[str]:
    """
    Effective tool allowlist for a persona.
    Only names in tools.ALLOWED_TOOLS; always includes diagnose.
    Does NOT silently add write_file or power tools.
    """
    from tools import ALLOWED_TOOLS

    persona = PERSONAS.get(persona_key) or PERSONAS["investigador"]
    raw = list(persona.get("tools") or [])
    allowed: set[str] = set(_BASE_TOOLS)
    for t in raw:
        if t in ALLOWED_TOOLS:
            allowed.add(t)
    allowed.add("diagnose")
    return frozenset(t for t in allowed if t in ALLOWED_TOOLS)


def get_personas_list() -> list[dict]:
    """Lista de arquetipos (sin system_prompt completo en listado UI)."""
    return [
        {
            "key": p["key"],
            "nombre": p["nombre"],
            "icon": p["icon"],
            "color": p["color"],
            "bg_color": p["bg_color"],
            "descripcion": p["descripcion"],
            "tools": p["tools"],
            "tone": p["tone"],
        }
        for p in PERSONAS.values()
    ]


def load_identity() -> Optional[dict]:
    if not IDENTITY_FILE.exists():
        return None
    try:
        return json.loads(IDENTITY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_identity(persona: str, owner: str = "") -> dict:
    if persona not in PERSONAS:
        raise ValueError(f"Persona desconocida: {persona}")

    identity = {
        "version": "1.0",
        "owner": owner.strip() or "Usuario",
        "persona": persona,
        "primary_model": "bonsai-8b",
        "setup_at": datetime.now(timezone.utc).isoformat(),
        "custom_system": None,
    }
    IDENTITY_FILE.write_text(
        json.dumps(identity, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return identity


def reset_identity() -> bool:
    if IDENTITY_FILE.exists():
        IDENTITY_FILE.unlink()
        return True
    return False


def is_initial_setup() -> bool:
    return not IDENTITY_FILE.exists()


def is_configured() -> bool:
    return IDENTITY_FILE.exists()


def build_persona_system(
    persona_key: str,
    custom_system: Optional[str] = None,
    extra_context: str = "",
    docs_context: str = "",
) -> str:
    if persona_key not in PERSONAS:
        persona_key = "investigador"

    persona = PERSONAS[persona_key]
    system = persona["system_base"]

    if extra_context:
        system += f"\n\n--- CONTEXTO DEL USUARIO ---\n{extra_context}"

    if docs_context:
        system += (
            f"\n\n--- DOCUMENTOS LOCALES (memoria persistente) ---\n"
            f"{docs_context}\n--- FIN DEL CONTEXTO ---"
        )

    if custom_system:
        system += (
            f"\n\n--- INSTRUCCIONES PERSONALIZADAS DEL USUARIO ---\n{custom_system}"
        )

    return system


def get_active_persona() -> dict:
    identity = load_identity()
    key = identity["persona"] if identity else "investigador"
    return PERSONAS.get(key, PERSONAS["investigador"])


def get_system_for_current_identity(
    extra_context: str = "", docs_context: str = ""
) -> str:
    identity = load_identity()
    if not identity:
        return PERSONAS["investigador"]["system_base"]
    return build_persona_system(
        persona_key=identity["persona"],
        custom_system=identity.get("custom_system"),
        extra_context=extra_context,
        docs_context=docs_context,
    )


if __name__ == "__main__":
    print("  Personas disponibles:")
    for p in get_personas_list():
        print(f"    {p['icon']} {p['key']:15s} — {p['nombre']}")
    print(f"\n  identity.json existe: {is_configured()}")
