import json
import os
import tempfile
import unittest
from unittest import mock

import server


NOW = 1787364000000


def inbox_event(item_id, title='AI算力服务器需求增长', code='601138', name='工业富联'):
    return {
        'id': item_id, 'kind': 'event', 'priority': 'medium', 'delivery': 'digest',
        'title': title, 'detail': '命中自选：%s；质量分 75。' % name,
        'reason': '来源 市场快讯，观测时间 2026-08-22T10:00:00+08:00',
        'page': 'overview', 'createdAt': NOW, 'expiresAt': NOW + 8 * 3600 * 1000,
        'eventImpact': {
            'watchlist': [code], 'watchlistLabels': [name], 'matchTypes': ['sector'],
            'sectors': ['消费电子'], 'causal': False,
        },
    }


def event_snapshot(match='sector', code='601138', name='工业富联', event_id='news-1', importance=2):
    return {
        'enabled': True,
        'impact': {
            'dataDate': '2026-08-22',
            'items': [{
                'event': {
                    'id': event_id, 'type': 'headline', 'title': 'AI算力服务器需求增长',
                    'importance': importance, 'observedAt': '2026-08-22T10:00:00+08:00',
                    'sources': [{'name': '市场快讯'}],
                },
                'watchlist': [{'code': code, 'name': name, 'match': match}],
                'sectors': ['消费电子'], 'quality': {'score': 75}, 'rules': [],
            }],
        },
    }


