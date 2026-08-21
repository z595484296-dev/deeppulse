import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import research_hypothesis
import server


BJC = timezone(timedelta(hours=8))
NOW = datetime(2026, 8, 24, 16, 0, tzinfo=BJC)


def saved_hypothesis():
    event = {
        'event': {
            'id': 'event-ai-1', 'type': 'headline', 'title': '算力基础设施大会召开',
            'observedAt': '2026-08-21T10:00:00+08:00',
            'sources': [{'id': 'source-1', 'name': '公开来源', 'tier': 'market'}],
        },
        'sectors': ['通信设备'],
        'watchlist': [{'code': '601138', 'name': '工业富联', 'basis': '行业重合'}],
        'quality': {'score': 75, 'corroborated': False},
    }
    return research_hypothesis.create_hypothesis(
        event, 1, '只观察，不追涨', datetime(2026, 8, 21, 10, tzinfo=BJC))


class ResearchCockpitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.profile_file = os.path.join(self.temp.name, 'profile.json')
        with open(self.profile_file, 'w', encoding='utf-8') as handle:
            json.dump({'schema': 1, 'revision': 0, 'data': {}}, handle)
        self.profile_patch = patch.object(server, 'PROFILE_FILE', self.profile_file)
        self.now_patch = patch.object(server, 'now_bj', return_value=NOW)
        self.diagnostics_patch = patch.object(server, 'build_product_diagnostics', return_value={
            'components': [], 'overall': 'ok',
        })
        self.profile_patch.start()
        self.now_patch.start()
        self.diagnostics_patch.start()

    def tearDown(self):
        self.diagnostics_patch.stop()
        self.now_patch.stop()
        self.profile_patch.stop()
        self.temp.cleanup()

    def test_due_hypothesis_becomes_explainable_focus_without_automatic_action(self):
        item = saved_hypothesis()
        server.save_profile({'research_hypotheses': [item]})
        cockpit = server.research_cockpit_status()
        focus = cockpit['focus'][0]
        self.assertEqual(focus['sourceType'], 'hypothesis')
        self.assertEqual(focus['level'], 'now')
        self.assertEqual(focus['nextAction']['type'], 'review')
        self.assertTrue(any(row['basis'] == 'registered-review-window'
                            for row in focus['reasons']))
        self.assertEqual(cockpit['method'], 'transparent-rules-plus-explicit-user-adjustment')
        self.assertFalse(cockpit['automaticGoalInference'])
        self.assertFalse(cockpit['automaticTradingActions'])

    def test_priority_control_is_explicit_reversible_and_does_not_edit_hypothesis(self):
        hypothesis = saved_hypothesis()
        server.save_profile({'research_hypotheses': [hypothesis]})
        item_id = server.research_cockpit_status()['focus'][0]['id']
        raised = server.mutate_research_cockpit('raise_priority', {'itemId': item_id})['cockpit']
        adjusted = next(row for row in raised['items'] if row['id'] == item_id)
        self.assertEqual(adjusted['adjustment'], 10)
        self.assertTrue(adjusted['userAdjusted'])
        stored = server.load_profile()['data']['research_hypotheses'][0]
        self.assertEqual(stored['statement'], hypothesis['statement'])
        reset = server.mutate_research_cockpit('reset', {'itemId': item_id})['cockpit']
        restored = next(row for row in reset['items'] if row['id'] == item_id)
        self.assertEqual(restored['adjustment'], 0)
        self.assertFalse(restored['userAdjusted'])

    def test_snooze_hides_focus_but_preserves_source_item(self):
        hypothesis = saved_hypothesis()
        server.save_profile({'research_hypotheses': [hypothesis]})
        item_id = server.research_cockpit_status()['focus'][0]['id']
        cockpit = server.mutate_research_cockpit('snooze', {'itemId': item_id})['cockpit']
        self.assertFalse(any(row['id'] == item_id for row in cockpit['focus']))
        self.assertTrue(next(row for row in cockpit['items'] if row['id'] == item_id)['snoozed'])
        self.assertEqual(cockpit['summary']['snoozed'], 1)
        self.assertEqual(len(server.load_profile()['data']['research_hypotheses']), 1)

    def test_watchlist_without_hypothesis_is_labeled_as_unmapped_not_predicted(self):
        server.save_profile({'watchlist': [
            {'code': '601138', 'name': '工业富联', 'note': '跟踪订单兑现', 'added': 1},
        ]})
        cockpit = server.research_cockpit_status()
        item = next(row for row in cockpit['items'] if row['sourceType'] == 'watchlist')
        self.assertIn('尚无研究假设', item['title'])
        self.assertEqual(item['origin'], '你的自选列表')
        self.assertEqual(item['nextAction']['type'], 'define_question')
        self.assertIn('不会推断未记录目标', cockpit['boundary'])

    def test_damaged_legacy_hypothesis_text_gets_a_readable_fallback(self):
        item = saved_hypothesis()
        item['statement'] = '观察????????????????????????'
        item['baseline']['title'] = '????????????'
        server.save_profile({'research_hypotheses': [item]})
        focus = server.research_cockpit_status()['focus'][0]
        self.assertEqual(focus['title'], '复盘 601138 相关事件研究假设')
        self.assertTrue(focus['subtitle'].startswith('创建于 '))
        self.assertNotIn('???', focus['title'] + focus['subtitle'])


if __name__ == '__main__':
    unittest.main()
