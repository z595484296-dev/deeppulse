import json
import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import research_hypothesis
import research_memory
import server


BJC = timezone(timedelta(hours=8))


def event_item(event_id='event-ai-1', title='算力基础设施大会召开'):
    return {
        'event': {'id': event_id, 'type': 'headline', 'title': title,
                  'observedAt': '2026-08-21T10:00:00+08:00',
                  'sources': [{'id': 's1', 'name': '公开来源', 'tier': 'market'}]},
        'sectors': ['通信设备', '消费电子'],
        'watchlist': [{'code': '601138', 'name': '工业富联', 'basis': '行业重合'}],
        'quality': {'score': 75, 'corroborated': False},
    }


def reviewed_hypothesis(event_id='event-ai-1'):
    created = research_hypothesis.create_hypothesis(
        event_item(event_id), 3, now=datetime(2026, 8, 21, 10, tzinfo=BJC))
    return research_hypothesis.review_hypothesis(
        created, 'mixed', '行业有反馈，但官方披露仍不足',
        datetime(2026, 8, 26, 16, tzinfo=BJC),
        [created['falsifiers'][2]], ['官方公告原文', '板块成交额对照'])


class ResearchMemoryModelTests(unittest.TestCase):
    def test_only_user_confirmed_reviews_become_memory(self):
        open_item = research_hypothesis.create_hypothesis(event_item('open'), 3)
        reviewed = reviewed_hypothesis()
        snapshot = research_memory.build_snapshot([open_item, reviewed])
        self.assertEqual(snapshot['summary']['total'], 1)
        memory = snapshot['items'][0]
        self.assertEqual(memory['outcome'], 'mixed')
        self.assertEqual(memory['dataGaps'], ['官方公告原文', '板块成交额对照'])
        self.assertTrue(memory['sourceImmutable'])
        self.assertFalse(snapshot['automaticCausalInference'])
        self.assertFalse(snapshot['automaticStrategyChange'])
        self.assertFalse(snapshot['automaticTradingAction'])

    def test_similarity_is_structural_and_can_be_disabled(self):
        reviewed = reviewed_hypothesis()
        current = research_hypothesis.create_hypothesis(event_item('event-ai-2', '另一条算力事件'), 5)
        enabled = research_memory.build_snapshot([reviewed, current])
        matches = enabled['relatedByHypothesis'][current['id']]
        self.assertGreaterEqual(matches[0]['similarityScore'], 3)
        self.assertIn('共同自选：601138', matches[0]['reasons'])
        disabled = research_memory.build_snapshot(
            [reviewed, current], {'enabled': False})
        self.assertEqual(disabled['relatedByHypothesis'], {})


class ResearchMemoryServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.profile_file = os.path.join(self.tmp.name, 'profile.json')
        with open(self.profile_file, 'w', encoding='utf-8') as handle:
            json.dump({'schema': 1, 'revision': 0,
                       'data': {'research_hypotheses': [reviewed_hypothesis()]}}, handle)
        self.profile_patch = patch.object(server, 'PROFILE_FILE', self.profile_file)
        self.profile_patch.start()

    def tearDown(self):
        self.profile_patch.stop()
        self.tmp.cleanup()

    def test_lesson_and_hide_do_not_mutate_source_review(self):
        before = server.load_profile()['data']['research_hypotheses'][0]
        memory_id = server.research_memory_status()['items'][0]['id']
        saved = server.mutate_research_memory(
            'update_lesson', {'memoryId': memory_id, 'lesson': '先补齐一级来源再看价格对照'})
        self.assertEqual(saved['memory']['items'][0]['lesson'], '先补齐一级来源再看价格对照')
        server.mutate_research_memory('hide', {'memoryId': memory_id})
        hidden = server.research_memory_status()
        self.assertEqual(hidden['summary']['visible'], 0)
        self.assertEqual(hidden['summary']['hidden'], 1)
        after = server.load_profile()['data']['research_hypotheses'][0]
        self.assertEqual(before, after)

    def test_cockpit_hint_does_not_change_priority_score(self):
        profile = server.load_profile()
        current = research_hypothesis.create_hypothesis(event_item('event-ai-2'), 5)
        profile['data']['research_hypotheses'].append(current)
        with open(self.profile_file, 'w', encoding='utf-8') as handle:
            json.dump(profile, handle, ensure_ascii=False)
        cockpit = server.research_cockpit_status(
            diagnostics={'components': []})
        item = next(row for row in cockpit['items'] if row['sourceId'] == current['id'])
        self.assertTrue(item['memoryHints'])
        expected = 15 + sum(reason['points'] for reason in item['reasons'])
        self.assertEqual(item['defaultScore'], expected)
        self.assertEqual(item['adjustment'], 0)

    def test_legacy_mojibake_is_repaired_only_in_response_view(self):
        raw = research_hypothesis.create_hypothesis(event_item('legacy'), 5)
        raw['statement'] = '??????????'
        raw['baseline']['title'] = '??????????'
        raw['baseline']['sectors'] = ['????']
        raw['baseline']['watchlist'][0]['name'] = '????'
        raw['falsifiers'] = ['??????????']
        profile = server.load_profile()
        profile['data']['research_hypotheses'] = [raw]
        with open(self.profile_file, 'w', encoding='utf-8') as handle:
            json.dump(profile, handle, ensure_ascii=False)
        display = server.research_hypotheses_status()['items'][0]
        self.assertEqual(display['baseline']['title'], '601138 相关事件')
        self.assertEqual(display['baseline']['watchlist'][0]['name'], '601138')
        self.assertEqual(display['baseline']['sectors'], [])
        self.assertIn('核对行业、自选和大盘对照反馈', display['statement'])
        stored = server.load_profile()['data']['research_hypotheses'][0]
        self.assertEqual(stored['baseline']['title'], '??????????')


if __name__ == '__main__':
    unittest.main()
