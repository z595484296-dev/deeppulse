import unittest
from datetime import datetime, timezone, timedelta
import os
import tempfile
from unittest.mock import patch

import server
from research_workflow import (attach_workflow_lineage, build_evidence_timeline,
                               build_result_card, build_template_spec, compare_runs,
                               create_workflow, mutate_workflow, preview_workflow,
                               record_run, workflow_snapshot)
from research_watch import (confirm_watch, is_due, material_change, preview_watch,
                            watch_state)


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
    def test_watch_requires_separate_persistent_confirmation(self):
        workflow_preview = preview_workflow(draft(reminderEnabled=False), now=NOW)
        workflow = create_workflow(workflow_preview,
                                   [row['id'] for row in workflow_preview['permissions']] + ['confirm:create'], NOW)
        watch_preview = preview_watch(workflow, {
            'frequency': 'close', 'delivery': 'center_only',
            'expiresAt': '2026-08-28T15:30:00+08:00',
        }, NOW)
        self.assertTrue(watch_preview['ready'])
        self.assertTrue(watch_preview['contract']['perWorkflowOptIn'])
        self.assertFalse(watch_preview['contract']['automaticAI'])
        with self.assertRaisesRegex(ValueError, '仍需确认值守权限'):
            confirm_watch(workflow, watch_preview, ['confirm:watch'], NOW)
        confirmations = [row['id'] for row in watch_preview['permissions']] + ['confirm:watch']
        watched = confirm_watch(workflow, watch_preview, confirmations, NOW)
        self.assertEqual(watch_state(watched, NOW), 'active')
        self.assertEqual(watched['watch']['sources'], workflow['sources'])
        self.assertEqual(watched['watch']['delivery'], 'center_only')
        self.assertFalse(is_due(watched, NOW))

    def test_watch_material_change_ignores_ordinary_price_but_detects_new_disclosure(self):
        before = {'results': [
            {'sourceId': 'market_quote', 'status': 'ok', 'evidence': [{'price': 50}]},
            {'sourceId': 'official_disclosures', 'status': 'ok',
             'evidence': [{'id': 'a', 'title': '公告 A', 'date': '2026-08-20'}]},
        ]}
        price_only = {'results': [
            {'sourceId': 'market_quote', 'status': 'ok', 'evidence': [{'price': 52}]},
            {'sourceId': 'official_disclosures', 'status': 'ok',
             'evidence': [{'id': 'a', 'title': '公告 A', 'date': '2026-08-20'}]},
        ]}
        self.assertFalse(material_change(before, price_only)['changed'])
        added = {'results': price_only['results'][:-1] + [{
            'sourceId': 'official_disclosures', 'status': 'ok',
            'evidence': [
                {'id': 'b', 'title': '公告 B', 'date': '2026-08-21'},
                {'id': 'a', 'title': '公告 A', 'date': '2026-08-20'},
            ],
        }]}
        change = material_change(before, added)
        self.assertTrue(change['changed'])
        self.assertEqual(change['changes'][0]['kind'], 'evidence_set')
        self.assertFalse(change['automaticConclusion'])

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
        self.assertFalse(item['templateSpec']['inheritsConclusion'])
        self.assertFalse(item['templateSpec']['inheritsRuns'])
        self.assertIn('{{target.name}}', item['templateSpec']['titleTemplate'])

    def test_template_parameterizes_subject_without_copying_run_state(self):
        spec = build_template_spec(draft(
            kind='template', title='工业富联需求验证',
            question='工业富联 601138 的需求证据是否获得独立来源支持？'))
        self.assertEqual(spec['titleTemplate'], '{{target.name}}需求验证')
        self.assertIn('{{target.code}}', spec['questionTemplate'])
        self.assertFalse(spec['inheritsResultCard'])
        self.assertTrue(spec['requiresFreshPreview'])

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
        self.assertEqual(run['resultCard']['reviewState'], 'waiting_for_user')
        self.assertFalse(run['resultCard']['automaticConclusion'])
        self.assertEqual(updated['lastRunAt'], '2026-08-21T10:00:00+08:00')

    def test_result_card_exposes_gaps_staleness_and_same_upstream(self):
        item = {
            'id': 'workflow:test', 'title': '同源核验', 'question': '是否获得独立证据？',
            'target': {'type': 'stock', 'code': '601138', 'name': '工业富联'},
            'sources': ['market_quote', 'akshare_macro', 'official_disclosures'],
        }
        run = {
            'id': 'workflow-run:test', 'ranAt': NOW.isoformat(),
            'results': [
                {'sourceId': 'market_quote', 'status': 'ok', 'upstream': '东方财富',
                 'summary': '行情已读取', 'evidence': [{'price': 50}]},
                {'sourceId': 'akshare_macro', 'status': 'ok', 'upstream': 'AKShare',
                 'summary': '宏观已读取', 'evidence': [{
                     'metrics': [{'id': 'lpr', 'status': 'stale', 'asOf': '2026-07-20',
                                  'source': {'upstream': '东方财富',
                                             'independentGroup': 'eastmoney'}}],
                 }]},
                {'sourceId': 'official_disclosures', 'status': 'unavailable',
                 'error': 'official source offline', 'evidence': []},
            ],
        }
        card = build_result_card(item, run)
        self.assertEqual(card['summary']['evidenceItems'], 2)
        self.assertEqual(card['summary']['staleItems'], 1)
        self.assertEqual(card['summary']['sameUpstreamGroups'], 1)
        self.assertEqual(card['sameUpstream'][0]['group'], 'eastmoney')
        self.assertEqual(card['gaps'][0]['sourceId'], 'official_disclosures')
        self.assertIn('我的结论：待填写', card['reviewDraft'])
        self.assertFalse(card['automaticTradingAction'])

    def test_run_comparison_reports_collection_changes_without_direction(self):
        item = {
            'id': 'workflow:compare', 'title': '对比', 'question': '证据是否变化？',
            'target': {'type': 'stock', 'code': '601138', 'name': '工业富联'},
            'sources': ['market_quote'], 'outputs': ['dashboard_card'],
        }
        before = {
            'id': 'run:1', 'ranAt': '2026-08-20T15:00:00+08:00',
            'results': [{'sourceId': 'market_quote', 'status': 'ok', 'upstream': 'eastmoney',
                         'evidence': [{'status': 'current'}]}],
        }
        before['resultCard'] = build_result_card(item, before)
        after = {
            'id': 'run:2', 'ranAt': '2026-08-21T15:00:00+08:00',
            'results': [{'sourceId': 'market_quote', 'status': 'unavailable',
                         'error': 'timeout', 'evidence': []}],
        }
        after['resultCard'] = build_result_card(item, after)
        comparison = compare_runs(before, after)
        self.assertEqual(comparison['deltas']['usableSources'], -1)
        self.assertEqual(comparison['deltas']['gapCount'], 1)
        self.assertEqual(comparison['changedSourceCount'], 1)
        self.assertFalse(comparison['automaticConclusion'])
        self.assertNotIn('direction', comparison)

    def test_workflow_lineage_is_server_attached_and_keeps_history_immutable(self):
        root_preview = preview_workflow(draft(), now=NOW)
        root_confirmations = [row['id'] for row in root_preview['permissions']] + ['confirm:create']
        root = create_workflow(root_preview, root_confirmations, NOW)
        root = attach_workflow_lineage(root)
        child_preview = preview_workflow(draft(
            title='工业富联需求证据复核', sources=['official_disclosures', 'market_quote']), now=NOW)
        child_confirmations = [row['id'] for row in child_preview['permissions']] + ['confirm:create']
        child = create_workflow(child_preview, child_confirmations, NOW)
        child = attach_workflow_lineage(child, root, [root], 'copy')
        self.assertEqual(root['lineage']['methodVersion'], 1)
        self.assertEqual(child['lineage']['methodVersion'], 2)
        self.assertEqual(child['lineage']['originWorkflowId'], root['id'])
        self.assertIn('证据来源已变更', child['lineage']['changeSummary'])
        self.assertTrue(child['lineage']['historyImmutable'])
        self.assertFalse(child['lineage']['automaticConclusion'])

    def test_evidence_timeline_preserves_observation_and_data_times(self):
        item = {'id': 'workflow:timeline', 'runs': [
            {'id': 'run:1', 'ranAt': '2026-08-20T15:00:00+08:00', 'results': [{
                'sourceId': 'market_quote', 'status': 'ok', 'fetchedAt': '2026-08-20T15:00:01+08:00',
                'upstream': 'Eastmoney', 'evidence': [{'title': '收盘行情', 'asOf': '2026-08-20', 'status': 'current'}],
            }]},
            {'id': 'run:2', 'ranAt': '2026-08-21T15:00:00+08:00', 'results': [{
                'sourceId': 'market_quote', 'status': 'ok', 'fetchedAt': '2026-08-21T15:00:01+08:00',
                'upstream': 'Eastmoney', 'evidence': [{'title': '收盘行情', 'asOf': '2026-08-21', 'status': 'stale'}],
            }]},
        ]}
        timeline = build_evidence_timeline(item)
        self.assertEqual(timeline['summary']['runs'], 2)
        self.assertEqual(timeline['summary']['items'], 2)
        self.assertEqual(timeline['summary']['staleItems'], 1)
        self.assertEqual(timeline['items'][0]['dataAt'], '2026-08-21')
        self.assertEqual(timeline['items'][1]['observedAt'], '2026-08-20T15:00:00+08:00')
        self.assertTrue(timeline['historyImmutable'])
        self.assertFalse(timeline['automaticConclusion'])

    def test_snapshot_backfills_result_card_for_legacy_run_without_mutating_source(self):
        item = {
            'id': 'workflow:legacy', 'modelVersion': 'research-workflow-v1',
            'title': '旧流程', 'question': '旧问题', 'status': 'active',
            'target': {'type': 'stock', 'code': '601138', 'name': '工业富联'},
            'sources': ['market_quote'], 'outputs': ['dashboard_card'],
            'runs': [{'id': 'run:legacy', 'ranAt': NOW.isoformat(), 'results': [{
                'sourceId': 'market_quote', 'status': 'ok', 'upstream': '腾讯',
                'summary': '行情已读取', 'evidence': [{'price': 50}],
            }]}],
        }
        snapshot = workflow_snapshot([item], NOW)
        self.assertIn('resultCard', snapshot['items'][0]['latestRun'])
        self.assertNotIn('resultCard', item['runs'][0])

    def test_snapshot_derives_review_due_without_mutating_status(self):
        preview = preview_workflow(draft(reviewDays=1, reminderEnabled=False), now=NOW)
        confirmations = [row['id'] for row in preview['permissions']] + ['confirm:create']
        item = create_workflow(preview, confirmations, NOW)
        later = datetime(2026, 8, 24, 16, 0, tzinfo=BJC)
        snapshot = workflow_snapshot([item], later)
        self.assertEqual(snapshot['items'][0]['status'], 'active')
        self.assertEqual(snapshot['items'][0]['effectiveStatus'], 'review_due')


