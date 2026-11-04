#!/usr/bin/env python3
"""Gateway RAG and experience endpoints (debug mode, loopback)."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from http.client import HTTPConnection
from pathlib import Path

from gateway import GatewayState, start_gateway
from local_memory import TFIDFIndex


class _GatewayCase(unittest.TestCase):
    rag_enabled = True
    persistence_enabled = True

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "memory.db"
        TFIDFIndex(db_path=self.db).close()
        self.state = GatewayState(
            llm_base="http://127.0.0.1:9",
            debug_mode=True,
            is_unlocked=lambda: True,
            rag_enabled=self.rag_enabled,
            persistence_enabled=self.persistence_enabled,
            db_path=self.db,
            host="127.0.0.1",
            port=0,
            auth_required=False,
            mask_outputs=False,
        )
        self.server = start_gateway(self.state, background=True)
        self.port = self.server.server_address[1]

    def tearDown(self):
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _req(self, method, path, body=None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(data))
        conn.request(method, path, body=data, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        try:
            parsed = json.loads(raw.decode()) if raw else {}
        except json.JSONDecodeError:
            parsed = {"_raw": raw.decode("utf-8", errors="replace")}
        return response.status, parsed

    def _seed(self):
        memory = TFIDFIndex(db_path=self.db)
        memory.index_docs(
            [
                {
                    "source": "manual.txt",
                    "content": "alpha philosophy ontology epistemology " * 20,
                },
                {
                    "source": "notes.txt",
                    "content": "beta gardening tomatoes soil " * 20,
                },
            ]
        )
        return memory


class TestGatewayRag(_GatewayCase):
    def test_status_endpoint(self):
        status, data = self._req("GET", "/api/rag/status")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["schema_version"], 2)

    def test_query_returns_citations_and_context(self):
        self._seed()
        status, data = self._req(
            "POST", "/api/rag/query", {"query": "alpha philosophy", "top_k": 2}
        )
        self.assertEqual(status, 200)
        self.assertTrue(data["results"])
        self.assertTrue(data["citations"])
        self.assertIn("[source:", data["context"])
        self.assertEqual(data["citations"][0]["path"], "manual.txt")

    def test_query_get(self):
        self._seed()
        status, data = self._req("GET", "/api/rag/query?q=alpha")
        self.assertEqual(status, 200)
        self.assertTrue(data["results"])

    def test_empty_query_is_rejected(self):
        status, data = self._req("POST", "/api/rag/query", {"query": "  "})
        self.assertEqual(status, 400)

    def test_index_path_is_confined(self):
        status, data = self._req("POST", "/api/rag/index", {"path": "/etc"})
        self.assertEqual(status, 403)
        self.assertEqual(data["error"], "path_forbidden")

    def test_sources_and_delete(self):
        self._seed()
        status, data = self._req("GET", "/api/rag/sources")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["sources"]), 2)
        document_id = data["sources"][0]["id"]
        status, data = self._req(
            "POST", "/api/rag/sources/delete", {"id": document_id}
        )
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])

    def test_legacy_action_contract(self):
        self._seed()
        status, data = self._req(
            "POST", "/api/rag", {"action": "query", "query": "beta", "top_k": 1}
        )
        self.assertEqual(status, 200)
        self.assertTrue(data["results"])
        status, data = self._req("POST", "/api/rag", {"action": "list"})
        self.assertEqual(status, 200)
        self.assertEqual(data["indexed"], 2)

    def test_query_filters_by_source_type_and_path_prefix(self):
        self._seed()
        status, data = self._req(
            "POST",
            "/api/rag/query",
            {"query": "alpha beta", "source_type": "document"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(data["results"])
        self.assertTrue(all(r["source_type"] == "document" for r in data["results"]))

        status, data = self._req(
            "POST",
            "/api/rag/query",
            {"query": "alpha beta", "source_type": "experience"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["results"], [])

        status, data = self._req(
            "GET", "/api/rag/query?q=alpha+beta&path_prefix=manual"
        )
        self.assertEqual(status, 200)
        self.assertTrue(data["results"])
        self.assertTrue(all(r["path"] == "manual.txt" for r in data["results"]))

        status, data = self._req(
            "POST",
            "/api/rag/query",
            {"query": "alpha", "path_prefix": "does-not-exist"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["results"], [])

    def test_purge_requires_a_real_boolean(self):
        self._seed()
        _, sources = self._req("GET", "/api/rag/sources")
        document_id = sources["sources"][0]["id"]
        status, data = self._req(
            "POST", "/api/rag/sources/delete", {"id": document_id, "purge": "false"}
        )
        self.assertEqual(status, 200)
        self.assertFalse(data["purged"])
        status, data = self._req(
            "POST", "/api/rag", {"action": "list", "include_deleted": True}
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(data["sources"]), 2)

    def test_maintenance_dry_run_string_stays_dry(self):
        self._seed()
        status, data = self._req(
            "POST", "/api/rag/maintenance", {"dry_run": "false"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(data["maintenance"]["dry_run"])

    def test_maintenance_dry_run_does_not_mutate(self):
        self._seed()
        status, data = self._req(
            "POST", "/api/rag/maintenance", {"dry_run": True}
        )
        self.assertEqual(status, 200)
        self.assertTrue(data["maintenance"]["dry_run"])
        self.assertEqual(data["maintenance"]["hard_deleted"], 0)


class TestGatewayRagDisabled(_GatewayCase):
    rag_enabled = False

    def test_status_reports_disabled(self):
        status, data = self._req("GET", "/api/rag/status")
        self.assertEqual(status, 503)
        self.assertEqual(data["error"], "feature_unavailable")


class TestGatewayExperience(_GatewayCase):
    def test_status_and_drain(self):
        from agent_loop import enqueue_experience

        enqueue_experience(
            {"query": "hello", "final": "world", "success": True},
            db_path=self.db,
        )
        status, data = self._req("GET", "/api/experience/status")
        self.assertEqual(status, 200)
        self.assertEqual(data["pending"], 1)
        status, data = self._req("POST", "/api/experience/drain", {})
        self.assertEqual(status, 200)
        self.assertEqual(data["processed"], 1)
        self.assertEqual(data["status"]["done"], 1)
        # Drained experience is searchable through RAG as an experience chunk.
        status, data = self._req("POST", "/api/rag/query", {"query": "hello world"})
        self.assertEqual(status, 200)
        self.assertTrue(any(r["source_type"] == "experience" for r in data["results"]))

    def test_purge(self):
        status, data = self._req("POST", "/api/experience/purge", {})
        self.assertEqual(status, 200)
        self.assertEqual(data["purged"], 0)

    def test_drain_honors_max_events(self):
        from agent_loop import enqueue_experience

        for i in range(3):
            enqueue_experience(
                {"query": f"q{i}", "final": "a", "success": True},
                db_path=self.db,
            )
        status, data = self._req("POST", "/api/experience/drain", {"max_events": 1})
        self.assertEqual(status, 200)
        self.assertEqual(data["processed"], 1)
        self.assertEqual(data["max_events"], 1)
        self.assertEqual(data["status"]["pending"], 2)

    def test_drain_rejects_invalid_max_events(self):
        status, data = self._req(
            "POST", "/api/experience/drain", {"max_events": "many"}
        )
        self.assertEqual(status, 400)
        self.assertIn("max_events", data["error"])


class TestGatewayExperienceDisabled(_GatewayCase):
    persistence_enabled = False

    def test_drain_reports_disabled(self):
        status, data = self._req("POST", "/api/experience/drain", {})
        self.assertEqual(status, 503)
        self.assertEqual(data["feature"], "persistence")


if __name__ == "__main__":
    unittest.main()


class TestGatewayApi(_GatewayCase):
    def test_read_only_endpoints(self):
        for path in (
            "/api/health",
            "/api/config",
            "/api/session/status",
            "/api/identity",
            "/api/personas",
        ):
            status, data = self._req("GET", path)
            self.assertEqual(status, 200, (path, data))
        status, data = self._req("GET", "/api/subagents")
        self.assertIn(status, (200, 503))

    def test_health_reports_features(self):
        status, data = self._req("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(data["rag_enabled"])
        self.assertTrue(data["persistence_enabled"])
        self.assertEqual(data["features"]["rag"], "enabled")

    def test_unknown_rag_action(self):
        status, data = self._req("POST", "/api/rag", {"action": "bogus"})
        self.assertEqual(status, 400)

    def test_delete_missing_source(self):
        status, data = self._req(
            "POST", "/api/rag/sources/delete", {"id": 999999}
        )
        self.assertEqual(status, 404)
        status, data = self._req("POST", "/api/rag/sources/delete", {})
        self.assertEqual(status, 400)

    def test_index_accepts_empty_body_and_reports_status(self):
        status, data = self._req("POST", "/api/rag/index", {})
        self.assertEqual(status, 200)
        self.assertIn("status", data)
        self.assertIn("documents", data["status"])

    def test_tools_debug_requires_tool_call(self):
        status, data = self._req("POST", "/api/tools", {"text": "no tools here"})
        self.assertEqual(status, 400)

    def test_subagent_disabled(self):
        status, data = self._req(
            "POST", "/api/subagent", {"name": "x", "message": "y"}
        )
        self.assertEqual(status, 503)
