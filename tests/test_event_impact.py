import json
import os
import tempfile
import unittest
from datetime import datetime

import event_impact
import server


CALENDAR = [{
    '时间': '2026-08-22 10:00:00', '地区': '中国',
    '事件': '中国8月制造业PMI', '重要性': 3,
    '今值': 51.2, '预期': 50.5, '前值': 49.8,
    '链接': 'https://example.test/calendar/pmi',
}]
CORROBORATION = [{
    '时间': '10:00', '地区': '中国',
    '事件': '中国8月官方制造业PMI', '重要性': 3,
    '公布': 51.2, '预期': 50.5, '前值': 49.8,
}]
NEWS = [{
    'time': '09:30:00', 'title': 'AI算力中心建设提速，服务器需求受到关注',
    'url': 'https://example.test/news/ai', 'source_name': '市场快讯', 'source_tier': 'market',
}]
WATCH = [{'code': '601138', 'name': '工业富联', 'group': 'AI硬件'}]
CATALOG = [{'code': '601138', 'name': '工业富联', 'industry': '消费电子'}]


class EventImpactModelTests(unittest.TestCase):
    def test_macro_sources_are_corroborated_and_quality_is_explained(self):
        result = event_impact.build_event_impact(
            CALENDAR, CORROBORATION, [], WATCH, CATALOG,
            data_date='2026-08-22', observed_at='2026-08-22T10:05:00+08:00')
        macro = next(item for item in result['items'] if item['event']['type'] == 'macro')
        self.assertEqual(macro['quality']['sourceCount'], 2)
        self.assertTrue(macro['quality']['corroborated'])
        self.assertIn('不代表方向预测准确率', macro['quality']['meaning'])
        self.assertFalse(macro['contract']['causalClaim'])

    def test_ai_event_links_industry_and_watchlist_without_causal_claim(self):
        result = event_impact.build_event_impact(
            [], [], NEWS, WATCH, CATALOG,
            data_date='2026-08-22', observed_at='2026-08-22T10:05:00+08:00')
        item = result['items'][0]
        self.assertIn('消费电子', item['sectors'])
        self.assertEqual(item['watchlist'][0]['code'], '601138')
        self.assertEqual(item['watchlist'][0]['match'], 'sector')
        self.assertTrue(all(rule['causal'] is False for rule in item['rules']))
        self.assertIn('不是因果', item['explanation'])

    def test_direct_company_mention_has_traceable_basis(self):
        direct_news = [dict(NEWS[0], title='工业富联披露新一代AI服务器进展')]
        result = event_impact.build_event_impact(
            [], [], direct_news, WATCH, CATALOG,
            data_date='2026-08-22', observed_at='2026-08-22T10:05:00+08:00')
        match = result['items'][0]['watchlist'][0]
        self.assertEqual(match['match'], 'direct')
        self.assertIn('直接出现', match['basis'])


class EventServiceTests(unittest.TestCase):
    def test_disabled_service_does_not_call_any_loader(self):
        called = []

        def forbidden(*_args):
            called.append(True)
            raise AssertionError('loader must not run without consent')

        snapshot = server.collect_event_impact(
            current=datetime(2026, 8, 22, 10, 0, tzinfo=server.BJC),
            profile_data={}, macro_loader=forbidden, news_loader=forbidden, stock_loader=forbidden)
        self.assertFalse(snapshot['enabled'])
        self.assertEqual(snapshot['state'], 'disabled')
        self.assertEqual(called, [])

    def test_enabled_service_builds_four_layer_snapshot(self):
        profile = {
            'event_service': {'enabled': True, 'scopes': {'macro': True, 'market_news': True}},
            'watchlist': WATCH,
        }
        snapshot = server.collect_event_impact(
            current=datetime(2026, 8, 22, 10, 0, tzinfo=server.BJC), profile_data=profile,
            macro_loader=lambda _day: {'calendar': CALENDAR, 'corroboration': CORROBORATION, 'errors': []},
            news_loader=lambda: NEWS, stock_loader=lambda: CATALOG)
        self.assertTrue(snapshot['authorization']['granted'])
        self.assertEqual(snapshot['state'], 'ok')
        self.assertGreater(snapshot['impact']['summary']['linkedEvents'], 0)
        self.assertEqual(snapshot['impact']['method']['relation'], 'rule-based-sensitivity')
        self.assertFalse(snapshot['impact']['method']['causal'])

    def test_background_delivery_is_idempotent(self):
        old_profile = server.PROFILE_FILE
        with tempfile.TemporaryDirectory() as tmp:
            try:
                server.PROFILE_FILE = os.path.join(tmp, 'profile.json')
                with open(server.PROFILE_FILE, 'w', encoding='utf-8') as handle:
                    json.dump({'schema': 1, 'revision': 0, 'data': {
                        'event_service': {'enabled': True, 'delivery': 'digest'},
                    }}, handle)
                snapshot = server.collect_event_impact(
                    current=datetime(2026, 8, 22, 10, 0, tzinfo=server.BJC),
                    profile_data={'event_service': {'enabled': True}, 'watchlist': WATCH},
                    macro_loader=lambda _day: {'calendar': [], 'corroboration': [], 'errors': []},
                    news_loader=lambda: NEWS, stock_loader=lambda: CATALOG)
                self.assertEqual(server.commit_event_attention(snapshot), 1)
                self.assertEqual(server.commit_event_attention(snapshot), 0)
                profile = server.load_profile()['data']
                self.assertEqual(len(profile['event_receipts']), 1)
                self.assertEqual(profile['attention_inbox'][0]['kind'], 'event')
                self.assertFalse(profile['attention_inbox'][0]['eventImpact']['causal'])
            finally:
                server.PROFILE_FILE = old_profile


if __name__ == '__main__':
    unittest.main()