class ResearchWorkflowServerTests(unittest.TestCase):
    def _watched_workflow(self):
        workflow_preview = preview_workflow(draft(
            sources=['official_disclosures'], outputs=['dashboard_card'], reminderEnabled=False), now=NOW)
        workflow = create_workflow(workflow_preview,
                                   [row['id'] for row in workflow_preview['permissions']] + ['confirm:create'], NOW)
        watch_preview = preview_watch(workflow, {
            'frequency': 'daily', 'delivery': 'center_only',
            'expiresAt': '2026-08-28T15:30:00+08:00',
        }, NOW)
        return confirm_watch(workflow, watch_preview,
                             [row['id'] for row in watch_preview['permissions']] + ['confirm:watch'], NOW)

    def test_unwatched_workflow_never_accesses_background_sources(self):
        workflow_preview = preview_workflow(draft(reminderEnabled=False), now=NOW)
        workflow = create_workflow(workflow_preview,
                                   [row['id'] for row in workflow_preview['permissions']] + ['confirm:create'], NOW)
        called = []
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(server, 'PROFILE_FILE', os.path.join(folder, 'profile.json')):
            server.save_profile({'research_workflows': [workflow]})
            result = server.process_research_watches_once(NOW, collector=lambda item: called.append(item))
        self.assertEqual(result['checked'], 0)
        self.assertEqual(called, [])

    def test_watch_preview_and_confirm_are_separate_server_actions(self):
        workflow_preview = preview_workflow(draft(reminderEnabled=False), now=NOW)
        workflow = create_workflow(workflow_preview,
                                   [row['id'] for row in workflow_preview['permissions']] + ['confirm:create'], NOW)
        options = {'frequency': 'close', 'delivery': 'center_only',
                   'expiresAt': '2026-08-28T23:59:00+08:00'}
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(server, 'PROFILE_FILE', os.path.join(folder, 'profile.json')), \
                patch.object(server, 'now_bj', return_value=NOW):
            server.save_profile({'research_workflows': [workflow]})
            preview = server.mutate_research_workflow('watch_preview', {
                'workflowId': workflow['id'], 'options': options,
            })['preview']
            with self.assertRaisesRegex(ValueError, '仍需确认值守权限'):
                server.mutate_research_workflow('watch_confirm', {
                    'workflowId': workflow['id'], 'options': options,
                    'previewId': preview['previewId'], 'confirmations': ['confirm:watch'],
                })
            result = server.mutate_research_workflow('watch_confirm', {
                'workflowId': workflow['id'], 'options': options,
                'previewId': preview['previewId'],
                'confirmations': [row['id'] for row in preview['permissions']] + ['confirm:watch'],
            })
        self.assertEqual(result['workflows']['watchSummary']['active'], 1)
        self.assertEqual(result['updated']['watch']['delivery'], 'center_only')

    def test_watch_baseline_is_silent_and_new_evidence_is_deduplicated(self):
        watched = self._watched_workflow()
        run_time = datetime(2026, 8, 24, 10, 0, tzinfo=BJC)
        batches = [[{'id': 'a', 'title': '公告 A', 'date': '2026-08-21'}],
                   [{'id': 'b', 'title': '公告 B', 'date': '2026-08-24'},
                    {'id': 'a', 'title': '公告 A', 'date': '2026-08-21'}]]
        def collector(_item):
            evidence = batches.pop(0) if batches else [
                {'id': 'b', 'title': '公告 B', 'date': '2026-08-24'},
                {'id': 'a', 'title': '公告 A', 'date': '2026-08-21'}]
            return [{'sourceId': 'official_disclosures', 'status': 'ok',
                     'upstream': '巨潮资讯', 'summary': '公告', 'evidence': evidence}]
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(server, 'PROFILE_FILE', os.path.join(folder, 'profile.json')):
            server.save_profile({'research_workflows': [watched]})
            baseline = server.process_research_watches_once(run_time, collector=collector,
                                                             workflow_id=watched['id'], force=True)
            changed = server.process_research_watches_once(run_time + timedelta(minutes=1), collector=collector,
                                                            workflow_id=watched['id'], force=True)
            repeated = server.process_research_watches_once(run_time + timedelta(minutes=2), collector=collector,
                                                             workflow_id=watched['id'], force=True)
            data = server.load_profile()['data']
        self.assertEqual(baseline['published'], 0)
        self.assertEqual(changed['published'], 1)
        self.assertEqual(repeated['published'], 0)
        self.assertEqual(len(data['attention_inbox']), 1)
        self.assertEqual(data['attention_inbox'][0]['delivery'], 'center_only')
        self.assertEqual(len(data['research_workflows'][0]['runs']), 3)

    def test_restart_does_not_catch_up_a_long_missed_watch(self):
        watched = self._watched_workflow()
        watched['watch']['nextCheckAt'] = '2026-08-21T09:05:00+08:00'
        called = []
        current = datetime(2026, 8, 24, 10, 0, tzinfo=BJC)
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(server, 'PROFILE_FILE', os.path.join(folder, 'profile.json')):
            server.save_profile({'research_workflows': [watched]})
            result = server.process_research_watches_once(current,
                                                           collector=lambda item: called.append(item))
            stored = server.load_profile()['data']['research_workflows'][0]['watch']
        self.assertEqual(result['checked'], 0)
        self.assertEqual(called, [])
        self.assertEqual(stored['lastMissedAt'], '2026-08-21T09:05:00+08:00')
        self.assertGreater(stored['nextCheckAt'], current.isoformat())

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

    def test_confirm_resolves_origin_from_profile_before_attaching_lineage(self):
        environment = {source_id: {'status': 'available', 'available': True, 'detail': ''}
                       for source_id in ('official_disclosures', 'market_quote', 'tdx_local',
                                         'akshare_macro', 'event_news')}
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(server, 'PROFILE_FILE', os.path.join(folder, 'profile.json')), \
                patch.object(server, 'research_workflow_environment', return_value=environment), \
                patch.object(server, 'now_bj', return_value=NOW):
            first_preview = server.mutate_research_workflow('preview', {'draft': draft()})['preview']
            first = server.mutate_research_workflow('confirm', {
                'draft': draft(), 'previewId': first_preview['previewId'],
                'confirmations': [row['id'] for row in first_preview['permissions']] + ['confirm:create'],
            })['created']
            changed = draft(title='工业富联二次核对', sources=['official_disclosures', 'market_quote'])
            second_preview = server.mutate_research_workflow('preview', {'draft': changed})['preview']
            second = server.mutate_research_workflow('confirm', {
                'draft': changed, 'previewId': second_preview['previewId'],
                'confirmations': [row['id'] for row in second_preview['permissions']] + ['confirm:create'],
                'originWorkflowId': first['id'], 'originKind': 'copy',
            })['created']
            self.assertEqual(second['lineage']['originWorkflowId'], first['id'])
            self.assertEqual(second['lineage']['methodVersion'], 2)
            self.assertEqual(second['lineage']['originKind'], 'copy')
            with self.assertRaisesRegex(ValueError, '来源研究流程'):
                server.mutate_research_workflow('confirm', {
                    'draft': changed, 'previewId': second_preview['previewId'],
                    'confirmations': [row['id'] for row in second_preview['permissions']] + ['confirm:create'],
                    'originWorkflowId': 'workflow:missing', 'originKind': 'copy',
                })

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
