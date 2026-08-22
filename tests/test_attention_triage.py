import json
import os
import tempfile
import unittest

import attention_triage
import server


NOW = 1787364000000


def event(item_id, title, created, read_at=None):
    return {
        'id': item_id, 'kind': 'event', 'priority': 'medium', 'delivery': 'digest',
        'title': title, 'detail': '命中自选：工业富联；质量分 75。规则只表示敏感性。',
        'reason': '来源 东方财富快讯，观测时间 2026-08-22T10:00:00+08:00',
        'page': 'overview', 'createdAt': created, 'expiresAt': created + 8 * 3600 * 1000,
        'readAt': read_at,
        'eventImpact': {'watchlist': ['601138'], 'sectors': ['消费电子'], 'causal': False},
    }


class AttentionTriageTests(unittest.TestCase):
    def test_same_target_topic_and_day_are_grouped_without_losing_evidence(self):
        items = [
            event('event:1', 'AI算力服务器需求增长', NOW - 1000),
            event('event:2', '数据中心服务器订单更新', NOW - 2000),
            {'id': 'price:1', 'kind': 'price', 'priority': 'high', 'title': '价格到达',
             'createdAt': NOW - 3000, 'expiresAt': NOW + 10000, 'readAt': None},
        ]
        result = attention_triage.build_attention_triage(items, NOW)
        self.assertEqual(result['rawCount'], 3)
        self.assertEqual(result['groupCount'], 2)
        self.assertEqual(result['unreadGroupCount'], 2)
        cluster = next(row for row in result['groups'] if row['type'] == 'cluster')
        self.assertEqual(cluster['count'], 2)
        self.assertEqual(set(cluster['memberIds']), {'event:1', 'event:2'})
        self.assertEqual(len(cluster['items']), 2)
        self.assertTrue(cluster['traceability']['evidencePreserved'])
        self.assertTrue(any(row['kind'] == 'price' and row['type'] == 'item'
                            for row in result['groups']))

    def test_group_action_marks_every_member_but_keeps_raw_rows(self):
        old_profile = server.PROFILE_FILE
        with tempfile.TemporaryDirectory() as tmp:
            try:
                server.PROFILE_FILE = os.path.join(tmp, 'profile.json')
                with open(server.PROFILE_FILE, 'w', encoding='utf-8') as handle:
                    json.dump({'schema': 1, 'revision': 0, 'data': {
                        'attention_inbox': [event('event:1', 'AI算力服务器需求增长', NOW - 1000),
                                            event('event:2', '数据中心服务器订单更新', NOW - 2000)],
                    }}, handle, ensure_ascii=False)
                snapshot = server.attention_triage_status()
                cluster = next(row for row in snapshot['groups'] if row['type'] == 'cluster')
                result = server.update_attention_triage(cluster['id'], 'mark_read')
                raw = result['profile']['data']['attention_inbox']
                self.assertEqual(len(raw), 2)
                self.assertTrue(all(row.get('readAt') for row in raw))
                self.assertEqual(result['triage']['unreadGroupCount'], 0)
            finally:
                server.PROFILE_FILE = old_profile

    def test_cluster_feedback_is_one_explicit_learning_record(self):
        old_profile = server.PROFILE_FILE
        with tempfile.TemporaryDirectory() as tmp:
            try:
                server.PROFILE_FILE = os.path.join(tmp, 'profile.json')
                with open(server.PROFILE_FILE, 'w', encoding='utf-8') as handle:
                    json.dump({'schema': 1, 'revision': 0, 'data': {
                        'attention_inbox': [event('event:1', 'AI算力服务器需求增长', NOW - 1000),
                                            event('event:2', '数据中心服务器订单更新', NOW - 2000)],
                    }}, handle, ensure_ascii=False)
                cluster = next(row for row in server.attention_triage_status()['groups']
                               if row['type'] == 'cluster')
                result = server.update_attention_triage(cluster['id'], 'feedback', 'too_frequent')
                data = result['profile']['data']
                self.assertEqual(len(data['attention_feedback']), 1)
                self.assertEqual(data['attention_feedback'][0]['memberCount'], 2)
                self.assertEqual(data['attention_preferences']['kindControls']['event']['delivery'], 'digest')
            finally:
                server.PROFILE_FILE = old_profile

    def test_mark_all_read_is_atomic_and_preserves_rows(self):
        old_profile = server.PROFILE_FILE
        with tempfile.TemporaryDirectory() as tmp:
            try:
                server.PROFILE_FILE = os.path.join(tmp, 'profile.json')
                with open(server.PROFILE_FILE, 'w', encoding='utf-8') as handle:
                    json.dump({'schema': 1, 'revision': 0, 'data': {
                        'attention_inbox': [event('event:1', 'AI算力服务器需求增长', NOW - 1000),
                                            {'id': 'price:1', 'kind': 'price', 'createdAt': NOW - 500}],
                    }}, handle, ensure_ascii=False)
                result = server.update_attention_triage(None, 'mark_all_read')
                self.assertEqual(len(result['profile']['data']['attention_inbox']), 2)
                self.assertTrue(all(row.get('readAt') for row in result['profile']['data']['attention_inbox']))
                self.assertEqual(result['triage']['unreadGroupCount'], 0)
            finally:
                server.PROFILE_FILE = old_profile


if __name__ == '__main__':
    unittest.main()
