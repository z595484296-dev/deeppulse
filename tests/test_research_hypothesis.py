import json
import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import research_hypothesis
import server


BJC = timezone(timedelta(hours=8))


def event_item():
    return {
        'event': {
            'id': 'event-ai-1', 'type': 'headline', 'title': '算力基础设施大会召开',
            'observedAt': '2026-08-21T10:00:00+08:00',
            'sources': [{'id': 'source-1', 'name': '公开来源', 'tier': 'market'}],
        },
        'sectors': ['通信设备', '消费电子'],
        'watchlist': [{'code': '601138', 'name': '工业富联', 'basis': '行业重合'}],
        'quality': {'score': 75, 'corroborated': False,
                    'meaning': '字段完整性，不代表预测准确率'},
    }


class ResearchHypothesisModelTests(unittest.TestCase):
    def test_create_preregisters_window_and_falsifiers_without_direction(self):
        created = research_hypothesis.create_hypothesis(
            event_item(), 3, '只观察，不追涨', datetime(2026, 8, 21, 10, tzinfo=BJC))
        self.assertEqual(created['reviewDueAt'], '2026-08-26T15:30:00+08:00')
        self.assertEqual(created['baseline']['watchlist'][0]['code'], '601138')
        self.assertGreaterEqual(len(created['falsifiers']), 3)
        self.assertFalse(created['contract']['causalClaim'])
        self.assertFalse(created['contract']['directionPrediction'])
        self.assertTrue(created['contract']['userReviewRequired'])

    def test_effective_status_and_user_review_are_explicit(self):
        created = research_hypothesis.create_hypothesis(
            event_item(), 1, now=datetime(2026, 8, 21, 10, tzinfo=BJC))
        self.assertEqual(research_hypothesis.effective_status(
            created, datetime(2026, 8, 24, 16, tzinfo=BJC)), 'review_due')
        reviewed = research_hypothesis.review_hypothesis(
            created, 'mixed', '行业有反馈，但个股未确认', datetime(2026, 8, 24, 17, tzinfo=BJC))
        self.assertEqual(reviewed['status'], 'completed')
        self.assertTrue(reviewed['review']['userConfirmed'])


class ResearchHypothesisServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.profile_file = os.path.join(self.tmp.name, 'profile.json')
        with open(self.profile_file, 'w', encoding='utf-8') as handle:
            json.dump({'schema': 1, 'revision': 0, 'data': {}}, handle)
        self.profile_patch = patch.object(server, 'PROFILE_FILE', self.profile_file)
        self.profile_patch.start()

    def tearDown(self):
        self.profile_patch.stop()
        self.tmp.cleanup()

    def test_create_is_idempotent_for_same_open_event(self):
        with patch.object(server, 'now_bj', return_value=datetime(2026, 8, 21, 10, tzinfo=BJC)):
            first = server.mutate_research_hypothesis('create', {
                'eventItem': event_item(), 'horizonDays': 3,
            })
            second = server.mutate_research_hypothesis('create', {
                'eventItem': event_item(), 'horizonDays': 5,
            })
        self.assertTrue(first['created'])
        self.assertFalse(second['created'])
        self.assertEqual(first['item']['id'], second['item']['id'])
        self.assertEqual(len(server.load_profile()['data']['research_hypotheses']), 1)

    def test_due_reminder_is_local_and_idempotent(self):
        with patch.object(server, 'now_bj', return_value=datetime(2026, 8, 21, 10, tzinfo=BJC)):
            server.mutate_research_hypothesis('create', {
                'eventItem': event_item(), 'horizonDays': 1,
            })
        due = datetime(2026, 8, 24, 16, tzinfo=BJC)
        self.assertEqual(server.publish_due_hypothesis_reminders(due), 1)
        self.assertEqual(server.publish_due_hypothesis_reminders(due), 0)
        profile = server.load_profile()['data']
        self.assertEqual(profile['attention_inbox'][0]['kind'], 'hypothesis_review')
        self.assertEqual(len(profile['hypothesis_receipts']), 1)

    def test_review_requires_supported_outcome(self):
        with patch.object(server, 'now_bj', return_value=datetime(2026, 8, 21, 10, tzinfo=BJC)):
            created = server.mutate_research_hypothesis('create', {
                'eventItem': event_item(), 'horizonDays': 1,
            })['item']
            with self.assertRaises(ValueError):
                server.mutate_research_hypothesis('review', {
                    'id': created['id'], 'outcome': 'profitable',
                })
            result = server.mutate_research_hypothesis('review', {
                'id': created['id'], 'outcome': 'not_supported', 'note': '没有结构反馈',
            })
        self.assertEqual(result['item']['review']['outcome'], 'not_supported')

    def test_evidence_refresh_persists_candidates_without_reviewing(self):
        created_at = datetime(2026, 8, 21, 10, tzinfo=BJC)
        with patch.object(server, 'now_bj', return_value=created_at):
            created = server.mutate_research_hypothesis('create', {
                'eventItem': event_item(), 'horizonDays': 3,
            })['item']
        loaders = {
            'quote_loader': lambda code: {'code': code, 'name': '工业富联', 'price': 60, 'source': 'tdx_local'},
            'benchmark_loader': lambda: [{'code': '000001', 'name': '上证指数', 'price': 3900}],
            'disclosure_loader': lambda code: {'items': [], 'source': {'id': 'cninfo', 'name': '巨潮资讯'}},
        }
        first = server.refresh_hypothesis_evidence(created['id'], created_at, **loaders)
        self.assertEqual(first['checked'], 1)
        self.assertEqual(first['hypotheses']['items'][0]['status'], 'observing')
        loaders['quote_loader'] = lambda code: {'code': code, 'name': '工业富联', 'price': 63, 'source': 'tdx_local'}
        loaders['benchmark_loader'] = lambda: [{'code': '000001', 'name': '上证指数', 'price': 3939}]
        second = server.refresh_hypothesis_evidence(
            created['id'], datetime(2026, 8, 24, 10, tzinfo=BJC), **loaders)
        item = second['hypotheses']['items'][0]
        self.assertIsNone(item.get('review'))
        self.assertGreaterEqual(len(item['evidenceCandidates']), 2)
        self.assertFalse(second['automaticConclusion'])


if __name__ == '__main__':
    unittest.main()
