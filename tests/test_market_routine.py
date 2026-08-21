import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

import server


class MarketRoutineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.profile = os.path.join(self.temp.name, 'profile.json')
        self.history = os.path.join(self.temp.name, 'history.json')
        self.profile_patch = patch.object(server, 'PROFILE_FILE', self.profile)
        self.history_patch = patch.object(server, 'HISTORY_FILE', self.history)
        self.profile_patch.start()
        self.history_patch.start()

    def tearDown(self):
        self.history_patch.stop()
        self.profile_patch.stop()
        self.temp.cleanup()

    def _enable(self, **tasks):
        return server.save_routine_config({'tasks': tasks})

    @staticmethod
    def _emotion(degraded=False):
        return {
            'date': '2026-08-21',
            'breadth': {'up': 3200, 'down': 1700},
            'engine': {
                'date': '2026-08-21', 'temp': 58, 'phase': '发酵期',
                'degraded': degraded,
                'raw': {'zt': 48, 'dt': 6, 'zb': 15},
            },
        }

    def test_each_service_window_requires_explicit_consent(self):
        config = server.normalize_routine_config({})
        self.assertFalse(config['enabled'])
        self.assertEqual(config['tasks'], {
            'pre_market': False, 'intraday': False, 'close_review': False,
        })
        saved = self._enable(pre_market=True, intraday=False, close_review=True)
        self.assertTrue(saved['data']['market_routine']['enabled'])
        self.assertTrue(saved['data']['market_routine']['tasks']['pre_market'])
        self.assertFalse(saved['data']['market_routine']['tasks']['intraday'])

    def test_windows_and_weekend_are_deterministic(self):
        self.assertEqual(server._routine_due_kind(
            datetime(2026, 8, 21, 8, 50, tzinfo=server.BJC)), 'pre_market')
        self.assertEqual(server._routine_due_kind(
            datetime(2026, 8, 21, 10, 30, tzinfo=server.BJC)), 'intraday')
        self.assertEqual(server._routine_due_kind(
            datetime(2026, 8, 21, 15, 20, tzinfo=server.BJC)), 'close_review')
        self.assertIsNone(server._routine_due_kind(
            datetime(2026, 8, 22, 10, 30, tzinfo=server.BJC)))

    def test_confirmed_holiday_is_not_scheduled(self):
        holiday = lambda current: {
            'date': '2026-10-01', 'is_trade_date': False,
            'confirmed': True, 'basis': 'AKShare 交易日历',
        }
        self.assertIsNone(server._routine_due_kind(
            datetime(2026, 10, 1, 10, 30, tzinfo=server.BJC), holiday))

    def test_pre_market_uses_last_close_and_is_idempotent(self):
        with open(self.history, 'w', encoding='utf-8') as handle:
            json.dump({'snapshots': [{'date': '2026-08-20', 'temp': 55, 'phase': '发酵期'}]}, handle)
        server.save_profile({
            'watchlist': [{'code': '601138'}],
            'alerts': [{'id': 'a1', 'triggered': False}],
        })
        self._enable(pre_market=True)
        now = datetime(2026, 8, 21, 9, 0, tzinfo=server.BJC)
        first = server.process_market_routine_once(now)
        second = server.process_market_routine_once(now)
        self.assertEqual(first['published'], 1)
        self.assertEqual(second['published'], 0)
        data = server.load_profile()['data']
        self.assertEqual(len(data['attention_inbox']), 1)
        self.assertIn('2026-08-20', data['attention_inbox'][0]['detail'])
        self.assertEqual(data['attention_inbox'][0]['dataDate'], '2026-08-20')
        self.assertEqual(len(data['routine_receipts']), 1)

    def test_intraday_publishes_explainable_structure_once(self):
        self._enable(intraday=True)
        now = datetime(2026, 8, 21, 10, 30, tzinfo=server.BJC)
        first = server.process_market_routine_once(now, emotion_loader=lambda force: self._emotion())
        second = server.process_market_routine_once(now, emotion_loader=lambda force: self._emotion())
        self.assertEqual(first['published'], 1)
        self.assertEqual(second['state'], 'completed_window')
        item = first['item']
        self.assertIn('发酵期', item['title'])
        self.assertIn('涨停 48', item['detail'])
        self.assertEqual(item['dataDate'], '2026-08-21')
        self.assertFalse(item['degraded'])

    def test_close_review_recognizes_saved_journal(self):
        server.save_profile({'journal': [{'date': '2026-08-21', 'text': '复盘'}]})
        self._enable(close_review=True)
        result = server.process_market_routine_once(
            datetime(2026, 8, 21, 15, 20, tzinfo=server.BJC),
            emotion_loader=lambda force: self._emotion(),
        )
        self.assertEqual(result['published'], 1)
        self.assertTrue(result['item']['journalSaved'])
        self.assertIn('复盘已保存', result['item']['detail'])

    def test_stale_intraday_payload_never_claims_current_market_direction(self):
        self._enable(intraday=True)
        result = server.process_market_routine_once(
            datetime(2026, 8, 24, 10, 30, tzinfo=server.BJC),
            emotion_loader=lambda force: self._emotion(),
        )
        self.assertEqual(result['item']['title'], '盘中数据尚未更新')
        self.assertTrue(result['item']['degraded'])
        self.assertEqual(result['item']['page'], 'datasrc')
        self.assertIn('并非今日', result['item']['detail'])

    def test_status_discloses_page_closed_boundary_and_next_service(self):
        self._enable(pre_market=True, intraday=True)
        status = server.market_routine_status(
            datetime(2026, 8, 22, 10, 0, tzinfo=server.BJC))
        self.assertEqual(status['runtime']['state'], 'non_trading_day')
        self.assertTrue(status['service_continues_when_page_closed'])
        self.assertTrue(status['service_stops_when_local_server_stops'])
        self.assertEqual(status['next_service']['kind'], 'pre_market')
        self.assertTrue(status['next_service']['at'].startswith('2026-08-24T08:45'))

    def test_calendar_fallback_is_explicit_when_akshare_is_missing(self):
        with patch('server.importlib.util.find_spec', return_value=None):
            info = server.market_calendar_info(
                datetime(2026, 8, 21, 9, 0, tzinfo=server.BJC))
        self.assertTrue(info['is_trade_date'])
        self.assertFalse(info['confirmed'])
        self.assertEqual(info['basis'], '工作日降级判断')


if __name__ == '__main__':
    unittest.main()
