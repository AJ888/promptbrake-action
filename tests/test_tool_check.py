import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.server.calls += 1
        self.rfile.read(int(self.headers["Content-Length"]))
        trace = {
            "version": 1,
            "request_id": self.headers.get("X-PromptBrake-Test-ID"),
            "source": "application_dispatcher",
            "complete": True,
            "covered_tools": ["send_email"],
            "calls": [],
        }
        mode = self.server.mode
        if mode == "fail":
            trace["calls"] = [{"sequence": 1, "tool": "send_email", "arguments": {"recipient": "PRIVATE_ARGUMENT"}}]
        if mode == "incomplete":
            trace["complete"] = False
        if mode == "uncorrelated":
            trace["request_id"] = "wrong"
        self.send_response(429 if mode == "rate_limit" else 200)
        self.end_headers()
        self.wfile.write(json.dumps({"answer": "PRIVATE_REPLY", "promptbrake_trace": trace}).encode())


class ToolCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_tools_mode_correlates_and_blocks_without_private_artifacts(self):
        for mode, code, status in [
            ("safe", 0, "pass"),
            ("fail", 1, "fail"),
            ("incomplete", 2, "inconclusive"),
            ("uncorrelated", 2, "inconclusive"),
            ("rate_limit", 2, "inconclusive"),
        ]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                (folder / "config.json").write_text(json.dumps({"request": {"prompt": "{{prompt}}"}}))
                (folder / "tool-tests.json").write_text(
                    json.dumps(
                        {
                            "kind": "agent_tool_calls",
                            "version": 1,
                            "tests": [
                                {
                                    "id": "blocked",
                                    "name": "Blocked",
                                    "prompt": "PRIVATE_PROMPT",
                                    "rule": {"type": "must_not_call", "tool": "send_email"},
                                }
                            ],
                        }
                    )
                )
                self.server.mode = mode
                self.server.calls = 0
                env = {
                    **os.environ,
                    "PYTHONPATH": str(REPO),
                    "PB_GROUPS": "tools",
                    "PB_TARGET_URL": f"http://127.0.0.1:{self.server.server_port}/chat",
                    "PB_CONFIG": str(folder / "config.json"),
                    "PB_TOOL_TESTS": str(folder / "tool-tests.json"),
                    "PB_REPORT_DIR": str(folder / "report"),
                }
                run = subprocess.run(
                    [sys.executable, str(ROOT / "quick_check.py")], env=env, capture_output=True, text=True
                )
                self.assertEqual(run.returncode, code, run.stderr)
                report = json.loads((folder / "report/results.json").read_text())
                self.assertEqual(report["status"], status)
                self.assertEqual(self.server.calls, 1)
                self.assertEqual(report["results"][0]["evidence_scope"], "tool_invocation")
                artifacts = json.dumps(report) + (folder / "report/summary.md").read_text() + run.stdout + run.stderr
                for secret in ("PRIVATE_ARGUMENT", "PRIVATE_REPLY", "PRIVATE_PROMPT"):
                    self.assertNotIn(secret, artifacts)
