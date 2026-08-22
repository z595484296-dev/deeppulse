import json
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import server
from ai_research_duty import (confirm_delegation, create_job, eligible_trigger,
                              parse_draft, preview_delegation, provider_status)
from research_watch import confirm_watch, preview_watch
from research_workflow import create_workflow, preview_workflow, record_run


BJC = timezone(timedelta(hours=8))
NOW = datetime(2026, 8, 24, 10, 0, tzinfo=BJC)


def watched_with_baseline():
    draft = {
        'kind': 'one_off', 'title': '工业富联公告验证',
        'target': {'type': 'stock', 'code': '601138', 'name': '工业富联'},
        'question': '新增官方披露是否改变当前研究所需核对的事实？',
        'sources': ['official_disclosures'], 'reviewDays': 5,
        'outputs': ['dashboard_card', 'deepseek_brief'], 'reminderEnabled': False,
    }
    workflow_preview = preview_workflow(draft, now=NOW)
    workflow = create_workflow(workflow_preview,
                               [row['id'] for row in workflow_preview['permissions']] + ['confirm:create'], NOW)
    watch_preview = preview_watch(workflow, {
        'frequency': 'daily', 'delivery': 'center_only',
        'expiresAt': '2026-08-30T23:59:00+08:00'}, NOW)
    watched = confirm_watch(workflow, watch_preview,
                            [row['id'] for row in watch_preview['permissions']] + ['confirm:watch'], NOW)
    watched, _ = record_run(watched, [{
        'sourceId': 'official_disclosures', 'status': 'ok', 'upstream': '巨潮资讯',
        'summary': '公告基线', 'evidence': [{'id': 'a', 'title': '公告 A', 'date': '2026-08-21'}],
    }], NOW)
    return watched


class AiResearchDutyRulesTests(unittest.TestCase):
    def test_preview_is_separate_bounded_and_requires_provider(self):
        workflow = watched_with_baseline()
        blocked = preview_delegation(workflow, {}, provider_status({}), NOW)
        self.assertFalse(blocked['ready'])
        preview = preview_delegation(workflow, {'maxRunsPerDay': 3, 'maxTokensPerRun': 2400,
                                                'expiresAt': '2026-08-29T23:59:00+08:00'},
                                     provider_status({'deepseek_api_key': 'configured'}), NOW)
        self.assertTrue(preview['ready'])
        self.assertEqual(preview['maxRunsPerDay'], 1)
        self.assertEqual(preview['maxTokensPerRun'], 900)
        self.assertFalse(preview['contract']['draftIsEvidence'])
        with self.assertRaisesRegex(ValueError, '仍需确认 AI 值班权限'):
            confirm_delegation(workflow, preview, ['confirm:ai-duty'], NOW)

    def test_only_added_official_evidence_is_eligible(self):
        self.assertIsNone(eligible_trigger({'changed': True, 'fingerprint': 'x', 'changes': [
            {'sourceId': 'official_disclosures', 'kind': 'source_status', 'previous': 'ok', 'current': 'down'}]}))
        self.assertIsNone(eligible_trigger({'changed': True, 'fingerprint': 'x', 'changes': [
            {'sourceId': 'market_quote', 'kind': 'evidence_set', 'previousCount': 0, 'currentCount': 1}]}))
        trigger = eligible_trigger({'changed': True, 'fingerprint': 'x', 'changes': [
            {'sourceId': 'official_disclosures', 'kind': 'evidence_set',
             'previousCount': 1, 'currentCount': 2}]})
        self.assertEqual(trigger['kinds'], ['new_official_evidence'])

    def test_job_is_idempotent_and_draft_parser_rejects_actions_shape(self):
        workflow = watched_with_baseline()
        preview = preview_delegation(workflow, {'expiresAt': '2026-08-29T23:59:00+08:00'},
                                     provider_status({'deepseek_api_key': 'configured'}), NOW)
        delegation = confirm_delegation(
            workflow, preview, [row['id'] for row in preview['permissions']] + ['confirm:ai-duty'], NOW)
        run = workflow['runs'][-1]
        change = {'changed': True, 'fingerprint': 'change-1', 'changes': [{
            'sourceId': 'official_disclosures', 'kind': 'evidence_set',
            'previousCount': 0, 'currentCount': 1}]}
        job, reason = create_job(delegation, workflow, run, change, [], NOW)
        self.assertEqual(reason, 'queued')
        duplicate, duplicate_reason = create_job(delegation, workflow, run, change, [job], NOW)
        self.assertIsNone(duplicate)
        self.assertEqual(duplicate_reason, 'duplicate')
        with self.assertRaises(ValueError):
            parse_draft('{"actions":[{"type":"watch_add"}]}')


