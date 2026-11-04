#!/usr/bin/env python3
"""hooks.py — Declarative lifecycle hooks.

Hooks are arbitrary local commands, so they are opt-in, declared only in the
owner's configuration, executed without a shell, with a sanitized environment,
a hard timeout, and an output cap.

Protocol:
  - JSON payload on stdin
  - exit code 0 = allow
  - exit code 2 = block (Claude-compatible convention)
  - other non-zero = warning, does not block
  - optional stdout JSON: {"decision": "allow"|"block", "reason": "..."}
  - pre_tool / user_prompt / pre_llm timeouts fail closed
"""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
from dataclasses import dataclass, field

EVENTS = (
    "session_start",
    "user_prompt",
    "pre_llm",
    "pre_tool",
    "post_tool",
    "session_end",
)
_FAIL_CLOSED_EVENTS = frozenset({"user_prompt", "pre_llm", "pre_tool"})
_MAX_OUTPUT = 4096
_ENV_ALLOW = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
)


@dataclass(frozen=True)
class HookSpec:
    event: str
    command: str
    args: tuple[str, ...] = ()
    timeout_ms: int = 5000
    matcher: str = ""
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class HookResult:
    blocked: bool = False
    reason: str = ""
    outputs: list[dict] = field(default_factory=list)


class HookError(ValueError):
    """Raised for invalid hook configuration."""


class HookManager:
    def __init__(
        self,
        specs: dict[str, list[HookSpec]] | None = None,
        *,
        enabled: bool = False,
        cwd: str | os.PathLike | None = None,
    ):
        self.specs = specs or {}
        self.enabled = bool(enabled)
        self.cwd = str(cwd) if cwd else None

    @classmethod
    def from_config(cls, config: dict | None, *, cwd=None) -> "HookManager":
        config = config if isinstance(config, dict) else {}
        enabled = bool(config.get("enabled", False))
        raw_events = config.get("events")
        if not isinstance(raw_events, dict):
            raw_events = {
                key: value for key, value in config.items() if key in EVENTS
            }
        specs: dict[str, list[HookSpec]] = {event: [] for event in EVENTS}
        for event, entries in raw_events.items():
            if event not in EVENTS:
                continue
            if isinstance(entries, dict):
                entries = [entries]
            if not isinstance(entries, list):
                raise HookError(f"hooks.{event} must be a list")
            for entry in entries:
                if not isinstance(entry, dict):
                    raise HookError(f"hooks.{event} entries must be objects")
                command = str(entry.get("command") or "").strip()
                if not command:
                    raise HookError(f"hooks.{event}: command is required")
                args = entry.get("args") or []
                if not isinstance(args, list):
                    raise HookError(f"hooks.{event}: args must be a list")
                try:
                    timeout_ms = int(entry.get("timeout_ms", 5000))
                except (TypeError, ValueError):
                    timeout_ms = 5000
                timeout_ms = max(100, min(timeout_ms, 120_000))
                env = entry.get("env") or {}
                if not isinstance(env, dict):
                    raise HookError(f"hooks.{event}: env must be an object")
                specs[event].append(
                    HookSpec(
                        event=event,
                        command=command,
                        args=tuple(str(arg) for arg in args),
                        timeout_ms=timeout_ms,
                        matcher=str(entry.get("matcher") or ""),
                        env={str(k): str(v) for k, v in env.items()},
                    )
                )
        return cls(specs, enabled=enabled, cwd=cwd)

    def has_hooks(self, event: str) -> bool:
        return self.enabled and bool(self.specs.get(event))

    def _env(self, spec: HookSpec) -> dict[str, str]:
        env = {key: os.environ[key] for key in _ENV_ALLOW if key in os.environ}
        env.update(spec.env)
        return env

    @staticmethod
    def _matches(pattern: str, value: str | None) -> bool:
        if not pattern:
            return True
        if value is None:
            return False
        return fnmatch.fnmatchcase(value, pattern)

    def run(
        self,
        event: str,
        payload: dict,
        *,
        matcher: str | None = None,
    ) -> HookResult:
        if not self.enabled:
            return HookResult()
        outputs: list[dict] = []
        for spec in self.specs.get(event, []):
            if not self._matches(spec.matcher, matcher):
                continue
            outcome = self._run_one(spec, payload)
            outputs.append(outcome)
            if outcome.get("blocked"):
                return HookResult(
                    blocked=True,
                    reason=outcome.get("reason", "blocked by hook"),
                    outputs=outputs,
                )
        return HookResult(outputs=outputs)

    def _run_one(self, spec: HookSpec, payload: dict) -> dict:
        data = json.dumps(payload, ensure_ascii=False, default=str)
        try:
            proc = subprocess.run(
                [spec.command, *spec.args],
                input=data,
                text=True,
                capture_output=True,
                timeout=spec.timeout_ms / 1000.0,
                cwd=self.cwd,
                env=self._env(spec),
                shell=False,
            )
        except subprocess.TimeoutExpired:
            blocked = spec.event in _FAIL_CLOSED_EVENTS
            return {
                "hook": spec.command,
                "event": spec.event,
                "blocked": blocked,
                "reason": f"hook timed out after {spec.timeout_ms} ms",
            }
        except OSError as exc:
            blocked = spec.event in _FAIL_CLOSED_EVENTS
            return {
                "hook": spec.command,
                "event": spec.event,
                "blocked": blocked,
                "reason": f"hook could not start: {exc}",
            }

        stdout = (proc.stdout or "")[:_MAX_OUTPUT]
        stderr = (proc.stderr or "")[:_MAX_OUTPUT]
        outcome = {
            "hook": spec.command,
            "event": spec.event,
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "blocked": False,
            "reason": "",
        }

        if stdout.strip():
            try:
                parsed = json.loads(stdout)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                decision = str(parsed.get("decision", "")).lower()
                if decision in ("block", "deny") or parsed.get("block") is True:
                    outcome["blocked"] = True
                    outcome["reason"] = str(parsed.get("reason") or "blocked by hook")

        if proc.returncode == 2:
            outcome["blocked"] = True
            outcome["reason"] = stderr.strip() or outcome["reason"] or "blocked by hook"
        return outcome
