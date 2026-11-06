import shutil
import tempfile
import unittest
from pathlib import Path

from agent_loop import (
    agent_step,
    evaluate_task_outcome,
    extract_tool_calls,
    filter_calls_by_persona,
    legacy_tool_line_to_calls,
    parse_reflection,
)
from audit_worker import audit_worker
from local_memory import TFIDFIndex


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def chat_completion(self, model, messages):
        self.calls += 1
        if not self.responses:
            return {"content": "done"}
        return {"content": self.responses.pop(0)}


class TestAgentLoop(unittest.TestCase):
    def test_parse_reflection_json(self):
        r = parse_reflection('{"success": true, "reflection": "ok"}')
        self.assertTrue(r["success"])
        self.assertEqual(r["reflection"], "ok")

    def test_parse_reflection_keywords(self):
        r = parse_reflection("The task was a complete failure and error")
        self.assertFalse(r["success"])

    def test_objective_outcome_requires_tool_evidence_and_final(self):
        self.assertEqual(
            evaluate_task_outcome("done", [{"tool": "t", "ok": True}])[
                "task_outcome"
            ],
            "succeeded",
        )
        self.assertEqual(
            evaluate_task_outcome("done", [])["task_outcome"], "unverified"
        )
        self.assertEqual(
            evaluate_task_outcome(
                "done", [{"tool": "t", "ok": False}]
            )["task_outcome"],
            "unsatisfied",
        )
        self.assertEqual(
            evaluate_task_outcome("", [{"tool": "t", "ok": True}])[
                "task_outcome"
            ],
            "unsatisfied",
        )

    def test_tool_call_budget_stops_and_blocks_success(self):
        from unittest import mock

        class ToolClient:
            def chat_completion(self, model, messages):
                return {
                    "content": '<tool_call>{"tool": "t", "args": {}}</tool_call>'
                }

        state = {"goal": "loop", "steps": 0, "done": False}
        with mock.patch(
            "agent_loop.run_tool_cycle",
            return_value=([{"tool": "t", "ok": True}], "ok"),
        ) as cycle:
            out = agent_step(
                "go",
                ToolClient(),
                state,
                system_prompt="sys",
                max_tool_rounds=10,
                config={"max_tool_calls": 2},
            )
        self.assertEqual(cycle.call_count, 2)
        self.assertEqual(out["budget_exhausted"], "max_tool_calls")
        self.assertEqual(out["task_outcome"], "unsatisfied")
        self.assertFalse(out.get("done"))

    def test_objective_success_requires_explicit_verifier(self):
        result = evaluate_task_outcome("done", [{"tool": "t", "ok": True}])
        self.assertTrue(result["tool_execution_success"])
        self.assertFalse(result["objective_success"])
        self.assertFalse(result["objective_verified"])
        self.assertEqual(result["verification_level"], "tool_execution")
        self.assertEqual(result["evidence"][-1]["kind"], "verification")
        self.assertEqual(result["evidence"][-1]["level"], "tool_execution")

        verified = evaluate_task_outcome(
            "done", [{"tool": "t", "ok": True}], objective_verified=True
        )
        self.assertTrue(verified["objective_success"])
        self.assertEqual(verified["verification_level"], "objective")

    def test_reflection_cannot_mark_failed_tool_as_success(self):
        tmp = Path(tempfile.mkdtemp())
        db = tmp / "runs.db"
        try:
            client = FakeClient(
                [
                    '<tool_call>{"tool": "does_not_exist", "args": {}}</tool_call>',
                    "FINAL ANSWER: done",
                    '{"success": true, "reflection": "looks good"}',
                ]
            )
            state = {"goal": "test failure evidence", "steps": 0, "done": False}
            out = agent_step(
                "hello",
                client,
                state,
                system_prompt="sys",
                model="test",
                persist_run=True,
                db_path=db,
            )
            self.assertEqual(out["task_outcome"], "unsatisfied")
            self.assertFalse(out.get("done"))
            self.assertTrue(out["model_reflection_success"])
            import agent_runs

            runs = agent_runs.list_runs(db_path=db)
            run = agent_runs.get_run(runs[0]["run_id"], db_path=db)
            self.assertEqual(run["task_outcome"], "unsatisfied")
            self.assertTrue(run["evidence"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_legacy_tool_line(self):
        calls = legacy_tool_line_to_calls("TOOL: list_dir .")
        self.assertEqual(calls[0]["tool"], "list_dir")

    def test_extract_canonical(self):
        text = '<tool_call>{"tool": "diagnose", "args": {}}</tool_call>'
        calls = extract_tool_calls(text)
        self.assertEqual(calls[0]["tool"], "diagnose")

    def test_agent_step_and_persist(self):
        tmp = Path(tempfile.mkdtemp())
        db = tmp / "m.db"
        try:
            # init schema via TFIDFIndex
            mem = TFIDFIndex(db_path=db)
            mem.close()

            client = FakeClient(
                [
                    # plan without tools
                    "FINAL ANSWER: all good",
                    # reflection
                    '{"success": true, "reflection": "worked"}',
                ]
            )
            state = {"goal": "test", "steps": 0, "done": False}
            out = agent_step(
                "hello",
                client,
                state,
                system_prompt="You are a test agent.",
                model="test",
                persist=True,
                drain_queue=True,
                db_path=db,
            )
            self.assertEqual(out.get("status"), "ok")
            self.assertTrue(out.get("success"))
            # queue should be drained
            n = audit_worker(db_path=db)
            self.assertEqual(n, 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_llm_unavailable(self):
        class Boom:
            def chat_completion(self, model, messages):
                raise ConnectionError("down")

        state = {"goal": "x", "steps": 0, "done": False}
        out = agent_step(
            "hi",
            Boom(),
            state,
            system_prompt="sys",
            persist=False,
            drain_queue=False,
        )
        self.assertEqual(out["status"], "error")

    def test_filter_calls_none_fail_closed_no_power(self):
        marked = filter_calls_by_persona(
            [
                {"tool": "python_exec", "args": {"code": "print(1)"}},
                {"tool": "read_file", "args": {"path": "x"}},
            ],
            None,
        )
        by_name = {c["tool"]: c for c in marked}
        self.assertTrue(by_name["python_exec"].get("_denied"))
        self.assertFalse(by_name["read_file"].get("_denied"))


if __name__ == "__main__":
    unittest.main()
