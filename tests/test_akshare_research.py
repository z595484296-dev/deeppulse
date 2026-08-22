import os
import sys
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from akshare_research import build_snapshot, normalize_pack_ids, unloaded_snapshot  # noqa: E402
import server  # noqa: E402


NOW = datetime(2026, 8, 22, 8, 0, 0)


def rows():
    return {
        'macro_china_pmi': [
            {'月份': '2026年07月份', '制造业-指数': 49.2, '非制造业-指数': 49.0},
            {'月份': '2026年09月份', '制造业-指数': None, '非制造业-指数': None},
        ],
        'macro_china_gdp': [
            {'季度': '2026年第1-2季度', '国内生产总值-同比增长': 4.7},
        ],
        'macro_china_cpi': [{'月份': '2026年07月份', '全国-同比增长': 0.5}],
        'macro_china_ppi': [{'月份': '2025年10月份', '当月同比增长': -1.2}],
        'macro_china_money_supply': [{
            '月份': '2026年07月份', '货币(M1)-同比增长': 4.0,
            '货币和准货币(M2)-同比增长': 7.7,
        }],
        'macro_china_shibor_all': [{
            '日期': '2026-08-21', 'O/N-定价': 1.434, '1W-定价': 1.4256,
        }],
        'macro_china_lpr': [
            {'TRADE_DATE': '2026-08-20', 'LPR1Y': 3.0, 'LPR5Y': 3.5},
        ],
        'bond_zh_us_rate': [{
            '日期': '2026-08-21', '中国国债收益率10年': 1.68,
            '美国国债收益率10年': 4.74,
        }],
        'macro_china_foreign_exchange_gold': [{
            '统计时间': '2026.7', '黄金储备': 7608, '国家外汇储备': 34187.76,
        }],
    }


class AkshareResearchModelTests(unittest.TestCase):
    def test_snapshot_preserves_lineage_and_never_changes_emotion_score(self):
        data = rows()
        snapshot = build_snapshot(lambda name, **kwargs: data[name], '1.16.82', NOW)

        self.assertEqual(snapshot['modelVersion'], 'akshare-research-v2')
        self.assertEqual(snapshot['status'], 'degraded')
        self.assertFalse(snapshot['includedInEmotionScore'])
        self.assertFalse(snapshot['automaticTradingAction'])
        self.assertEqual(snapshot['marketBreadth']['status'], 'kept-on-primary-chain')
        metrics = {row['id']: row for module in snapshot['modules'] for row in module['metrics']}
        self.assertEqual(metrics['china-lpr-1y']['source']['independentGroup'], 'eastmoney')
        self.assertEqual(metrics['shibor-overnight']['source']['independentGroup'], 'jin10')
        self.assertEqual(metrics['china-pmi-manufacturing']['source']['interface'], 'macro_china_pmi')
        self.assertFalse(metrics['china-pmi-manufacturing']['includedInEmotionScore'])
        self.assertEqual(snapshot['sourceGroups'], ['eastmoney', 'jin10'])

    def test_chinese_month_and_quarter_are_parsed_and_old_value_is_stale(self):
        snapshot = build_snapshot(lambda name, **kwargs: rows()[name], 'test', NOW)
        metrics = {row['id']: row for module in snapshot['modules'] for row in module['metrics']}

        self.assertEqual(metrics['china-pmi-manufacturing']['asOf'], '2026-07-31')
        self.assertEqual(metrics['china-gdp-yoy']['asOf'], '2026-06-30')
        self.assertEqual(metrics['china-pmi-manufacturing']['status'], 'current')
        self.assertEqual(metrics['china-ppi-yoy']['status'], 'stale')

    def test_only_selected_packs_are_fetched(self):
        data = rows()
        calls = []

        def fetch(name, **kwargs):
            calls.append(name)
            return data[name]

        snapshot = build_snapshot(fetch, 'test', NOW, ['liquidity', 'reserves'])
        self.assertEqual(snapshot['selection'], ['liquidity', 'reserves'])
        self.assertEqual([row['id'] for row in snapshot['modules']], ['liquidity', 'reserves'])
        self.assertEqual(set(calls), {
            'macro_china_money_supply', 'macro_china_shibor_all',
            'macro_china_foreign_exchange_gold',
        })
        self.assertEqual(snapshot['sourceGroups'], ['eastmoney', 'jin10', 'sina'])

    def test_one_interface_failure_is_isolated_and_reported(self):
        data = rows()

        def fetch(name, **kwargs):
            if name == 'macro_china_cpi':
                raise RuntimeError('upstream changed')
            return data[name]

        snapshot = build_snapshot(fetch, 'test', NOW, ['prices', 'rates'])
        metrics = {row['id']: row for module in snapshot['modules'] for row in module['metrics']}

        self.assertEqual(snapshot['status'], 'degraded')
        self.assertEqual(metrics['china-cpi-yoy']['status'], 'unavailable')
        self.assertEqual(snapshot['errors'][0]['interface'], 'macro_china_cpi')
        self.assertEqual(metrics['china-lpr-1y']['status'], 'current')

    def test_unloaded_state_and_pack_normalization_are_explicit(self):
        snapshot = unloaded_snapshot(installed=True, version='1.16.82', selected_packs=['reserves', 'bad'])
        self.assertEqual(snapshot['status'], 'not_loaded')
        self.assertIsNone(snapshot['generatedAt'])
        self.assertEqual(snapshot['selection'], ['reserves'])
        self.assertEqual(normalize_pack_ids([]), ['growth', 'prices', 'liquidity', 'rates'])


class AkshareResearchServerTests(unittest.TestCase):
    def setUp(self):
        server.cache_drop('akshare_research_snapshot_v2')

    def tearDown(self):
        server.cache_drop('akshare_research_snapshot_v2')

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

        preferences = {'enabledPacks': ['growth', 'rates']}
        with patch.object(server, 'load_akshare', return_value=module), \
                patch.object(server, 'now_bj', return_value=NOW), \
                patch.object(server, 'load_akshare_research_preferences', return_value=preferences):
            unloaded = server.akshare_research_snapshot(refresh=False)
            loaded = server.akshare_research_snapshot(refresh=True)
            cached = server.akshare_research_snapshot(refresh=False)

        self.assertEqual(unloaded['status'], 'not_loaded')
        self.assertEqual(loaded['modelVersion'], 'akshare-research-v2')
        self.assertEqual(loaded['selection'], ['growth', 'rates'])
        self.assertEqual(cached, loaded)
        self.assertEqual(len(calls), 4)
        self.assertEqual(len(loaded['interfaceHealth']), 4)

    def test_preferences_are_normalized_before_profile_save(self):
        with patch.object(server, 'save_profile', return_value={'revision': 2}) as save:
            result = server.save_akshare_research_preferences({
                'enabledPacks': ['reserves', 'unknown', 'growth'],
                'automaticTradingAction': True,
            })
        saved = save.call_args.args[0]['akshare_research_preferences']
        self.assertEqual(saved['enabledPacks'], ['growth', 'reserves'])
        self.assertTrue(saved['manualOnly'])
        self.assertFalse(saved['automaticTradingAction'])
        self.assertEqual(result['preferences'], saved)


if __name__ == '__main__':
    unittest.main()
