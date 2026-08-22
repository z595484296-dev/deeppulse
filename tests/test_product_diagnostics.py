import io
import json
import os
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import server


class ProductDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.history_patch = patch.object(
            server, 'DIAGNOSTICS_HISTORY_FILE',
            os.path.join(self.temp.name, 'diagnostics-history.json'))
        self.history_patch.start()
        with server._desktop_heartbeat_lock:
            server._desktop_heartbeat.update(
                last_seen=None, app_version=None, product_version=None,
                service_ownership=None,
                process_lifetime_protected=None)

    def tearDown(self):
        self.history_patch.stop()
        self.temp.cleanup()

    def report(self, *, harness=True, device_enabled=False, device_running=False,
               device_last_seen=None, failed=0, market_items=None):
        delivery = {
            'channels': {
                'desktop': {'enabled': True, 'failed': failed},
                'epaper': {'enabled': False, 'failed': 0},
            }
        }
        device_config = {
            'enabled': device_enabled,
            'token': 'device-super-secret-token',
            'focus_code': '601138',
        }
        gateway = {
            'running': device_running,
            'last_seen': device_last_seen,
            'addresses': ['192.168.1.7'],
            'last_ip': '192.168.1.21',
        }
        with patch.object(server, 'port_is_listening', return_value=harness), \
                patch.object(server, 'tdx_status', return_value={
                    'installed': True, 'service_ready': True,
                    'install_path': r'C:\Users\secret\tdx',
                }), \
                patch.object(server, 'akshare_status', return_value={
                    'installed': True, 'status': 'ok', 'error': 'sk-private-value',
                }), \
                patch.object(server, 'source_catalog', return_value={'items': market_items or [
                    {'id': 'eastmoney', 'status': 'ok'},
                    {'id': 'tencent', 'status': 'unobserved'},
                ]}), \
                patch.object(server, 'attention_delivery_status', return_value=delivery), \
                patch.object(server, 'load_device_config', return_value=device_config), \
                patch.object(server, 'device_gateway_status', return_value=gateway):
            return server.build_product_diagnostics()

    def test_recent_primary_success_prevents_aggregate_circuit_false_alarm(self):
        report = self.report(market_items=[
            {'id': 'eastmoney', 'status': 'degraded', 'latest_ok': True},
            {'id': 'tencent', 'status': 'unobserved', 'latest_ok': None},
        ])
        market = next(row for row in report['components'] if row['id'] == 'market_sources')
        self.assertEqual(market['state'], 'ok')
        self.assertIn('最近访问正常', market['summary'])

    def test_report_uses_whitelist_and_excludes_private_values(self):
        report = self.report()
        encoded = json.dumps(report, ensure_ascii=False)
        for secret in ('device-super-secret-token', '192.168.1.7',
                       '192.168.1.21', r'C:\Users\secret\tdx',
                       'sk-private-value', '601138'):
            self.assertNotIn(secret, encoded)
        self.assertIn('API 密钥', report['privacy'])
        self.assertEqual(report['version'], '1.26.0')

    def test_desktop_heartbeat_changes_optional_desktop_state(self):
        before = self.report()
        desktop = next(row for row in before['components'] if row['id'] == 'desktop_app')
        self.assertEqual(desktop['state'], 'info')
        server.update_desktop_heartbeat({
            'appVersion': '1.26.0', 'productVersion': '1.26.0+test',
            'serviceOwnership': 'owned',
            'processLifetimeProtected': True,
            'ignoredSecret': 'must-not-appear',
        })
        after = self.report()
        desktop = next(row for row in after['components'] if row['id'] == 'desktop_app')
        self.assertEqual(desktop['state'], 'ok')
        self.assertIn('生命周期保护', desktop['summary'])
        self.assertEqual(desktop['trend'], 'recovered')
        self.assertNotIn('must-not-appear', json.dumps(after, ensure_ascii=False))

    def test_desktop_heartbeat_discloses_missing_lifetime_protection(self):
        server.update_desktop_heartbeat({
            'appVersion': '1.26.0', 'productVersion': '1.26.0+test',
            'serviceOwnership': 'owned',
            'processLifetimeProtected': False,
        })
        report = self.report()
        desktop = next(row for row in report['components'] if row['id'] == 'desktop_app')
        self.assertEqual(desktop['state'], 'warn')
        self.assertTrue(desktop['optional'])
        self.assertIn('未生效', desktop['summary'])

    def test_attached_desktop_does_not_claim_process_ownership(self):
        server.update_desktop_heartbeat({
            'appVersion': '1.26.0', 'productVersion': '1.26.0+test',
            'serviceOwnership': 'attached',
            'processLifetimeProtected': False,
        })
        report = self.report()
        desktop = next(row for row in report['components'] if row['id'] == 'desktop_app')
        self.assertEqual(desktop['state'], 'ok')
        self.assertIn('不接管', desktop['summary'])

    def test_enabled_but_stopped_gateway_requires_action(self):
        report = self.report(device_enabled=True, device_running=False)
        device = next(row for row in report['components'] if row['id'] == 'epaper')
        self.assertEqual(device['state'], 'error')
        self.assertEqual(report['overall'], 'action_required')
        self.assertTrue(any(row['component'] == 'epaper' for row in report['actions']))

    def test_failed_delivery_is_human_readable_warning(self):
        report = self.report(failed=2)
        notifications = next(row for row in report['components'] if row['id'] == 'notifications')
        self.assertEqual(notifications['state'], 'warn')
        self.assertIn('2 条', notifications['summary'])
        self.assertEqual(report['overall'], 'attention')

    def test_export_contains_only_sanitized_report_and_readme(self):
        report = self.report()
        payload = server.build_diagnostics_archive(report)
        with zipfile.ZipFile(io.BytesIO(payload), 'r') as archive:
            self.assertEqual(set(archive.namelist()), {
                'diagnostics.json', 'README-zh.txt', 'github-issue.md'})
            diagnostic_text = archive.read('diagnostics.json').decode('utf-8')
            readme = archive.read('README-zh.txt').decode('utf-8')
            issue = archive.read('github-issue.md').decode('utf-8')
        self.assertEqual(json.loads(diagnostic_text), report)
        self.assertIn('脱敏诊断包', readme)
        self.assertIn('问题反馈', issue)
        self.assertNotIn('device-super-secret-token', diagnostic_text + readme + issue)

    def test_history_deduplicates_stable_state_and_tracks_changes(self):
        first = self.report(harness=True)
        second = self.report(harness=True)
        self.assertEqual(first['history']['samples'], 0)
        self.assertEqual(second['history']['samples'], 1)
        self.assertEqual(len(server.load_diagnostics_history()), 1)
        changed = self.report(harness=False)
        harness = next(row for row in changed['components'] if row['id'] == 'harness')
        self.assertEqual(harness['trend'], 'new_issue')
        self.assertEqual(len(server.load_diagnostics_history()), 2)

    def test_repairs_are_strictly_whitelisted(self):
        with self.assertRaises(ValueError):
            server.repair_product_component('start_external_program')
        with patch.object(server, 'em_indices_any', return_value=[]), \
                patch.object(server, 'build_product_diagnostics', return_value={'overall': 'ok'}):
            result = server.repair_product_component('recheck_market_sources')
        self.assertTrue(result['ok'])
        self.assertEqual(result['action'], 'recheck_market_sources')

    def test_market_repair_verifies_tencent_when_primary_stays_down(self):
        with patch.object(server, 'em_indices_any', side_effect=RuntimeError('primary down')), \
                patch.object(server, 'tq_quote', return_value={'price': 10}), \
                patch.object(server, 'build_product_diagnostics', return_value={'overall': 'attention'}):
            result = server.repair_product_component('recheck_market_sources')
        self.assertTrue(result['ok'])
        self.assertIn('腾讯备援可用', result['message'])


if __name__ == '__main__':
    unittest.main()
