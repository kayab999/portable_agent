#!/usr/bin/env python3
"""CRS execution step — thin wrapper over unified agent_loop."""

from __future__ import annotations

from agent_loop import agent_step
from agentportable import BonsaiOrchestrator
from tools import get_toolbox

CFG = {"active_persona": "default", "active_snapshot": "v1"}
ORCHESTRATOR = BonsaiOrchestrator(CFG, get_toolbox())
TOOLBOX = get_toolbox()


def execution_step(perception, model_client, task_state):
    print(f"\n[Perception]: {perception}")
    system_prompt = ORCHESTRATOR.get_dynamic_system_prompt()
    model = ORCHESTRATOR.current_model or "bonsai-8b"

    result = agent_step(
        perception,
        model_client,
        task_state,
        system_prompt=system_prompt,
        model=model,
        toolbox=TOOLBOX,
        # rc.5 containment: the experience pipeline is experimental and off.
        persist=False,
        drain_queue=False,
    )

    if result.get("status") == "error":
        print(f"[Error]: {result.get('message')}")
        return result

    exp = result.get("experience") or {}
    print(f"[Plan]: {exp.get('plan', '')[:500]}")
    print(f"[Result]: {exp.get('result', '')[:500]}")
    print(f"[Success]: {result.get('success')}")
    return result
