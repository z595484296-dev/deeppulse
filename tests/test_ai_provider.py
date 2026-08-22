import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import server
from ai_provider import (candidate, config_fingerprint, provider_fingerprint,
                         public_status, test_preview, validate_confirmation)


BJC = timezone(timedelta(hours=8))
NOW = datetime(2026, 8, 24, 11, 0, tzinfo=BJC)


class AiProviderRulesTests(unittest.TestCase):
    def test_key_presence_is_not_readiness_and_rotation_invalidates_verification(self):
        base = 'https://api.deepseek.com'
        config = {'deepseek_api_key': 'first-secret', 'deepseek_model': 'deepseek-chat',
                  'deepseek_base_url': base}
        self.assertEqual(public_status(config, NOW)['state'], 'saved_unverified')
        config['deepseek_provider_verified_fingerprint'] = provider_fingerprint(
            base, 'deepseek-chat', 'first-secret')
        self.assertTrue(public_status(config, NOW)['ready'])
        config['deepseek_api_key'] = 'second-secret'
        rotated = public_status(config, NOW)
        self.assertFalse(rotated['ready'])
        self.assertEqual(rotated['state'], 'saved_unverified')

    def test_external_http_and_secret_echo_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'HTTPS'):
            candidate({'baseUrl': 'http://example.com', 'model': 'deepseek-chat',
                       'apiKey': 'valid-secret'})
        value = candidate({'baseUrl': 'http://127.0.0.1:9999', 'model': 'deepseek-chat',
                           'apiKey': 'valid-secret'})
        self.assertEqual(value['host'], '127.0.0.1:9999')
        status = public_status({'deepseek_api_key': 'valid-secret',
                                'deepseek_base_url': 'http://127.0.0.1:9999'}, NOW)
        self.assertNotIn('valid-secret', json.dumps(status))

    def test_confirmation_requires_all_boundaries_and_fresh_revision(self):
        value = candidate({'baseUrl': 'https://api.deepseek.com', 'model': 'deepseek-chat',
                           'apiKey': 'valid-secret'})
        preview = test_preview(value, {'passed': True, 'latencyMs': 20}, 'unconfigured', NOW)
        with self.assertRaisesRegex(ValueError, '本机保存'):
            validate_confirmation(preview, ['confirm:ai-provider'], 'unconfigured', NOW)
        confirmed = [row['id'] for row in preview['confirmations']] + ['confirm:ai-provider']
        self.assertTrue(validate_confirmation(preview, confirmed, 'unconfigured', NOW))
        with self.assertRaisesRegex(ValueError, '已变化'):
            validate_confirmation(preview, confirmed, 'different', NOW)


class SyntheticProviderHandler(BaseHTTPRequestHandler):
    requests = 0
    authorization = ''
    payload_text = ''

    def log_message(self, *_args):
        pass

    def do_POST(self):
        type(self).requests += 1
        type(self).authorization = self.headers.get('Authorization') or ''
        length = int(self.headers.get('Content-Length') or 0)
        payload = json.loads(self.rfile.read(length).decode('utf-8'))
        type(self).payload_text = json.dumps(payload, ensure_ascii=False)
        content = json.dumps({'summary': 'format ok', 'facts': [], 'inferences': [],
                              'gaps': [], 'falsifierChecks': [],
                              'citations': ['SYNTHETIC-001']})
        body = json.dumps({'choices': [{'message': {'content': content}}],
                           'usage': {'prompt_tokens': 30, 'completion_tokens': 20,
                                     'total_tokens': 50}}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class AiProviderServerTests(unittest.TestCase):
    def setUp(self):
        SyntheticProviderHandler.requests = 0
        SyntheticProviderHandler.authorization = ''
        SyntheticProviderHandler.payload_text = ''
        with server._ai_provider_test_lock:
            server._ai_provider_tests.clear()

    def test_real_http_synthetic_test_then_confirm_keeps_chat_separate(self):
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), SyntheticProviderHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                config_file = os.path.join(folder, 'config.json')
                secret = 'secret-never-returned'
                base = 'http://127.0.0.1:%d' % httpd.server_port
                with open(config_file, 'w', encoding='utf-8') as handle:
                    json.dump({'deepseek_api_key': '', 'deepseek_model': 'deepseek-chat',
                               'deepseek_base_url': 'https://api.deepseek.com'}, handle)
                with patch.object(server, 'CONFIG_FILE', config_file), \
                     patch.object(server, 'now_bj', return_value=NOW):
                    result = server.test_ai_provider({'baseUrl': base, 'model': 'deepseek-chat',
                                                      'apiKey': secret})
                    serialized = json.dumps(result, ensure_ascii=False)
                    self.assertTrue(result['passed'])
                    self.assertNotIn(secret, serialized)
                    confirmations = [row['id'] for row in result['confirmations']] + ['confirm:ai-provider']
                    saved = server.confirm_ai_provider(result['testId'], result['expectedRevision'], confirmations)
                    brain = {'mode': 'llm' if server.load_config().get('deepseek_chat_enabled') else 'local'}
                    with open(config_file, 'r', encoding='utf-8') as handle:
                        disk = handle.read()
                self.assertEqual(SyntheticProviderHandler.requests, 1)
                self.assertEqual(SyntheticProviderHandler.authorization, 'Bearer ' + secret)
                self.assertNotIn('601138', SyntheticProviderHandler.payload_text)
                self.assertNotIn('股票', SyntheticProviderHandler.payload_text)
                self.assertTrue(saved['ready'])
                self.assertEqual(brain['mode'], 'local')
                self.assertIn(secret, disk)
                self.assertNotIn(secret, json.dumps(saved, ensure_ascii=False))
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_disconnect_wipes_key_and_synthetic_test_does_not_touch_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            config_file = os.path.join(folder, 'config.json')
            profile_file = os.path.join(folder, 'profile.json')
            key = 'configured-secret'
            base = 'https://api.deepseek.com'
            config = {'deepseek_api_key': key, 'deepseek_base_url': base,
                      'deepseek_model': 'deepseek-chat',
                      'deepseek_provider_verified_fingerprint': provider_fingerprint(base, 'deepseek-chat', key)}
            with open(config_file, 'w', encoding='utf-8') as handle:
                json.dump(config, handle)
            with patch.object(server, 'CONFIG_FILE', config_file), \
                 patch.object(server, 'PROFILE_FILE', profile_file):
                revision = config_fingerprint(config)[:20]
                result = server.disconnect_ai_provider(revision, True)
                self.assertFalse(result['configured'])
                self.assertFalse(os.path.exists(profile_file))
                self.assertEqual(server.load_config().get('deepseek_api_key'), '')


if __name__ == '__main__':
    unittest.main()

