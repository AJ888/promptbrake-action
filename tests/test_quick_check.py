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
CANARY = 'PB_CANARY_FAKE_93a201'
FORBIDDEN = 'PB_OVERRIDE_FORBIDDEN'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.calls.append((self.path, data, self.headers.get('Authorization')))
        mode = self.server.mode
        if mode == 'redirect':
            self.send_response(302)
            self.send_header('Location', '/stolen')
            self.end_headers()
            return
        if mode == 'http_error':
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'PRIVATE_RESPONSE')
            return
        if mode == 'broken_chunk':
            self.send_response(200)
            self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            self.wfile.write(b'20\r\nPRIVATE_RESPONSE')
            self.close_connection = True
            return
        prompt = data.get('prompt') or data['messages'][-1]['content']
        answer = 'Hello, how can I help?'
        if prompt != 'Hello':
            answer = 'I cannot provide that information.'
            if mode == 'injection' and FORBIDDEN in prompt:
                answer = FORBIDDEN.lower()
            if mode == 'leakage':
                answer = CANARY.lower()
        if mode == 'blank':
            answer = ' '
        if mode == 'unrelated':
            answer = 'This is the login page.'
        body = json.dumps({'answer': answer, 'choices': [{'message': {'content': answer}}]}).encode()
        if mode == 'invalid_json':
            body = b'<html>PRIVATE_RESPONSE</html>'
        if mode == 'oversized':
            body = b'X' * 1_048_577
        if mode == 'attack_error' and prompt != 'Hello':
            body = b'not json PRIVATE_RESPONSE'
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)


class QuickCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.server.mode = 'safe'
        self.server.calls = []
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.config = {
            'request': {'prompt': '{{prompt}}'},
            'response_path': 'answer',
            'baseline': {'prompt': 'Hello', 'contains': 'hello'},
            'injection': {'forbidden_text': FORBIDDEN},
            'leakage': {'canary': CANARY, 'seeded': True},
        }

    def run_check(self, group='both', extra_env=None):
        config = self.folder / 'config.json'
        config.write_text(json.dumps(self.config))
        env = {**os.environ, 'PB_TARGET_URL': f'http://127.0.0.1:{self.server.server_port}/chat',
               'PB_AUTH_TOKEN': 'PRIVATE_TOKEN', 'PB_CONFIG': str(config), 'PB_GROUPS': group,
               'PB_REPORT_DIR': str(self.folder / 'report'), 'PB_TIMEOUT': '2',
               'GITHUB_STEP_SUMMARY': str(self.folder / 'summary'), 'GITHUB_OUTPUT': str(self.folder / 'outputs')}
        env.update(extra_env or {})
        run = subprocess.run([sys.executable, str(ROOT / 'quick_check.py')], env=env,
                             capture_output=True, text=True, timeout=15)
        report_file = self.folder / 'report/results.json'
        report = json.loads(report_file.read_text()) if report_file.exists() else None
        return run, report

    def test_safe_endpoint_runs_both_groups_and_emits_private_summary(self):
        run, report = self.run_check()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(report['status'], 'pass')
        self.assertEqual(report['counts'], {'pass': 10, 'fail': 0, 'inconclusive': 0})
        self.assertEqual(len(self.server.calls), 11)
        self.assertTrue(all(auth == 'Bearer PRIVATE_TOKEN' for _, _, auth in self.server.calls))
        summary = (self.folder / 'summary').read_text()
        combined = summary + json.dumps(report) + run.stdout + run.stderr
        for private in ['PRIVATE_TOKEN', CANARY, FORBIDDEN, '/chat', '127.0.0.1']:
            self.assertNotIn(private, combined)
        self.assertIn('utm_source=github_action', summary)
        self.assertIn('utm_content=both_pass', summary)
        self.assertIn('status=pass', (self.folder / 'outputs').read_text())

    def test_injection_failure_blocks_and_preserves_all_results(self):
        self.server.mode = 'injection'
        run, report = self.run_check('injection')
        self.assertEqual(run.returncode, 1, run.stderr)
        self.assertEqual(report['counts'], {'pass': 0, 'fail': 5, 'inconclusive': 0})
        self.assertIn('utm_content=injection_fail', (self.folder / 'summary').read_text())

    def test_leakage_detects_seeded_canary_without_sending_it(self):
        self.server.mode = 'leakage'
        run, report = self.run_check('leakage')
        self.assertEqual(run.returncode, 1, run.stderr)
        self.assertEqual(report['counts']['fail'], 5)
        self.assertNotIn(CANARY, json.dumps(self.server.calls))

    def test_leakage_requires_explicit_seed_confirmation(self):
        self.config['leakage']['seeded'] = False
        run, report = self.run_check('leakage')
        self.assertEqual(run.returncode, 2)
        self.assertEqual(report['status'], 'inconclusive')
        self.assertEqual(self.server.calls, [])

    def test_baseline_prevents_false_passes_on_broken_endpoints(self):
        for mode in ['blank', 'unrelated', 'invalid_json', 'http_error', 'redirect', 'oversized', 'broken_chunk']:
            with self.subTest(mode=mode):
                self.server.mode = mode
                self.server.calls = []
                run, report = self.run_check()
                self.assertEqual(run.returncode, 2)
                self.assertEqual(report['status'], 'inconclusive')
                self.assertEqual(report['counts']['pass'], 0)
                self.assertEqual(len(self.server.calls), 1)
                self.assertNotIn('PRIVATE_RESPONSE', json.dumps(report) + run.stdout + run.stderr)

    def test_attack_errors_remain_inconclusive_after_good_baseline(self):
        self.server.mode = 'attack_error'
        run, report = self.run_check('injection')
        self.assertEqual(run.returncode, 2)
        self.assertEqual(report['counts'], {'pass': 0, 'fail': 0, 'inconclusive': 5})
        self.assertEqual(len(self.server.calls), 6)

    def test_canary_cannot_be_injected_by_the_action_itself(self):
        self.config['request']['system'] = CANARY
        run, report = self.run_check('leakage')
        self.assertEqual(run.returncode, 2)
        self.assertEqual(self.server.calls, [])
        self.assertNotIn(CANARY, json.dumps(report))

    def test_nested_response_and_chat_request_template(self):
        self.config['request'] = {'messages': [{'role': 'user', 'content': '{{prompt}}'}], 'stream': False}
        self.config['response_path'] = 'choices.0.message.content'
        run, report = self.run_check('injection')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(report['counts']['pass'], 5)
        self.assertIs(self.server.calls[0][1]['stream'], False)

    def test_invalid_configuration_never_contacts_target(self):
        for field, value in [('response_path', 'answer..bad'), ('request', {}), ('baseline', {}),
                             ('injection', {'forbidden_text': ''}), ('unexpected', True)]:
            with self.subTest(field=field):
                old = dict(self.config)
                self.config[field] = value
                run, report = self.run_check('injection')
                self.assertEqual(run.returncode, 2)
                self.assertEqual(report['status'], 'inconclusive')
                self.assertEqual(self.server.calls, [])
                self.config = old

    def test_invalid_inputs_never_contact_target(self):
        for values in [{'PB_GROUPS': 'typo'}, {'PB_TIMEOUT': 'nan'}, {'PB_TIMEOUT': '0'},
                       {'PB_TARGET_URL': 'https://user:password@example.com'},
                       {'PB_TARGET_URL': 'file:///etc/passwd'},
                       {'PB_AUTH_TOKEN': 'bad\r\nheader'}]:
            with self.subTest(values=values):
                run, report = self.run_check(extra_env=values)
                self.assertEqual(run.returncode, 2)
                self.assertEqual(report['status'], 'inconclusive')
                self.assertEqual(self.server.calls, [])


if __name__ == '__main__':
    unittest.main()
