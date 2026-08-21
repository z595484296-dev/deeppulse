import os
import sys
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from akshare_research import build_snapshot, unloaded_snapshot  # noqa: E402
import server  # noqa: E402


NOW = datetime(2026, 8, 22, 8, 0, 0)


def rows():
    return {
        'macro_china_pmi_yearly': [
            {'日期': '2026-07-31', '今值': 50.2},
            {'日期': '2026-08-31', '今值': None},
        ],
        'macro_china_cpi_yearly': [{'日期': '2026-07-09', '今值': 0.4}],
        'macro_china_ppi_yearly': [{'日期': '2025-10-09', '今值': -1.2}],
        'macro_china_lpr': [
            {'TRADE_DATE': '2026-08-20', 'LPR1Y': 3.0, 'LPR5Y': 3.5},
        ],
        'bond_zh_us_rate': [{
            '日期': '2026-08-21', '中国国债收益率10年': 1.68,
            '美国国债收益率10年': 4.74,
            '中国国债收益率10年-2年': 0.44, '美国国债收益率10年-2年': 0.5,
        }],
    }


class AkshareResearchModelTests(unittest.TestCase):
    def test_snapshot_preserves_lineage_and_never_changes_emotion_score(self):
        data = rows()
        snapshot = build_snapshot(lambda name, **kwargs: data[name], '1.16.82', NOW)

        self.assertEqual(snapshot['status'], 'degraded')
        self.assertFalse(snapshot['includedInEmotionScore'])
        self.assertFalse(snapshot['automaticTradingAction'])
        self.assertEqual(snapshot['marketBreadth']['status'], 'kept-on-primary-chain')
        metrics = {row['id']: row for module in snapshot['modules'] for row in module['metrics']}
        self.assertEqual(metrics['china-lpr-1y']['source']['independentGroup'], 'eastmoney')
        self.assertEqual(metrics['china-pmi']['source']['independentGroup'], 'jin10')
        self.assertFalse(metrics['china-pmi']['includedInEmotionScore'])

    def test_future_null_release_is_skipped_and_old_value_is_marked_stale(self):
        data = rows()
        snapshot = build_snapshot(lambda name, **kwargs: data[name], 'test', NOW)
        metrics = {row['id']: row for module in snapshot['modules'] for row in module['metrics']}

        self.assertEqual(metrics['china-pmi']['asOf'], '2026-07-31')
        self.assertEqual(metrics['china-pmi']['status'], 'current')
        self.assertEqual(metrics['china-ppi-yoy']['status'], 'stale')
        self.assertGreater(metrics['china-ppi-yoy']['stalenessDays'], 50)

    def test_one_interface_failure_is_isolated_and_reported(self):
        data = rows()

        def fetch(name, **kwargs):
            if name == 'macro_china_cpi_yearly':
                raise RuntimeError('upstream changed')
            return data[name]

        snapshot = build_snapshot(fetch, 'test', NOW)
        metrics = {row['id']: row for module in snapshot['modules'] for row in module['metrics']}

        self.assertEqual(snapshot['status'], 'degraded')
        self.assertEqual(metrics['china-cpi-yoy']['status'], 'unavailable')
        self.assertEqual(snapshot['errors'][0]['interface'], 'macro_china_cpi_yearly')
        self.assertEqual(metrics['china-lpr-1y']['status'], 'current')

    def test_unloaded_state_does_not_claim_data_was_observed(self):
        snapshot = unloaded_snapshot(installed=True, version='1.16.82')
        self.assertEqual(snapshot['status'], 'not_loaded')
        self.assertIsNone(snapshot['generatedAt'])
        self.assertEqual(snapshot['summary']['metrics'], 0)


class AkshareResearchServerTests(unittest.TestCase):
    def setUp(self):
        server.cache_drop('akshare_research_snapshot_v1')

    def tearDown(self):
        server.cache_drop('akshare_research_snapshot_v1')

    def test_snapshot_is_on_demand_then_reused_from_cache(self):
        data = rows()
        calls = []

        def method(name):
            def run(**kwargs):
                calls.append(name)
                return data[name]
            return run

        module = SimpleNamespace(__version__='test')
        for name in data:
            setattr(module, name, method(name))

        with patch.object(server, 'load_akshare', return_value=module), \
                patch.object(server, 'now_bj', return_value=NOW):
            unloaded = server.akshare_research_snapshot(refresh=False)
            loaded = server.akshare_research_snapshot(refresh=True)
            cached = server.akshare_research_snapshot(refresh=False)

        self.assertEqual(unloaded['status'], 'not_loaded')
        self.assertEqual(loaded['modelVersion'], 'akshare-research-v1')
        self.assertEqual(cached, loaded)
        self.assertEqual(len(calls), 5)


if __name__ == '__main__':
    unittest.main()
