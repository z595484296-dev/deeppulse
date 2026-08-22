import unittest
from datetime import datetime, timezone, timedelta
import os
import tempfile
from unittest.mock import patch

import server
from research_workflow import (create_workflow, mutate_workflow, preview_workflow,
                               record_run, workflow_snapshot)


BJC = timezone(timedelta(hours=8))
NOW = datetime(2026, 8, 21, 10, 0, tzinfo=BJC)


def draft(**patch):
    value = {
        'kind': 'one_off',
        'title': '工业富联热点验证',
        'target': {'type': 'stock', 'code': '601138', 'name': '工业富联'},
        'question': '近期算力热点是否获得公告、行情与宏观背景共同支持？',
        'sources': ['official_disclosures', 'market_quote', 'akshare_macro'],
        'reviewDays': 5,
        'outputs': ['dashboard_card', 'deepseek_brief'],
        'reminderEnabled': True,
    }
    value.update(patch)
    return value


class ResearchWorkflowTests(unittest.TestCase):
    def test_preview_is_deterministic_and_non_mutating(self):
        source = draft()
        first = preview_workflow(source, now=NOW)
        second = preview_workflow(source, now=NOW)
        self.assertEqual(first['previewId'], second['previewId'])
        self.assertTrue(first['ready'])
        self.assertEqual(source['target']['code'], '601138')
        self.assertFalse(first['contract']['automaticExternalAuthorization'])
        self.assertEqual(len(first['permissions']), 4)

    def test_preview_rejects_incomplete_contract(self):
        result = preview_workflow(draft(target={'type': 'stock', 'code': '11'},
                                        question='', sources=[], outputs=[]), now=NOW)
        self.assertFalse(result['ready'])
        self.assertEqual({row['field'] for row in result['blockers']},
                         {'target.code', 'question', 'sources', 'outputs'})

    def test_creation_requires_every_permission_and_explicit_create(self):
        preview = preview_workflow(draft(), now=NOW)
        with self.assertRaisesRegex(ValueError, '仍需确认权限'):
            create_workflow(preview, ['confirm:create'], NOW)
        permissions = [row['id'] for row in preview['permissions']]
        with self.assertRaisesRegex(ValueError, '明确确认创建'):
            create_workflow(preview, permissions, NOW)
        item = create_workflow(preview, permissions + ['confirm:create'], NOW)
        self.assertEqual(item['status'], 'active')
        self.assertEqual(item['dueAt'], '2026-08-28T15:30:00+08:00')
        self.assertFalse(item['contract']['automaticTradingAction'])

    def test_template_has_no_due_date(self):
        preview = preview_workflow(draft(kind='template', reminderEnabled=False), now=NOW)
        confirmations = [row['id'] for row in preview['permissions']] + ['confirm:create']
        item = create_workflow(preview, confirmations, NOW)
        self.assertEqual(item['status'], 'template')
        self.assertIsNone(item['dueAt'])

    def test_pause_resume_complete_are_state_checked(self):
        preview = preview_workflow(draft(reminderEnabled=False), now=NOW)
        confirmations = [row['id'] for row in preview['permissions']] + ['confirm:create']
        item = create_workflow(preview, confirmations, NOW)
        paused = mutate_workflow(item, 'pause', NOW)
        self.assertEqual(paused['status'], 'paused')
        resumed = mutate_workflow(paused, 'resume', NOW)
        self.assertEqual(resumed['status'], 'active')
        completed = mutate_workflow(resumed, 'complete', NOW)
        self.assertEqual(completed['status'], 'completed')
        with self.assertRaises(ValueError):
            mutate_workflow(completed, 'resume', NOW)

    def test_run_is_bounded_and_does_not_invent_conclusion(self):
        preview = preview_workflow(draft(reminderEnabled=False), now=NOW)
        confirmations = [row['id'] for row in preview['permissions']] + ['confirm:create']
        item = create_workflow(preview, confirmations, NOW)
        updated, run = record_run(item, [
            {'sourceId': 'official_disclosures', 'status': 'ok', 'summary': '2 条公告',
             'upstream': '巨潮资讯', 'evidence': [{'title': '公告 A'}]},
            {'sourceId': 'not_selected', 'status': 'ok', 'summary': '不应进入'},
        ], NOW)
        self.assertEqual(len(run['results']), 1)
        self.assertFalse(run['automaticConclusion'])
        self.assertEqual(updated['lastRunAt'], '2026-08-21T10:00:00+08:00')

    def test_snapshot_derives_review_due_without_mutating_status(self):
        preview = preview_workflow(draft(reviewDays=1, reminderEnabled=False), now=NOW)
        confirmations = [row['id'] for row in preview['permissions']] + ['confirm:create']
        item = create_workflow(preview, confirmations, NOW)
        later = datetime(2026, 8, 24, 16, 0, tzinfo=BJC)
        snapshot = workflow_snapshot([item], later)
        self.assertEqual(snapshot['items'][0]['status'], 'active')
        self.assertEqual(snapshot['items'][0]['effectiveStatus'], 'review_due')


