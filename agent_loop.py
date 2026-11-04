#!/usr/bin/env python3
"""
agent_loop.py — Single agent execution contract for Bonsai Portable RC.

Canonical tool protocol:
  <tool_call>{"tool": "name", "args": {...}}</tool_call>

Fenced/standalone JSON and ReAct "TOOL:" lines are legacy opt-in only
(BONSAI_LEGACY_TOOL_PARSING=1).
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Protocol

from local_memory import DB_PATH, retrieve
from tools import (
    ALLOWED_TOOLS,
    execute_tool_calls,
    format_tool_results,
    get_toolbox,
    parse_tool_calls,
)

ROOT = Path(__file__).parent.resolve()

TOOL_PROTOCOL_HINT = """
=== HERRAMIENTAS (formato obligatorio) ===
Si necesitas una herramienta, incluye UNICAMENTE bloques:
<tool_call>{"tool": "nombre", "args": {"param": "valor"}}</tool_call>

Herramientas típicas: read_file, write_file, list_dir, file_stats, read_csv,
read_json, write_csv, python_exec, execute_command, diagnose, fetch_url.

Cuando termines, responde al usuario en texto normal (sin tool_call).
""".strip()


class ModelClient(Protocol):
    def chat_completion(self, model: str, messages: list[dict]) -> dict: ...


def legacy_tool_line_to_calls(text: str) -> list[dict]:
    """Translate 'TOOL: name argstring' into structured calls when possible."""
    calls: list[dict] = []
    for match in re.finditer(r"TOOL:\s*(\w+)\s*(.*)", text, re.IGNORECASE):
        name = match.group(1).strip()
        rest = match.group(2).strip()
        if name not in ALLOWED_TOOLS:
            calls.append({"tool": name, "args": {}})
            continue
        # Prefer JSON args if present
        if rest.startswith("{"):
            try:
                args = json.loads(rest)
                if isinstance(args, dict):
                    calls.append({"tool": name, "args": args})
                    continue
            except json.JSONDecodeError:
                pass
        # Heuristic by tool signature
        if name in ("execute_command",):
            calls.append({"tool": name, "args": {"command": rest}})
        elif name in ("read_file", "write_file", "list_dir", "file_stats", "read_csv", "read_json"):
            # path as first token; write_file needs more — keep path only
            path = rest.split()[0] if rest else "."
            if name == "write_file" and " " in rest:
                path, content = rest.split(" ", 1)
                calls.append({"tool": name, "args": {"path": path, "content": content}})
            else:
                calls.append({"tool": name, "args": {"path": path}})
        elif name == "python_exec":
            calls.append({"tool": name, "args": {"code": rest}})
        elif name == "diagnose":
            calls.append({"tool": name, "args": {}})
        else:
            calls.append({"tool": name, "args": {"command": rest} if rest else {}})
    return calls


def extract_tool_calls(text: str) -> list[dict]:
    """Parse canonical tool_calls; legacy lines only when explicitly enabled."""
    try:
        calls = parse_tool_calls(text)
    except ValueError:
        calls = []
    if calls:
        return calls
    if is_legacy_tool_parsing_enabled():
        return legacy_tool_line_to_calls(text)
    return []


def is_legacy_tool_parsing_enabled() -> bool:
    import os

    return os.environ.get("BONSAI_LEGACY_TOOL_PARSING", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def extract_final_answer(text: str) -> str | None:
    m = re.search(r"FINAL ANSWER:\s*(.*)", text, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(1).strip()
    return None


def parse_reflection(text: str) -> dict:
    """
    Prefer JSON {"success": bool, "reflection": str}.
    Fallback: keyword heuristics.
    """
    raw = (text or "").strip()
    # Try fenced or bare JSON
    for candidate in (raw,):
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(candidate[start : end + 1])
                if isinstance(data, dict) and "success" in data:
                    return {
                        "success": bool(data.get("success")),
                        "reflection": str(data.get("reflection", raw)),
                        "raw": raw,
                    }
            except json.JSONDecodeError:
                pass
    lower = raw.lower()
    success = ("success" in lower or "éxito" in lower or "exito" in lower) and (
        "fail" not in lower and "error" not in lower and "fracaso" not in lower
    )
    if "fail" in lower or "error" in lower or "fracaso" in lower:
        success = False
    return {"success": success, "reflection": raw, "raw": raw}


def enqueue_experience(
    experience: dict,
    *,
    db_path: Path | None = None,
) -> None:
    path = Path(db_path) if db_path is not None else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        # Ensure queue exists even if TFIDFIndex not opened yet
        conn.execute(
            """CREATE TABLE IF NOT EXISTS event_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                timestamp INTEGER NOT NULL
            )"""
        )
        conn.execute(
            "INSERT INTO event_queue (event_type, payload, timestamp) VALUES (?, ?, ?)",
            ("PERSIST_EXPERIENCE", json.dumps(experience, ensure_ascii=False), int(time.time())),
        )
        conn.commit()
    finally:
        conn.close()


def drain_experience_queue(
    *,
    db_path: Path | None = None,
    max_events: int = 50,
) -> int:
    """Process queued experiences into CRS. Returns count processed."""
    from audit_worker import audit_worker

    return audit_worker(db_path=db_path, max_events=max_events)


def filter_calls_by_persona(
    calls: list[dict],
    allowed: set[str] | frozenset[str] | None,
) -> list[dict]:
    """
    Filter tool calls by persona allowlist.
    None → fail-closed: ALLOWED_TOOLS minus POWER_TOOLS (safe default).
    Empty set → only diagnose.
    """
    from tools import POWER_TOOLS

    if allowed is None:
        # Fail closed: no unrestricted power surface when caller omitted persona
        allowed_set = set(ALLOWED_TOOLS - POWER_TOOLS)
    else:
        allowed_set = set(allowed)
    allowed_set |= {"diagnose"}
    out = []
    for c in calls:
        name = c.get("tool", "")
        permitted = name in allowed_set and (
            name in ALLOWED_TOOLS or name.startswith("mcp__")
        )
        if permitted:
            out.append(c)
        else:
            out.append(
                {
                    "tool": name,
                    "args": c.get("args", {}),
                    "_denied": True,
                    "_reason": f"herramienta no permitida por persona: {name}",
                }
            )
    return out


def _hook_messages(messages: list[dict], limit: int = 4) -> list[dict]:
    return [
        {
            "role": message.get("role"),
            "content": str(message.get("content", ""))[:2000],
        }
        for message in messages[-limit:]
    ]


def run_tool_cycle(
    reply_text: str,
    toolbox=None,
    *,
    persona_tools: set[str] | frozenset[str] | None = None,
    registry=None,
    hooks=None,
    permissions=None,
) -> tuple[list[dict], str]:
    """
    Parse + execute tools from model text.
    Returns (results, formatted_observation).
    Denied persona tools become synthetic error results.
    """
    toolbox = toolbox or get_toolbox()
    try:
        calls = extract_tool_calls(reply_text)
    except ValueError:
        return [], "Tool parsing failed (Invalid JSON)"

    if not calls:
        return [], ""

    marked = filter_calls_by_persona(calls, persona_tools)
    real = [c for c in marked if not c.get("_denied")]
    denied = [c for c in marked if c.get("_denied")]

    results = (
        execute_tool_calls(
            real, toolbox, registry=registry, hooks=hooks, permissions=permissions
        )
        if real
        else []
    )
    for d in denied:
        results.append(
            {
                "tool": d.get("tool", "unknown"),
                "ok": False,
                "error": "herramienta_no_permitida",
                "message": d.get("_reason", "denied"),
            }
        )
    return results, format_tool_results(results)


def agent_step(
    perception: str,
    model_client: ModelClient,
    task_state: dict,
    *,
    system_prompt: str,
    model: str = "bonsai-8b",
    toolbox=None,
    persona_tools: set[str] | frozenset[str] | None = None,
    registry=None,
    hooks=None,
    permissions=None,
    max_tool_rounds: int = 3,
    persist: bool = False,
    db_path: Path | None = None,
    drain_queue: bool = False,
    use_memory: bool = False,
) -> dict:
    """
    One agent step: retrieve → plan/tools → reflect → optionally persist.

    Retrieval and persistence are opt-in while RAG/experience storage remain
    experimental; the default path touches no database.
    """
    toolbox = toolbox or get_toolbox()
    memory_context = (
        retrieve(perception, top_k=3, db_path=db_path) if use_memory else ""
    )

    messages = [
        {"role": "system", "content": f"{system_prompt}\n\n{TOOL_PROTOCOL_HINT}"},
        {
            "role": "user",
            "content": (
                f"Contexto memoria:\n{memory_context or '(vacío)'}\n\n"
                f"Task: {task_state.get('goal', '')}\n\n"
                f"Input: {perception}"
            ),
        },
    ]

    max_retries = 2
    response = None
    for attempt in range(max_retries):
        if hooks is not None:
            decision = hooks.run(
                "pre_llm",
                {"messages": _hook_messages(messages), "attempt": attempt},
                matcher="agent_step",
            )
            if decision.blocked:
                task_state["status"] = "blocked"
                task_state["message"] = f"Blocked by hook: {decision.reason}"
                return task_state
        try:
            response = model_client.chat_completion(model, messages)
            break
        except Exception as e:
            if attempt == max_retries - 1:
                task_state["status"] = "error"
                task_state["message"] = f"LLM unavailable after {max_retries} attempts: {e}"
                return task_state
            time.sleep(1)

    plan = (response or {}).get("content", "")
    action_result = "No tool executed"
    all_results: list[dict] = []

    for _round in range(max_tool_rounds):
        try:
            results, formatted = run_tool_cycle(
                plan,
                toolbox,
                persona_tools=persona_tools,
                registry=registry,
                hooks=hooks,
                permissions=permissions,
            )
        except ValueError:
            messages.append({"role": "assistant", "content": plan})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Respuesta inválida. Formatea herramientas como:\n"
                        '<tool_call>{"tool": "nombre", "args": {}}</tool_call>'
                    ),
                }
            )
            try:
                response = model_client.chat_completion(model, messages)
                plan = response.get("content", "")
                continue
            except Exception as e:
                action_result = f"Tool recovery failed: {e}"
                break

        if not results:
            action_result = "No tool executed"
            break

        all_results.extend(results)
        action_result = formatted
        messages.append({"role": "assistant", "content": plan})
        messages.append(
            {
                "role": "user",
                "content": f"Resultados de herramientas:\n{formatted}\nContinúa o responde al usuario.",
            }
        )
        try:
            response = model_client.chat_completion(model, messages)
            plan = response.get("content", "")
        except Exception as e:
            action_result = f"{formatted}\n(LLM follow-up failed: {e})"
            break

        # If model returned final answer without new tools, stop
        if not extract_tool_calls(plan):
            break

    final = extract_final_answer(plan) or plan

    # Reflection (structured)
    reflection_prompt = (
        f"Plan:\n{plan}\n\nResult:\n{action_result}\n\n"
        'Responde SOLO JSON: {"success": true|false, "reflection": "..."}'
    )
    try:
        reflection = model_client.chat_completion(
            model, [{"role": "user", "content": reflection_prompt}]
        )
        ref_parsed = parse_reflection(reflection.get("content", ""))
    except Exception as e:
        ref_parsed = {
            "success": False,
            "reflection": f"reflection_error: {e}",
            "raw": "",
        }

    experience = {
        "query": perception,
        "plan": plan,
        "result": action_result,
        "final": final,
        "reflection": ref_parsed["reflection"],
        "success": ref_parsed["success"],
        "tool_results": [
            {"tool": r.get("tool"), "ok": r.get("ok")} for r in all_results
        ],
    }

    if persist:
        enqueue_experience(experience, db_path=db_path)
        if drain_queue:
            try:
                drain_experience_queue(db_path=db_path)
            except Exception:
                pass

    if ref_parsed["success"]:
        task_state["done"] = True
    task_state["steps"] = int(task_state.get("steps", 0)) + 1
    task_state["status"] = task_state.get("status") or "ok"
    task_state["final"] = final
    task_state["success"] = ref_parsed["success"]
    task_state["experience"] = experience
    return task_state


def interactive_react(
    user_input: str,
    model_client: ModelClient,
    messages: list[dict],
    *,
    model: str = "bonsai-8b",
    toolbox=None,
    persona_tools: set[str] | frozenset[str] | None = None,
    registry=None,
    hooks=None,
    permissions=None,
    max_iterations: int = 5,
) -> tuple[str, list[dict], list[dict]]:
    """
    Multi-turn ReAct for chat UIs/CLI.
    Returns (final_text, updated_messages, tool_results_flat).
    """
    toolbox = toolbox or get_toolbox()
    messages = list(messages)
    messages.append({"role": "user", "content": user_input})
    tool_results_flat: list[dict] = []

    for _ in range(max_iterations):
        if hooks is not None:
            decision = hooks.run(
                "pre_llm",
                {"messages": _hook_messages(messages)},
                matcher="interactive_react",
            )
            if decision.blocked:
                message = f"⛔ Bloqueado por hook: {decision.reason}"
                messages.append({"role": "assistant", "content": message})
                return message, messages, tool_results_flat
        response = model_client.chat_completion(model, messages)
        reply = response.get("content", "")

        final = extract_final_answer(reply)
        results, formatted = run_tool_cycle(
            reply,
            toolbox,
            persona_tools=persona_tools,
            registry=registry,
            hooks=hooks,
            permissions=permissions,
        )

        if results:
            tool_results_flat.extend(results)
            messages.append({"role": "assistant", "content": reply})
            messages.append(
                {
                    "role": "user",
                    "content": f"Resultados de herramientas:\n{formatted}\nContinúa o da la respuesta final.",
                }
            )
            continue

        answer = final if final is not None else reply
        messages.append({"role": "assistant", "content": answer})
        return answer, messages, tool_results_flat

    messages.append(
        {
            "role": "assistant",
            "content": "⚠️ Bonsai: límite de iteraciones alcanzado.",
        }
    )
    return messages[-1]["content"], messages, tool_results_flat
