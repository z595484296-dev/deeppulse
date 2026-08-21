import unittest
from unittest.mock import patch

import emotion


def make_raw(**overrides):
    rows = [{'close': 100 + index * 0.1, 'volume': 100.0} for index in range(20)]
    rows.append({'close': 102.0, 'volume': 100.0})
    raw = {
        'date': '2026-08-15',
        'server_time': '2026-08-15 15:05:00',
        'pool_error': False,
        'bk_ok': True,
        'kline_ok': True,
        'flow_ok': True,
        'zt': 60,
        'dt': 8,
        'zb': 15,
        'zt_pool': [{'lbc': value} for value in (5, 4, 3, 2, 2, 1)],
        'breadth': {'bins': {'0~3%': 2000}, 'up': 3000, 'down': 2000, 'flat': 300, 'total': 5300},
        'indices': [{'amount': 5e11}, {'amount': 6e11}],
        'flows': {'sh': [{'main': 5e9}], 'sz': [{'main': 5e9}]},
        'bk': {'zt': {'pct': 1.5, 'price': 100}, 'lb': {'pct': 0.8, 'price': 100}},
        'sh_kline': rows,
    }
    raw.update(overrides)
    return raw


class ScoreBoundaryTests(unittest.TestCase):
    def test_limit_down_score_decreases_at_documented_boundaries(self):
        self.assertEqual(emotion.score_dt(0), 15)
        self.assertEqual(emotion.score_dt(3), 15)
        self.assertEqual(emotion.score_dt(8), 10)
        self.assertEqual(emotion.score_dt(15), 0)
        self.assertEqual(emotion.score_dt(25), -10)
        self.assertEqual(emotion.score_dt(26), -20)

    def test_broken_board_score_decreases_as_rate_rises(self):
        self.assertEqual(emotion.score_zb(0.10), 10)
        self.assertEqual(emotion.score_zb(0.25), 5)
        self.assertEqual(emotion.score_zb(0.35), 0)
        self.assertEqual(emotion.score_zb(0.45), -10)
        self.assertEqual(emotion.score_zb(0.46), -20)

    def test_all_scores_share_the_declared_scale(self):
        samples = [emotion.score_zt(200), emotion.score_zt(0), emotion.score_dt(100),
                   emotion.score_zb(1), emotion.score_height(20), emotion.score_flow(1e9)]
        self.assertTrue(all(-20 <= value <= 20 for value in samples))


class EmotionEngineTests(unittest.TestCase):
    def compute(self, raw, history=None):
        with patch.object(emotion, 'load_weights', return_value=dict(emotion.DEFAULT_WEIGHTS)):
            return emotion.compute_emotion(raw, history or [])

    def test_complete_snapshot_exposes_quality_dynamics_and_dimensions(self):
        result = self.compute(make_raw(), [{'date': '2026-08-14', 'temp': 45, 'phase': '发酵期'}])
        self.assertEqual(result['model_version'], '2.0')
        self.assertEqual(result['coverage'], 100)
        self.assertGreaterEqual(result['confidence'], 90)
        self.assertTrue(result['actionable'])
        self.assertEqual(len(result['dimensions']), 6)
        self.assertIsNotNone(result['dynamics']['delta1'])
        self.assertFalse(result['transition']['calibrated'])

    def test_missing_core_pool_blocks_risk_exposure_reference(self):
        result = self.compute(make_raw(pool_error=True))
        self.assertFalse(result['actionable'])
        self.assertEqual(result['phase'], '数据不足')
        self.assertEqual(result['advice']['position'], '--')
        self.assertTrue(any('暂停模型风险暴露区间' in risk for risk in result['risks']))

    def test_ma20_uses_exactly_twenty_latest_closes(self):
        rows = [{'close': value, 'volume': 100} for value in range(1, 22)]
        result = self.compute(make_raw(sh_kline=rows))
        self.assertEqual(result['raw']['ma20'], 11.5)

    def test_intraday_volume_is_compared_on_a_same_time_basis(self):
        rows = [{'close': 100, 'volume': 100} for _ in range(20)]
        rows.append({'close': 100, 'volume': 24})
        result = self.compute(make_raw(server_time='2026-08-15 10:00:00', sh_kline=rows))
        self.assertAlmostEqual(result['raw']['volume_fraction'], 0.24, places=2)
        self.assertAlmostEqual(result['raw']['vol_ratio'], 1.0, places=2)
        self.assertEqual(result['raw']['volume_basis'], '盘中同刻折算')

    def test_overheat_risk_is_included_in_risk_list(self):
        pool = [{'lbc': 6} for _ in range(12)]
        hot = make_raw(
            zt=150, dt=0, zb=0, zt_pool=pool,
            breadth={'bins': {'3~5%': 4500}, 'up': 4500, 'down': 500, 'flat': 300, 'total': 5300},
            bk={'zt': {'pct': 4.0, 'price': 100}, 'lb': {'pct': 4.0, 'price': 100}},
            flows={'sh': [{'main': 3e10}], 'sz': [{'main': 3e10}]},
        )
        rows = [{'close': 100, 'volume': 100} for _ in range(20)]
        rows.append({'close': 105, 'volume': 150})
        hot['sh_kline'] = rows
        result = self.compute(hot)
        self.assertGreaterEqual(result['temp'], 80)
        self.assertTrue(any('情绪过热' in risk for risk in result['risks']))


if __name__ == '__main__':
    unittest.main()
