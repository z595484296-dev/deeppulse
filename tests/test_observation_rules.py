import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import observation_rules
import server


WATCH = [{'code': '601138', 'name': '工业富联'}]


class ObservationRuleModelTests(unittest.TestCase):
    def test_whitelist_parser_builds_composite_rule(self):
        parsed = observation_rules.parse_intent(
            '情绪温度低于 45，并且工业富联股价跌破 60 元时提醒我复盘', WATCH,
            datetime(2026, 8, 21, 10, 0, tzinfo=observation_rules.BJ))
        self.assertEqual(parsed['blockers'], [])
        self.assertEqual(parsed['draft']['logic'], 'all')
        self.assertEqual(parsed['draft']['target']['code'], '601138')
        self.assertEqual([row['signal'] for row in parsed['draft']['clauses']],
                         ['emotion.temperature', 'quote.price'])

    def test_trade_action_and_mixed_logic_are_blocked(self):
        parsed = observation_rules.parse_intent(
            '温度低于 45 并且工业富联涨幅超过 3%，或者退潮期就自动卖出', WATCH)
        self.assertTrue(any('交易' in row for row in parsed['blockers']))
        self.assertTrue(any('混合' in row for row in parsed['blockers']))

    def test_single_price_rule_routes_to_existing_alert(self):
        parsed = observation_rules.parse_intent('工业富联股价上破 70 元提醒我', WATCH)
        self.assertTrue(any('价格提醒' in row for row in parsed['blockers']))

    def test_unknown_input_never_becomes_true(self):
        rule = observation_rules.normalize_draft({
            'clauses': [{'signal': 'emotion.temperature', 'operator': 'lte', 'value': 45}],
            'expiresAt': (datetime.now(observation_rules.BJ) + timedelta(days=2)).isoformat(),
        }, WATCH)
        self.assertIsNone(observation_rules.evaluate(rule, {})['truth'])
        self.assertFalse(observation_rules.evaluate(rule, {'emotion.temperature': 50})['truth'])
        self.assertTrue(observation_rules.evaluate(rule, {'emotion.temperature': 40})['truth'])


class ObservationRuleServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.profile_patch = patch.object(server, 'PROFILE_FILE', os.path.join(self.temp.name, 'profile.json'))
        self.profile_patch.start()
        server.save_profile({'watchlist': WATCH})

    def tearDown(self):
        self.profile_patch.stop()
        self.temp.cleanup()

    def _rule(self, last_truth=False, needs_reset=False):
        draft = observation_rules.normalize_draft({
            'title': '低温与价格观察', 'logic': 'all', 'target': WATCH[0],
            'clauses': [
                {'signal': 'emotion.temperature', 'operator': 'lte', 'value': 45},
                {'signal': 'quote.price', 'operator': 'lte', 'value': 60},
            ],
            'expiresAt': (server.now_bj() + timedelta(days=2)).isoformat(),
        }, WATCH, server.now_bj())
        return {**draft, 'id': 'observation-rule:test', 'version': 1, 'status': 'active',
                'runtime': {'state': 'armed', 'lastTruth': last_truth,
                            'needsFalseToRearm': needs_reset, 'triggerCount': 0}}

    def test_preview_then_confirm_establishes_baseline_without_attention(self):
        parsed = server.parse_observation_rule('温度低于 45，并且工业富联股价跌破 60 元时提醒我')
        evidence = ([{'signal': 'emotion.temperature', 'value': 40, 'sourceId': 'emotion-engine'},
                     {'signal': 'quote.price', 'value': 55, 'sourceId': 'test'}])
        with patch.object(server, '_observation_values', return_value=(
                {'emotion.temperature': 40, 'quote.price': 55}, evidence, [])):
            preview = server.preview_observation_rule(parsed['draft'])
            result = server.confirm_observation_rule(
                preview['previewId'], preview['profileRevision'], confirmed=True)
        self.assertTrue(result['rule']['runtime']['lastTruth'])
        self.assertTrue(result['rule']['runtime']['needsFalseToRearm'])
        self.assertEqual(server.load_profile()['data'].get('attention_inbox', []), [])

    def test_false_to_true_triggers_once_and_stays_quiet_while_true(self):
        server.save_profile({'observation_rules': [self._rule()]})
        emotion = lambda: {'server_time': '2026-08-21 10:00:00', 'date': '2026-08-21',
                           'engine': {'temp': 40, 'phase': '修复期', 'raw': {'zb_rate': .2}}}
        quote = lambda code: {'price': 55, 'pct': -2, 'source': 'test'}
        now = datetime(2026, 8, 21, 10, 0, tzinfo=server.BJC)
        first = server.process_observation_rules_once(now, quote, emotion)
        second = server.process_observation_rules_once(now + timedelta(minutes=1), quote, emotion)
        self.assertEqual(first['triggered'], 1)
        self.assertEqual(second['triggered'], 0)
        self.assertEqual(len(server.load_profile()['data']['attention_inbox']), 1)
        self.assertEqual(server.load_profile()['data']['attention_inbox'][0]['observationRuleId'],
                         'observation-rule:test')

    def test_missing_input_degrades_without_trigger(self):
        server.save_profile({'observation_rules': [self._rule()]})
        emotion = lambda: {'server_time': '2026-08-21 10:00:00', 'date': '2026-08-21',
                           'engine': {'temp': None, 'phase': '数据不可用', 'raw': {}, 'degraded': True}}
        result = server.process_observation_rules_once(
            datetime(2026, 8, 21, 10, 0, tzinfo=server.BJC),
            lambda code: {'price': 55, 'pct': -2, 'source': 'test'}, emotion)
        self.assertEqual(result['triggered'], 0)
        self.assertEqual(result['outcomes'][0]['state'], 'degraded')


if __name__ == '__main__':
    unittest.main()
