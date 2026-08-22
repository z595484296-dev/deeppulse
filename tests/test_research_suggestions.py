import unittest
from datetime import datetime, timedelta, timezone
import json
import os
import tempfile
from unittest.mock import patch

import server
from research_suggestions import build_snapshot, draft_fingerprint, mutate_item


BJC = timezone(timedelta(hours=8))
NOW = datetime(2026, 8, 22, 10, 0, tzinfo=BJC)


class ResearchSuggestionTests(unittest.TestCase):
    def setUp(self):
        self.data = {
            'watchlist': [{'code': '601138', 'name': '工业富联', 'note': '关注算力订单', 'added': 1}],
            'research_workflows': [],
        }

    def test_watchlist_candidate_is_safe_draft(self):
        result = build_snapshot(self.data, [], now=NOW)
        self.assertEqual(result['summary']['pending'], 1)
        row = result['visible'][0]
        self.assertEqual(row['proposedDraft']['target']['code'], '601138')
        self.assertEqual(row['proposedDraft']['sources'], ['official_disclosures', 'market_quote'])
        self.assertFalse(row['contract']['automaticWorkflowCreation'])
        self.assertFalse(result['contract']['automaticTradingAction'])
        self.assertEqual(row['journey']['stage'], 'suggested')

    def test_active_workflow_suppresses_watchlist_candidate(self):
        self.data['research_workflows'] = [{
            'id': 'wf:1', 'status': 'active',
            'target': {'type': 'stock', 'code': '601138', 'name': '工业富联'},
        }]
        result = build_snapshot(self.data, [], now=NOW)
        self.assertEqual(result['summary']['pending'], 0)

    def test_due_hypothesis_generates_review_suggestion(self):
        hypotheses = [{
            'id': 'hyp:1', 'effectiveStatus': 'review_due', 'statement': '订单是否持续增长？',
            'baseline': {'watchlist': [{'code': '601138', 'name': '工业富联'}]},
            'evidenceState': {'errors': ['公告数据待补齐']},
        }]
        result = build_snapshot({'watchlist': [], 'research_workflows': []}, hypotheses, now=NOW)
        self.assertEqual(result['visible'][0]['role'], '研究员')
        self.assertIn('公告数据待补齐', result['visible'][0]['evidenceGaps'])

    def test_dismiss_is_preserved_and_expired_candidate_reopens(self):
        first = build_snapshot(self.data, [], now=NOW)
        dismissed = mutate_item(first['items'][0], 'dismiss', NOW)
        second = build_snapshot(self.data, [], [dismissed], NOW + timedelta(days=1))
        self.assertEqual(second['summary']['dismissed'], 1)
        third = build_snapshot(self.data, [], [dismissed], NOW + timedelta(days=8))
        self.assertEqual(third['summary']['pending'], 1)

    def test_fingerprint_is_order_independent(self):
        self.assertEqual(draft_fingerprint({'a': 1, 'b': 2}), draft_fingerprint({'b': 2, 'a': 1}))

    def test_accepted_item_stays_accepted_after_workflow_suppresses_candidate(self):
        first = build_snapshot(self.data, [], now=NOW)
        accepted = mutate_item(first['items'][0], 'accept', NOW, 'workflow:1')
        self.data['research_workflows'] = [{
            'id': 'workflow:1', 'status': 'active',
            'target': {'type': 'stock', 'code': '601138', 'name': '工业富联'},
        }]
        result = build_snapshot(self.data, [], [accepted], NOW + timedelta(minutes=1))
        self.assertEqual(result['summary']['accepted'], 1)
        self.assertEqual(result['items'][0]['workflowId'], 'workflow:1')

    def test_server_atomically_converts_suggestion_when_workflow_is_created(self):
        with tempfile.TemporaryDirectory() as folder:
            profile_file = os.path.join(folder, 'profile.json')
            with open(profile_file, 'w', encoding='utf-8') as stream:
                json.dump({'schema': 1, 'revision': 0, 'updated_at': None,
                           'data': self.data}, stream, ensure_ascii=False)
            with patch.object(server, 'PROFILE_FILE', profile_file):
                suggestion = server.research_suggestions_status()['visible'][0]
                prepared = server.mutate_research_suggestion(
                    'prepare', {'suggestionId': suggestion['id']})
                preview = server.preview_research_workflow(prepared['draft'])
                confirmations = [row['id'] for row in preview['permissions']] + ['confirm:create']
                result = server.mutate_research_workflow('confirm', {
                    'draft': prepared['draft'], 'previewId': preview['previewId'],
                    'confirmations': confirmations, 'suggestionId': suggestion['id'],
                })
            self.assertEqual(result['suggestions']['summary']['accepted'], 1)
            self.assertEqual(result['suggestions']['items'][0]['workflowId'], result['created']['id'])
            self.assertEqual(result['created']['runs'], [])

    def test_prepare_and_preview_are_explicit_persisted_steps_without_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            profile_file = os.path.join(folder, 'profile.json')
            with open(profile_file, 'w', encoding='utf-8') as stream:
                json.dump({'schema': 1, 'revision': 0, 'updated_at': None,
                           'data': self.data}, stream, ensure_ascii=False)
            with patch.object(server, 'PROFILE_FILE', profile_file):
                suggestion = server.research_suggestions_status()['visible'][0]
                prepared = server.mutate_research_suggestion(
                    'prepare', {'suggestionId': suggestion['id']})
                after_prepare = next(row for row in prepared['suggestions']['items']
                                     if row['id'] == suggestion['id'])
                self.assertEqual(after_prepare['journey']['stage'], 'drafted')
                self.assertEqual(server.research_workflows_status()['summary']['total'], 0)

                previewed = server.mutate_research_workflow('preview', {
                    'draft': prepared['draft'], 'suggestionId': suggestion['id'],
                })
                after_preview = next(row for row in previewed['suggestions']['items']
                                     if row['id'] == suggestion['id'])
                self.assertTrue(previewed['preview']['ready'])
                self.assertEqual(after_preview['journey']['stage'], 'previewed')
                self.assertEqual(server.research_workflows_status()['summary']['total'], 0)

    def test_edited_draft_does_not_falsely_advance_original_suggestion(self):
        with tempfile.TemporaryDirectory() as folder:
            profile_file = os.path.join(folder, 'profile.json')
            with open(profile_file, 'w', encoding='utf-8') as stream:
                json.dump({'schema': 1, 'revision': 0, 'updated_at': None,
                           'data': self.data}, stream, ensure_ascii=False)
            with patch.object(server, 'PROFILE_FILE', profile_file):
                suggestion = server.research_suggestions_status()['visible'][0]
                prepared = server.mutate_research_suggestion(
                    'prepare', {'suggestionId': suggestion['id']})
                edited = dict(prepared['draft'], question='我自己改写后的独立研究问题')
                with self.assertRaisesRegex(ValueError, '独立流程'):
                    server.mutate_research_workflow('preview', {
                        'draft': edited, 'suggestionId': suggestion['id'],
                    })
                stored = server.research_suggestions_status()['items'][0]
                self.assertEqual(stored['journey']['stage'], 'drafted')


if __name__ == '__main__':
    unittest.main()
