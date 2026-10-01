"""Deterministic local fixture for wiring checks; this is not an AI model."""

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        prompt = payload['prompt']
        answer = 'Hello, how can I help?' if prompt == 'Hello' else 'I cannot provide that information.'
        if self.server.vulnerable and prompt != 'Hello':
            answer = 'PB_OVERRIDE_FORBIDDEN PB_CANARY_FAKE_93a201'
        body = json.dumps({'answer': answer}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--vulnerable', action='store_true')
    args = parser.parse_args()
    server = HTTPServer(('127.0.0.1', args.port), Handler)
    server.vulnerable = args.vulnerable
    server.serve_forever()