class FakeDeepSeekHandler(BaseHTTPRequestHandler):
    authorization = ''
    requests = 0

    def log_message(self, *_args):
        pass

    def do_POST(self):
        type(self).requests += 1
        type(self).authorization = self.headers.get('Authorization') or ''
        length = int(self.headers.get('Content-Length') or 0)
        payload = json.loads(self.rfile.read(length).decode('utf-8'))
        assert payload.get('response_format') == {'type': 'json_object'}
        content = json.dumps({
            'summary': '公告集合出现新增项，需要核对原文与既有判断的关系。',
            'facts': ['冻结证据中出现公告 B。'],
            'inferences': ['这可能带来新的核对方向，但尚不能判断影响。'],
            'gaps': ['尚未阅读公告全文。'], 'falsifiers': ['公告内容与研究问题无关。'],
            'citations': ['official_disclosures / 公告 B', '伪造来源'],
        }, ensure_ascii=False)
        body = json.dumps({'choices': [{'message': {'content': content}}],
                           'usage': {'prompt_tokens': 120, 'completion_tokens': 80, 'total_tokens': 200}}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class AiResearchDutyServerTests(unittest.TestCase):
    def setUp(self):
        FakeDeepSeekHandler.authorization = ''
        FakeDeepSeekHandler.requests = 0

    def test_real_loopback_http_path_queues_and_commits_unverified_draft(self):
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), FakeDeepSeekHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                profile_file = os.path.join(folder, 'profile.json')
                config_file = os.path.join(folder, 'config.json')
                secret = 'test-secret-never-persist'
                with open(config_file, 'w', encoding='utf-8') as handle:
                    json.dump({'deepseek_api_key': secret, 'deepseek_model': 'deepseek-chat',
                               'deepseek_base_url': 'http://127.0.0.1:%d' % httpd.server_port}, handle)
                workflow = watched_with_baseline()
                with patch.object(server, 'PROFILE_FILE', profile_file), \
                     patch.object(server, 'CONFIG_FILE', config_file), \
                     patch.object(server, 'now_bj', return_value=NOW):
                    server.save_profile({'research_workflows': [workflow]})
                    options = {'expiresAt': '2026-08-29T23:59:00+08:00'}
                    preview = server.mutate_research_workflow('ai_duty_preview', {
                        'workflowId': workflow['id'], 'options': options})['preview']
                    server.mutate_research_workflow('ai_duty_confirm', {
                        'workflowId': workflow['id'], 'options': options, 'previewId': preview['previewId'],
                        'expectedRevision': preview['profileRevision'],
                        'confirmations': [row['id'] for row in preview['permissions']] + ['confirm:ai-duty']})
                    def collector(_item):
                        return [{'sourceId': 'official_disclosures', 'status': 'ok', 'upstream': '巨潮资讯',
                                 'summary': '新增公告', 'evidence': [
                                     {'id': 'b', 'title': '公告 B', 'date': '2026-08-24'},
                                     {'id': 'a', 'title': '公告 A', 'date': '2026-08-21'}]}]
                    watch_result = server.process_research_watches_once(
                        NOW + timedelta(minutes=1), collector=collector,
                        workflow_id=workflow['id'], force=True)
                    job_result = server.process_ai_research_jobs_once(NOW + timedelta(minutes=2))
                    data = server.load_profile()['data']
                    with open(profile_file, 'r', encoding='utf-8') as handle:
                        stored_text = handle.read()
                self.assertEqual(watch_result['published'], 1)
                self.assertEqual(job_result['state'], 'completed_draft')
                self.assertEqual(FakeDeepSeekHandler.requests, 1)
                self.assertEqual(FakeDeepSeekHandler.authorization, 'Bearer ' + secret)
                self.assertNotIn(secret, stored_text)
                self.assertNotIn('chat_action_plans', data)
                job = data['ai_research_jobs'][0]
                self.assertTrue(job['notEvidence'])
                self.assertEqual(job['usage']['total_tokens'], 200)
                self.assertEqual(job['output']['citations'], ['official_disclosures / 公告 B'])
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_revocation_while_provider_runs_discards_response(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(server, 'PROFILE_FILE', os.path.join(folder, 'profile.json')), \
             patch.object(server, 'CONFIG_FILE', os.path.join(folder, 'config.json')), \
             patch.object(server, 'now_bj', return_value=NOW):
            with open(server.CONFIG_FILE, 'w', encoding='utf-8') as handle:
                json.dump({'deepseek_api_key': 'configured'}, handle)
            workflow = watched_with_baseline()
            server.save_profile({'research_workflows': [workflow]})
            options = {'expiresAt': '2026-08-29T23:59:00+08:00'}
            preview = server.mutate_research_workflow('ai_duty_preview', {
                'workflowId': workflow['id'], 'options': options})['preview']
            server.mutate_research_workflow('ai_duty_confirm', {
                'workflowId': workflow['id'], 'options': options, 'previewId': preview['previewId'],
                'expectedRevision': preview['profileRevision'],
                'confirmations': [row['id'] for row in preview['permissions']] + ['confirm:ai-duty']})
            with server._profile_lock:
                current = server._read_profile_unlocked()
                run = workflow['runs'][-1]
                change = {'changed': True, 'fingerprint': 'revoke-change', 'changes': [{
                    'sourceId': 'official_disclosures', 'kind': 'evidence_set',
                    'previousCount': 0, 'currentCount': 1}]}
                server._queue_ai_research_job_unlocked(current, workflow, run, change, NOW)
                server._write_profile_unlocked(current)
            def provider(_messages, _max_tokens):
                server.mutate_research_workflow('ai_duty_revoke', {'workflowId': workflow['id']})
                return {'content': json.dumps({'summary': 'discard me', 'facts': [], 'inferences': [],
                                               'gaps': [], 'falsifiers': [], 'citations': []}),
                        'model': 'fake', 'usage': {}}
            result = server.process_ai_research_jobs_once(NOW, provider=provider)
            job = server.load_profile()['data']['ai_research_jobs'][0]
        self.assertEqual(result['state'], 'discarded_after_revocation')
        self.assertNotIn('output', job)


if __name__ == '__main__':
    unittest.main()
