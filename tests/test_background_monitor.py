import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

import server


class BackgroundMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.profile = os.path.join(self.temp.name, 'profile.json')
        self.profile_patch = patch.object(server, 'PROFILE_FILE', self.profile)
        self.profile_patch.start()

    def tearDown(self):
        self.profile_patch.stop()
        self.temp.cleanup()

    def _save_alert(self, direction='up', threshold=10.0):
        server.save_profile({'alerts': [{
            'id': 'alert-1', 'code': '600001', 'name': '测试股票',
            'dir': direction, 'price': threshold, 'triggered': False,
        }]})

    def test_monitor_is_opt_in_and_interval_is_bounded(self):
        self.assertFalse(server.normalize_monitor_config({})['enabled'])
        self.assertEqual(server.normalize_monitor_config({'intervalSeconds': 1})['interval_seconds'], 10)
        self.assertEqual(server.normalize_monitor_config({'intervalSeconds': 999})['interval_seconds'], 120)
        self.assertTrue(server.normalize_monitor_config({'enabled': True})['market_hours_only'])

    def test_closed_market_does_not_request_quotes(self):
        self._save_alert()
        called = []
        result = server.process_background_alerts_once(
            datetime(2026, 8, 22, 10, 0, tzinfo=server.BJC),
            quote_loader=lambda code: called.append(code),
        )
        self.assertEqual(result['state'], 'paused_market_closed')
        self.assertEqual(called, [])

    def test_reached_price_is_atomically_marked_and_published(self):
        self._save_alert(direction='up', threshold=10.0)
        with patch.object(server, 'cached', side_effect=lambda key, ttl, loader: loader()):
            result = server.process_background_alerts_once(
                datetime(2026, 8, 21, 10, 0, tzinfo=server.BJC),
                quote_loader=lambda code: {'code': code, 'price': 10.5},
            )
        self.assertEqual(result['triggered'], 1)
        data = server.load_profile()['data']
        self.assertTrue(data['alerts'][0]['triggered'])
        self.assertEqual(data['alerts'][0]['triggered_by'], 'background-monitor')
        self.assertEqual(data['attention_inbox'][0]['id'], 'price:alert-1')
        self.assertIn('设置了上破', data['attention_inbox'][0]['reason'])

    def test_unreached_or_already_triggered_price_is_not_duplicated(self):
        self._save_alert(direction='down', threshold=9.0)
        with patch.object(server, 'cached', side_effect=lambda key, ttl, loader: loader()):
            first = server.process_background_alerts_once(
                datetime(2026, 8, 21, 10, 0, tzinfo=server.BJC),
                quote_loader=lambda code: {'code': code, 'price': 9.5},
            )
            second = server.process_background_alerts_once(
                datetime(2026, 8, 21, 10, 1, tzinfo=server.BJC),
                quote_loader=lambda code: {'code': code, 'price': 8.8},
            )
            third = server.process_background_alerts_once(
                datetime(2026, 8, 21, 10, 2, tzinfo=server.BJC),
                quote_loader=lambda code: {'code': code, 'price': 8.7},
            )
        self.assertEqual(first['triggered'], 0)
        self.assertEqual(second['triggered'], 1)
        self.assertEqual(third['triggered'], 0)
        self.assertEqual(len(server.load_profile()['data']['attention_inbox']), 1)

    def test_save_monitor_config_persists_explicit_consent(self):
        result = server.save_monitor_config({'enabled': True, 'intervalSeconds': 20})
        config = result['data']['background_monitor']
        self.assertTrue(config['enabled'])
        self.assertEqual(config['interval_seconds'], 20)
        self.assertIsNotNone(config['enabled_at'])


if __name__ == '__main__':
    unittest.main()
