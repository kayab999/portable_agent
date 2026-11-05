"""Durable agent run storage tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import agent_runs
import agent_trace


class TestAgentRuns(unittest.TestCase):
    def test_create_and_finish_run(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "runs.db"
            run_id = agent_runs.create_run(goal="hello", model="m", db_path=db)
            self.assertTrue(run_id.startswith("run-"))
            step_id = agent_runs.record_step(run_id, 0, status="running", messages=[{"role": "user", "content": "hi"}], db_path=db)
            self.assertGreater(step_id, 0)
            agent_runs.finish_step(step_id, status="completed", model_output="done", db_path=db)
            agent_runs.finish_run(run_id, status="completed", final_answer="done", db_path=db)
            run = agent_runs.get_run(run_id, db_path=db)
            self.assertEqual(run["status"], "completed")
            self.assertEqual(run["final_answer"], "done")
            self.assertEqual(len(run["steps"]), 1)
            self.assertEqual(run["steps"][0]["status"], "completed")

    def test_list_runs_and_cleanup(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "runs.db"
            rid = agent_runs.create_run(goal="old", db_path=db)
            agent_runs.record_step(rid, 0, db_path=db)
            conn = agent_runs._connect(db)
            conn.execute("UPDATE agent_runs SET updated_at = 0 WHERE run_id = ?", (rid,))
            conn.execute("UPDATE agent_steps SET started_at = 0 WHERE run_id = ?", (rid,))
            conn.commit()
            conn.close()
            removed = agent_runs.cleanup_runs(retention_days=1, db_path=db)
            self.assertGreaterEqual(removed, 1)
            self.assertEqual(agent_runs.list_runs(db_path=db), [])

    def test_get_run_missing_and_list(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "runs.db"
            self.assertIsNone(agent_runs.get_run("nope", db_path=db))
            rid = agent_runs.create_run(goal="g", db_path=db)
            runs = agent_runs.list_runs(db_path=db)
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["run_id"], rid)
        with tempfile.TemporaryDirectory() as td:
            db = Path(td)
            old_trace_dir = agent_trace.TRACE_DIR
            agent_trace.TRACE_DIR = db / "traces"
            try:
                run_id = "run-test"
                agent_trace.emit_event(
                    run_id,
                    "tool_call",
                    payload={"token": "secret", "content": "x" * 3000},
                )
                events = agent_trace.read_events(run_id)
                self.assertEqual(events[0]["payload"]["token"], "[redacted]")
                self.assertTrue(events[0]["payload"]["content"].endswith("...[truncated]"))
            finally:
                agent_trace.TRACE_DIR = old_trace_dir


    def test_trace_read_empty_and_redact(self):
        with tempfile.TemporaryDirectory() as td:
            old = agent_trace.TRACE_DIR
            agent_trace.TRACE_DIR = Path(td) / "traces"
            try:
                self.assertEqual(agent_trace.read_events("missing"), [])
                self.assertEqual(agent_trace._redact({"a": "x" * 3000}), {"a": "x" * 2000 + "...[truncated]"})
            finally:
                agent_trace.TRACE_DIR = old
