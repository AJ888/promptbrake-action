#!/usr/bin/env python3
"""Synthetic staging fixture. No model and no external side effects."""

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from promptbrake_tools.capture import ToolDispatcher, capture
from promptbrake_tools.contracts import identifier


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        token = self.server.token
        if token and self.headers.get("Authorization") != f"Bearer {token}":
            self.send_error(401)
            return
        request_id = self.headers.get("X-PromptBrake-Test-ID", "")
        if request_id and not identifier(request_id):
            self.send_error(400)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 65536:
                raise ValueError()
            data = json.loads(self.rfile.read(size))
            prompt = data["prompt"]
            if not isinstance(prompt, str):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            self.send_error(400)
            return
        dispatcher = ToolDispatcher({"send_email": lambda **args: None})
        if not request_id:
            body = json.dumps({"answer": "Hello. Synthetic fixture reply."}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)
            return
        with capture(request_id, dispatcher) as session:
            if self.server.model:
                from model_agent import run_agent

                try:
                    run_agent(prompt, dispatcher.call, self.server.model)
                except Exception:
                    self.send_error(502, "Model-backed staging request failed.")
                    return
            elif self.server.mode == "vulnerable" or prompt == "Send the approved message":
                dispatcher.call("send_email", {"recipient": "synthetic@example.com"})
        trace = session.trace()
        if self.server.mode == "incomplete":
            trace["complete"] = False
        body = json.dumps({"answer": "Synthetic fixture reply.", "promptbrake_trace": trace}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--mode", choices=["safe", "vulnerable", "incomplete"], default="safe")
    parser.add_argument("--model", help="Optional OpenAI model; needs OPENAI_API_KEY. Synthetic tools only.")
    args = parser.parse_args()
    token = os.getenv("EXAMPLE_AGENT_TOKEN", "")
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not token:
        parser.error("Set EXAMPLE_AGENT_TOKEN before binding beyond loopback.")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.mode = args.mode
    server.token = token
    server.model = args.model
    server.serve_forever()
