import os
os.environ.setdefault("BONSAI_LEGACY_PATHS", "1")

import sys
sys.path.append(".")
import unittest
from execution_loop import execution_step

class MockModelClient:
    def chat_completion(self, model, messages):
        # Default behavior: simulate successful planning
        return {"content": "Planning action"}

class ConnectionErrorModelClient(MockModelClient):
    def chat_completion(self, model, messages):
        raise ConnectionError("llama-server unavailable")

class TestExecutionResilience(unittest.TestCase):
    def test_llama_server_crash_recovery(self):
        print("INFO: Testing execution_loop resilience to llama-server crash.")
        client = ConnectionErrorModelClient()
        task_state = {"goal": "test", "steps": 0, "done": False}
        
        # Now execution_step catches the error and updates task_state
        result_state = execution_step("test", client, task_state)
        
        self.assertEqual(result_state.get("status"), "error")
        self.assertIn("LLM unavailable", result_state.get("message", ""))

    def test_tool_hallucination_handling(self):
        print("INFO: Testing tool hallucination handling.")
        # Setup: Tool call for non-existent tool
        from tools import get_toolbox, parse_tool_calls, execute_tool_calls
        toolbox = get_toolbox()
        
        # Hallucinated tool call
        hallucination = '<tool_call>{"tool": "destroy_system", "args": {"force": true}}</tool_call>'
        calls = parse_tool_calls(hallucination)
        
        results = execute_tool_calls(calls, toolbox)
        
        # Verify it was rejected
        self.assertFalse(results[0]["ok"])
        self.assertEqual(results[0]["error"], "herramienta_no_encontrada")

if __name__ == '__main__':
    unittest.main()
