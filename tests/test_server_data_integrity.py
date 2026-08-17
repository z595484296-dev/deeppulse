import os
import tempfile
import unittest
from unittest.mock import patch

import server


class TradingDateTests(unittest.TestCase):
    def test_previous_trade_date_skips_weekend(self):
        rows = [
            {'date': '2026-08-13'},
            {'date': '2026-08-14'},
            {'date': '2026-08-17'},
        ]
        self.assertEqual(server.previous_trade_date('20260817', rows), '20260814')

    def test_premium_requests_verified_previous_trading_day(self):
        calls = []

        def fake_pool(kind, date=None, size=250):
            calls.append((kind, date))
            return {'qdate': '20260817', 'total': 1, 'pool': [
                {'code': '000002', 'name': '今日股', 'lbc': 1},
            ]}

        def fake_cached(key, ttl, loader):
            if key == 'sh_kline60':
                return {'rows': [{'date': '2026-08-14'}, {'date': '2026-08-17'}]}
            if key == 'bk0815_members':
                return [{'code': '000001', 'name': '昨日股', 'hybk': '测试',
                         'pct': 2.5, 'open': 10.1, 'high': 10.5, 'prev_close': 10.0}]
            if key == 'pool_ZB':
                return {'pool': []}
            return loader()

        with patch.object(server, 'em_pool', side_effect=fake_pool), \
                patch.object(server, 'cached', side_effect=fake_cached):
            result = server.em_premium()

        self.assertEqual(result['prev_date'], '20260814')
        self.assertNotIn(('ZT', '20260816'), calls)

    def test_premium_uses_board_members_and_does_not_invent_chain_height(self):
        today = {'qdate': '20260817', 'total': 1,
                 'pool': [{'code': '000001', 'name': '昨日股', 'lbc': 2}]}

        def fake_cached(key, ttl, loader):
            if key == 'sh_kline60':
                return {'rows': [{'date': '2026-08-14'}, {'date': '2026-08-17'}]}
            if key == 'bk0815_members':
                return [{'code': '000001', 'name': '昨日股', 'hybk': '测试',
                         'pct': 10.0, 'open': 10.0, 'high': 11.0, 'prev_close': 10.0}]
            if key == 'pool_ZB':
                return {'pool': []}
            return loader()

        with patch.object(server, 'em_pool', return_value=today), \
                patch.object(server, 'cached', side_effect=fake_cached):
            result = server.em_premium()
        self.assertIsNone(result['stats']['lb_ratio'])
        self.assertIsNone(result['list'][0]['prev_lbc'])
        self.assertEqual(result['source']['id'], 'BK0815')


class ProfileTests(unittest.TestCase):
    def test_profile_roundtrip_preserves_explicit_empty_lists(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile_file = os.path.join(tmp, 'profile.json')
            with patch.object(server, 'PROFILE_FILE', profile_file):
                first = server.save_profile({'watchlist': [{'code': '600519'}], 'alerts': []})
                second = server.save_profile({'watchlist': []})
                loaded = server.load_profile()

        self.assertEqual(first['revision'], 1)
        self.assertEqual(second['revision'], 2)
        self.assertEqual(loaded['data']['watchlist'], [])
        self.assertEqual(loaded['data']['alerts'], [])

    def test_profile_rejects_unknown_or_oversized_shapes(self):
        with self.assertRaises(ValueError):
            server.save_profile({'unknown': []})
        with self.assertRaises(ValueError):
            server.save_profile({'watchlist': 'not-a-list'})


class SectorHistoryTests(unittest.TestCase):
    def test_sector_cycle_uses_only_recorded_trading_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            history_file = os.path.join(tmp, 'sector_history.json')
            with patch.object(server, 'SECTOR_HISTORY_FILE', history_file):
                server.record_sector_snapshot('20260814', [
                    {'hybk': '算力'}, {'hybk': '算力'}, {'hybk': '消费'},
                ])
                server.record_sector_snapshot('20260817', [
                    {'hybk': '算力'}, {'hybk': '半导体'}, {'hybk': '半导体'},
                ])
                result = server.em_sector_cycle(5)

        self.assertEqual(result['dates'], ['2026-08-14', '2026-08-17'])
        self.assertEqual(result['source'], 'local_snapshots')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['sectors'][0]['streak'], 2)

    def test_sector_cycle_reports_collection_state_instead_of_fake_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            history_file = os.path.join(tmp, 'sector_history.json')
            with patch.object(server, 'SECTOR_HISTORY_FILE', history_file):
                server.record_sector_snapshot('20260817', [{'hybk': '算力'}])
                result = server.em_sector_cycle(5)
        self.assertEqual(result['status'], 'collecting')
        self.assertEqual(result['sectors'], [])


if __name__ == '__main__':
    unittest.main()