class EventRelevanceTests(unittest.TestCase):
    def setUp(self):
        self.old_profile = server.PROFILE_FILE
        self.tmp = tempfile.TemporaryDirectory()
        server.PROFILE_FILE = os.path.join(self.tmp.name, 'profile.json')
        server._event_relevance_previews.clear()

    def tearDown(self):
        server.PROFILE_FILE = self.old_profile
        server._event_relevance_previews.clear()
        self.tmp.cleanup()

    def write_profile(self, data, revision=0):
        with open(server.PROFILE_FILE, 'w', encoding='utf-8') as handle:
            json.dump({'schema': 1, 'revision': revision, 'data': data}, handle, ensure_ascii=False)

    def test_preview_is_zero_write_and_confirm_is_exact_and_reversible(self):
        self.write_profile({'attention_inbox': [
            inbox_event('event:1'), inbox_event('event:2'),
        ]})
        group = next(row for row in server.attention_triage_status()['groups']
                     if row['type'] == 'cluster')
        before = server.load_profile()
        preview = server.preview_event_relevance(
            group['id'], 'irrelevant', group['target']['fingerprint'])
        self.assertEqual(server.load_profile(), before)
        self.assertEqual(preview['scope']['targetCode'], '601138')
        self.assertEqual(preview['scope']['topicId'], 'ai_compute')
        self.assertEqual(preview['mode'], 'mute_indirect')
        confirmed = server.confirm_event_relevance(
            preview['previewId'], preview['profileRevision'], True)
        data = confirmed['profile']['data']
        self.assertNotIn('event', data['attention_preferences'].get('kindControls', {}))
        control = data['attention_preferences']['relevanceControls'][-1]
        self.assertEqual((control['targetCode'], control['topicId']), ('601138', 'ai_compute'))
        self.assertEqual(control['status'], 'active')
        restored = server.mutate_event_relevance_control(control['id'], 'restore')
        self.assertEqual(restored['control']['status'], 'undone')

    def test_stale_profile_revision_blocks_confirmation(self):
        self.write_profile({'attention_inbox': [inbox_event('event:1')]})
        group = server.attention_triage_status()['groups'][0]
        preview = server.preview_event_relevance(group['id'], 'too_frequent',
                                                 group['target']['fingerprint'])
        server.save_profile({'journal': []})
        with self.assertRaisesRegex(ValueError, '档案已变化'):
            server.confirm_event_relevance(preview['previewId'], preview['profileRevision'], True)

    def test_market_or_multi_target_event_cannot_create_persistent_rule(self):
        item = inbox_event('event:1')
        item['eventImpact']['watchlist'] = ['601138', '601398']
        item['eventImpact']['watchlistLabels'] = ['工业富联', '工商银行']
        self.write_profile({'attention_inbox': [item]})
        group = server.attention_triage_status()['groups'][0]
        self.assertFalse(group['relevanceScope']['eligible'])
        with self.assertRaisesRegex(ValueError, '唯一关注标的'):
            server.preview_event_relevance(group['id'], 'irrelevant', group['target']['fingerprint'])

    def test_mute_indirect_suppresses_only_exact_target_topic_and_keeps_receipt(self):
        control = {
            'id': 'control-1', 'targetCode': '601138', 'targetLabel': '工业富联',
            'topicId': 'ai_compute', 'topicLabel': 'AI 算力', 'mode': 'mute_indirect',
            'status': 'active', 'createdAt': NOW, 'updatedAt': NOW,
            'expiresAt': NOW + 30 * 24 * 3600 * 1000,
            'taxonomyFingerprint': server.TOPIC_TAXONOMY_FINGERPRINT,
        }
        self.write_profile({'event_service': {'enabled': True, 'delivery': 'digest'},
                            'attention_preferences': {'relevanceControls': [control]}})
        with mock.patch('server.time.time', return_value=NOW / 1000):
            self.assertEqual(server.commit_event_attention(event_snapshot()), 0)
            self.assertEqual(server.commit_event_attention(event_snapshot()), 0)
        data = server.load_profile()['data']
        self.assertEqual(data.get('attention_inbox', []), [])
        self.assertEqual(len(data['event_receipts']), 1)
        self.assertEqual(data['event_receipts'][0]['status'], 'suppressed')
        self.assertTrue(data['event_receipts'][0]['originalEvidencePreserved'])

    def test_control_does_not_affect_other_security_or_direct_mention(self):
        control = {
            'id': 'control-1', 'targetCode': '601138', 'targetLabel': '工业富联',
            'topicId': 'ai_compute', 'topicLabel': 'AI 算力', 'mode': 'mute_indirect',
            'status': 'active', 'expiresAt': NOW + 30 * 24 * 3600 * 1000,
            'taxonomyFingerprint': server.TOPIC_TAXONOMY_FINGERPRINT,
        }
        self.write_profile({'event_service': {'enabled': True, 'delivery': 'digest'},
                            'attention_preferences': {'relevanceControls': [control]}})
        with mock.patch('server.time.time', return_value=NOW / 1000):
            self.assertEqual(server.commit_event_attention(
                event_snapshot(code='601398', name='工商银行', event_id='other')), 1)
            self.assertEqual(server.commit_event_attention(
                event_snapshot(match='direct', event_id='direct')), 1)
        inbox = server.load_profile()['data']['attention_inbox']
        self.assertEqual(len(inbox), 2)
        self.assertTrue(any(row['eventImpact']['watchlist'] == ['601398'] for row in inbox))
        self.assertTrue(any(row['eventImpact']['matchTypes'] == ['direct'] for row in inbox))

    def test_center_only_applies_to_indirect_exact_scope_but_never_direct(self):
        control = {
            'id': 'control-1', 'targetCode': '601138', 'targetLabel': '工业富联',
            'topicId': 'ai_compute', 'topicLabel': 'AI 算力', 'mode': 'center_only',
            'status': 'active', 'expiresAt': NOW + 7 * 24 * 3600 * 1000,
            'taxonomyFingerprint': server.TOPIC_TAXONOMY_FINGERPRINT,
        }
        self.write_profile({'event_service': {'enabled': True, 'delivery': 'immediate'},
                            'attention_preferences': {'relevanceControls': [control]}})
        with mock.patch('server.time.time', return_value=NOW / 1000):
            server.commit_event_attention(event_snapshot(event_id='indirect'))
            server.commit_event_attention(event_snapshot(match='direct', event_id='direct'))
        by_id = {row['id']: row for row in server.load_profile()['data']['attention_inbox']}
        self.assertEqual(by_id['event:indirect']['delivery'], 'center_only')
        self.assertEqual(by_id['event:direct']['delivery'], 'digest')


if __name__ == '__main__':
    unittest.main()