class ResearchWorkflowServerTests(unittest.TestCase):
    def test_preview_and_confirm_persist_one_workflow(self):
        environment = {
            source_id: {'status': 'available', 'available': True, 'detail': ''}
            for source_id in ('official_disclosures', 'market_quote', 'tdx_local',
                              'akshare_macro', 'event_news')
        }
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(server, 'PROFILE_FILE', os.path.join(folder, 'profile.json')), \
                patch.object(server, 'research_workflow_environment', return_value=environment), \
                patch.object(server, 'now_bj', return_value=NOW):
            preview = server.mutate_research_workflow('preview', {'draft': draft()})['preview']
            confirmations = [row['id'] for row in preview['permissions']] + ['confirm:create']
            result = server.mutate_research_workflow('confirm', {
                'draft': draft(), 'previewId': preview['previewId'],
                'confirmations': confirmations,
            })
            self.assertEqual(result['workflows']['summary']['total'], 1)
            self.assertEqual(server.load_profile()['data']['research_workflows'][0]['status'], 'active')

    def test_stale_preview_cannot_be_confirmed(self):
        with patch.object(server, 'research_workflow_environment', return_value={}):
            with self.assertRaisesRegex(ValueError, '重新预览'):
                server.mutate_research_workflow('confirm', {
                    'draft': draft(), 'previewId': 'workflow-preview:stale',
                    'confirmations': ['confirm:create'],
                })

    def test_source_failures_are_isolated(self):
        item = {'target': {'code': '601138'},
                'sources': ['official_disclosures', 'market_quote']}
        with patch.object(server, 'cninfo_disclosures', side_effect=RuntimeError('offline')), \
                patch.object(server, 'quote_with_fallback', return_value={
                    'code': '601138', 'name': '工业富联', 'price': 50,
                    'prev_close': 49, 'source': 'tq'}):
            rows = server.collect_research_workflow_sources(item)
        self.assertEqual([row['status'] for row in rows], ['unavailable', 'ok'])
        self.assertIn('offline', rows[0]['error'])

    def test_due_reminder_requires_explicit_reminder_flag_and_deduplicates(self):
        preview = preview_workflow(draft(reviewDays=1), now=NOW)
        confirmations = [row['id'] for row in preview['permissions']] + ['confirm:create']
        item = create_workflow(preview, confirmations, NOW)
        later = datetime(2026, 8, 24, 16, 0, tzinfo=BJC)
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(server, 'PROFILE_FILE', os.path.join(folder, 'profile.json')):
            with server._profile_lock:
                server._write_profile_unlocked({
                    'schema': 1, 'revision': 0, 'updated_at': None,
                    'data': {'research_workflows': [item]},
                })
            self.assertEqual(server.publish_due_research_workflow_reminders(later), 1)
            self.assertEqual(server.publish_due_research_workflow_reminders(later), 0)
            profile = server.load_profile()['data']
            self.assertEqual(len(profile['attention_inbox']), 1)
            self.assertEqual(profile['attention_inbox'][0]['workflowId'], item['id'])


if __name__ == '__main__':
    unittest.main()
