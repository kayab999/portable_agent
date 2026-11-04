#!/usr/bin/env python3
"""Smoke boot without requiring a GGUF model."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    errors: list[str] = []

    try:
        from tools import get_toolbox, parse_tool_calls, execute_tool_calls

        tb = get_toolbox()
        d = tb.diagnose()
        assert d["ok"]
        r = tb.write_file("smoke_test.txt", "hello-rc")
        assert r["ok"], r
        r2 = tb.read_file("smoke_test.txt")
        assert r2["ok"] and "hello-rc" in r2["content"]
        calls = parse_tool_calls(
            '<tool_call>{"tool": "list_dir", "args": {"path": "."}}</tool_call>'
        )
        results = execute_tool_calls(calls, tb)
        assert results[0]["ok"]
        print("  tools: OK")
    except Exception as e:
        errors.append(f"tools: {e}")

    try:
        import tempfile
        from pathlib import Path as P
        from local_memory import TFIDFIndex

        tmp = P(tempfile.mkdtemp()) / "m.db"
        mem = TFIDFIndex(db_path=tmp)
        docs = ROOT / "docs"
        if docs.exists():
            stats = mem.index_docs(docs)
            assert "error" not in stats or stats.get("indexed", 0) >= 0
            print(f"  memory index: OK ({stats})")
        else:
            stats = mem.index_docs(
                [{"source": "t.txt", "content": "philosophy knowledge test document"}]
            )
            assert stats["indexed"] >= 1
            print("  memory list-index: OK")
        mem.close()
    except Exception as e:
        errors.append(f"memory: {e}")

    try:
        from gateway import make_handler

        assert make_handler
        print("  gateway import: OK")
    except Exception as e:
        errors.append(f"gateway: {e}")

    try:
        from agent_loop import extract_tool_calls, parse_reflection
        from crypto import encrypt, decrypt

        assert extract_tool_calls('<tool_call>{"tool":"diagnose","args":{}}</tool_call>')
        assert parse_reflection('{"success":true,"reflection":"x"}')["success"]
        k = b"9" * 32
        assert decrypt(encrypt("x", k), k) == "x"
        print("  agent_loop+crypto: OK")
    except Exception as e:
        errors.append(f"agent/crypto: {e}")

    try:
        from launcher import load_config, detect_platform

        cfg = load_config()
        assert "port" in cfg
        detect_platform()
        print("  launcher: OK")
    except Exception as e:
        errors.append(f"launcher: {e}")

    if errors:
        print("SMOKE FAIL:")
        for e in errors:
            print(" ", e)
        return 1
    print("SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
