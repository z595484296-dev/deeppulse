import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

import server


class ServicePlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.profile = os.path.join(self.temp.name, 'profile.json')
        self.profile_patch = patch.object(server, 'PROFILE_FILE', self.profile)
        self.profile_patch.start()

    def tearDown(self):
        self.profile_patch.stop()
        self.temp.cleanup()

    def test_preview_is_non_mutating_and_explainable(self):
        before = server.load_profile()
        plan = server.parse_service_intent(
            '盘前提醒我准备，盘中只报重要变化，晚上 22:30 到 8:00 别打扰，也关注工业富联')
        self.assertEqual(server.load_profile(), before)
        self.assertTrue(plan['requires_confirmation'])
        self.assertTrue(plan['draft']['marketRoutine']['tasks']['pre_market'])
        self.assertTrue(plan['draft']['marketRoutine']['tasks']['intraday'])
        self.assertEqual(plan['draft']['attentionPreferences']['mode'], 'high_only')
        self.assertEqual(plan['draft']['attentionPreferences']['quietStart'], '22:30')
        self.assertEqual(plan['draft']['attentionPreferences']['quietEnd'], '08:00')
        self.assertIn('不会自动修改自选股', plan['unresolved'][0])

    def test_negation_wins_over_task_mention(self):
        plan = server.parse_service_intent('关闭盘前，收盘后提醒我复盘')
        tasks = plan['draft']['marketRoutine']['tasks']
        self.assertFalse(tasks['pre_market'])
        self.assertTrue(tasks['close_review'])

    def test_apply_requires_confirmation_and_preserves_unrelated_preferences(self):
        server.save_profile({'attention_preferences': {
            'desktopSystemEnabled': True, 'epaperDeliveryEnabled': True, 'kindControls': {'news': {'delivery': 'digest'}},
        }})
        plan = server.parse_service_intent('盘前提醒，晚上别打扰')
        with self.assertRaises(ValueError):
            server.apply_service_plan_draft(plan['draft'])
        saved = server.apply_service_plan_draft(plan['draft'], confirmed=True)
        prefs = saved['data']['attention_preferences']
        self.assertTrue(prefs['desktopSystemEnabled'])
        self.assertTrue(prefs['epaperDeliveryEnabled'])
        self.assertIn('news', prefs['kindControls'])
        self.assertTrue(saved['data']['market_routine']['tasks']['pre_market'])

    def test_pause_resume_and_single_skip_are_reversible(self):
        server.save_routine_config({'tasks': {'pre_market': True, 'intraday': True}})
        now = datetime(2026, 8, 24, 8, 10, tzinfo=server.BJC)
        server.mutate_routine_action('skip_next', now)
        status = server.market_routine_status(now)
        self.assertEqual(status['timeline'][0]['state'], 'skipped')
        self.assertEqual(status['next_service']['kind'], 'intraday')
        server.mutate_routine_action('pause_until_morning', now)
        self.assertEqual(server.market_routine_status(now)['runtime']['state'], 'paused')
        server.mutate_routine_action('resume', now)
        self.assertNotEqual(server.market_routine_status(now)['runtime']['state'], 'paused')


if __name__ == '__main__':
    unittest.main()
