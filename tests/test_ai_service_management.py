import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import server
from ai_service_management import (build_status, normalize_preferences,
                                   onboarding, preview_preferences,
                                   validate_plan)


BJC = timezone(timedelta(hours=8))
NOW = datetime(2026, 8, 24, 14, 0, tzinfo=BJC)


class AiServiceManagementRulesTests(unittest.TestCase):
    def test_preferences_are_bounded_and_safe_by_default(self):
        self.assertEqual(normalize_preferences()['dailyLimit'], 3)
        self.assertFalse(normalize_preferences()['paused'])
        self.assertEqual(normalize_preferences({'dailyLimit': 99})['dailyLimit'], 3)
        self.assertEqual(normalize_preferences({'dailyLimit': -1})['dailyLimit'], 0)

    def test_onboarding_exposes_only_the_next_explicit_step(self):
        empty = onboarding({'ready': False}, {'items': []})
        self.assertEqual(empty['next']['action'], 'open_provider')
        workflow = {
            'id': 'wf1', 'runs': [{'id': 'run1'}],
            'watch': {'effectiveStatus': 'active'},
            'aiDuty': {'effectiveStatus': 'active'},
            'aiDrafts': [{'status': 'completed_draft', 'reviewStatus': 'evidence_opened'}],
        }
        complete = onboarding({'ready': True}, {'items': [workflow]})
        self.assertTrue(complete['complete'])
        self.assertEqual(complete['completed'], complete['total'])

    def test_onboarding_does_not_combine_permissions_from_different_workflows(self):
        mixed = onboarding({'ready': True}, {'items': [
            {'id': 'wf-baseline', 'runs': [{'id': 'run1'}]},
            {'id': 'wf-duty', 'runs': [],
             'watch': {'effectiveStatus': 'active'},
             'aiDuty': {'effectiveStatus': 'active'},
             'aiDrafts': [{'status': 'dismissed'}]},
        ]})
        self.assertFalse(mixed['complete'])
        self.assertEqual(mixed['next']['label'], '开启研究值守')

    def test_preview_requires_fresh_revision_and_explicit_boundary(self):
        plan = preview_preferences({}, {'paused': True, 'dailyLimit': 1}, 7, NOW)
        self.assertTrue(plan['ready'])
        with self.assertRaisesRegex(ValueError, '档案已变化'):
            validate_plan(plan, plan['planId'], 8,
                          ['ai-service:budget', 'confirm:ai-service'], NOW)
        with self.assertRaisesRegex(ValueError, '预算'):
            validate_plan(plan, plan['planId'], 7, [], NOW)
        value = validate_plan(plan, plan['planId'], 7,
                              ['ai-service:budget', 'confirm:ai-service'], NOW)
        self.assertTrue(value['paused'])
        self.assertEqual(value['dailyLimit'], 1)

    def test_public_status_contains_no_secret_or_automatic_authorization(self):
        status = build_status({'ready': True, 'host': 'api.deepseek.com'},
                              {'items': [], 'aiDutySummary': {'usedToday': 1}},
                              {'dailyLimit': 2}, 5)
        encoded = json.dumps(status, ensure_ascii=False)
        self.assertNotIn('apiKey', encoded)
        self.assertEqual(status['summary']['userDailyLimit'], 2)
        self.assertEqual(status['onboarding']['next']['action'], 'open_workflow')


class AiServiceManagementServerTests(unittest.TestCase):
    def setUp(self):
        with server._ai_service_plan_lock:
            server._ai_service_plans.clear()

    def test_confirm_pause_cancels_queued_job_before_network(self):
        with tempfile.TemporaryDirectory() as folder:
            profile_file = os.path.join(folder, 'profile.json')
            profile = {
                'schema': 1, 'revision': 4, 'updated_at': NOW.isoformat(),
                'data': {'ai_research_jobs': [{
                    'id': 'job1', 'workflowId': 'wf1', 'status': 'queued',
                    'createdAt': NOW.isoformat(timespec='seconds'),
                }]},
            }
            with open(profile_file, 'w', encoding='utf-8') as handle:
                json.dump(profile, handle)
            with patch.object(server, 'PROFILE_FILE', profile_file), \
                 patch.object(server, 'now_bj', return_value=NOW):
                result = server.preview_ai_service_settings({'paused': True, 'dailyLimit': 1})
                plan = result['preview']
                saved = server.confirm_ai_service_settings(
                    plan['planId'], plan['profileRevision'],
                    ['ai-service:budget', 'confirm:ai-service'])
                disk = server.load_profile()
                revision_after_pause = disk['revision']
                idle = server.process_ai_research_jobs_once(
                    NOW, provider=lambda *_: self.fail('已取消任务不应访问模型'))
                disk_after_idle = server.load_profile()
            self.assertTrue(saved['status']['preferences']['paused'])
            self.assertEqual(disk['data']['ai_research_jobs'][0]['status'], 'cancelled')
            self.assertEqual(disk['data']['ai_research_jobs'][0]['errorCode'],
                             'global_ai_paused')
            self.assertEqual(idle['state'], 'idle')
            self.assertEqual(disk_after_idle['revision'], revision_after_pause)


if __name__ == '__main__':
    unittest.main()
