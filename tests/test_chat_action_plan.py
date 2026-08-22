import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import server


NOW = datetime(2026, 8, 24, 10, 0, tzinfo=server.BJC)


class ChatActionPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.profile = os.path.join(self.temp.name, 'profile.json')
        self.profile_patch = patch.object(server, 'PROFILE_FILE', self.profile)
        self.profile_patch.start()
        self.resolver_patch = patch.object(
            server, '_resolve_chat_security',
            side_effect=lambda code, fallback='': {
                '601138': {'code': '601138', 'name': '工业富联'},
                '600519': {'code': '600519', 'name': '贵州茅台'},
            }.get(str(code)),
        )
        self.resolver_patch.start()

    def tearDown(self):
        self.resolver_patch.stop()
        self.profile_patch.stop()
        self.temp.cleanup()

    def preview(self, action):
        return server.preview_chat_actions([action], 'test', NOW)

    def test_untrusted_actions_are_split_into_safe_actions_plans_and_rejections(self):
        result = server.preview_chat_actions([
            {'type': 'nav', 'page': 'strategy', 'confirmed': True},
            {'type': 'watch_add', 'code': '601138', 'name': '伪造名称', 'confirmed': True},
            {'type': 'shell', 'command': 'whoami'},
            {'type': 'nav', 'page': 'https://evil.test'},
        ], 'cloud', NOW)
        self.assertEqual(result['safeActions'], [{'type': 'nav', 'page': 'strategy'}])
        self.assertEqual(len(result['actionPlans']), 1)
        plan = result['actionPlans'][0]
        self.assertEqual(plan['action']['name'], '工业富联')
        self.assertEqual(plan['status'], 'pending')
        self.assertTrue(plan['requiresConfirmation'])
        self.assertFalse(plan['contract']['automaticExecution'])
        self.assertEqual({row['reason'] for row in result['rejectedActions']},
                         {'action_not_allowed', 'page_not_allowed'})
        self.assertEqual(server.load_profile()['data'].get('watchlist'), None)

    def test_add_requires_confirmation_is_idempotent_and_can_be_undone(self):
        plan = self.preview({'type': 'watch_add', 'code': '601138'})['actionPlans'][0]
        self.assertEqual(server.load_profile()['data'].get('watchlist'), None)
        first = server.mutate_chat_action_plan('confirm', plan['id'], NOW)
        second = server.mutate_chat_action_plan('confirm', plan['id'], NOW + timedelta(seconds=1))
        self.assertEqual(first['receipt']['id'], second['receipt']['id'])
        self.assertEqual([row['code'] for row in second['watchlist']], ['601138'])
        self.assertEqual(len(server.load_profile()['data']['chat_action_receipts']), 1)
        undone = server.mutate_chat_action_plan('undo', plan['id'], NOW + timedelta(minutes=1))
        self.assertEqual(undone['plan']['status'], 'undone')
        self.assertEqual(undone['watchlist'], [])

    def test_remove_undo_restores_group_note_and_order(self):
        original = [
            {'code': '600519', 'name': '贵州茅台', 'group': '长期', 'note': '渠道跟踪', 'added': 1},
            {'code': '601138', 'name': '工业富联', 'group': 'AI', 'note': '订单', 'added': 2},
        ]
        server.save_profile({'watchlist': original})
        plan = self.preview({'type': 'watch_remove', 'code': '600519'})['actionPlans'][0]
        executed = server.mutate_chat_action_plan('confirm', plan['id'], NOW)
        self.assertEqual([row['code'] for row in executed['watchlist']], ['601138'])
        undone = server.mutate_chat_action_plan('undo', plan['id'], NOW + timedelta(minutes=2))
        self.assertEqual(undone['watchlist'], original)

    def test_relevant_state_change_makes_old_plan_stale(self):
        plan = self.preview({'type': 'watch_add', 'code': '601138'})['actionPlans'][0]
        server.save_profile({'watchlist': [{'code': '600519', 'name': '贵州茅台'}]})
        with self.assertRaisesRegex(ValueError, '已变化'):
            server.mutate_chat_action_plan('confirm', plan['id'], NOW)
        stored = server.load_profile()['data']['chat_action_plans'][0]
        self.assertEqual(stored['status'], 'stale')

    def test_unrelated_profile_revision_drift_does_not_invalidate_target_scope(self):
        plan = self.preview({'type': 'watch_add', 'code': '601138'})['actionPlans'][0]
        server.save_profile({'chat_history': [{'role': 'user', 'html': '仅更新对话'}]})
        result = server.mutate_chat_action_plan('confirm', plan['id'], NOW)
        self.assertEqual(result['plan']['status'], 'executed')
        self.assertEqual(result['watchlist'][0]['code'], '601138')

    def test_expired_plan_is_rejected(self):
        plan = self.preview({'type': 'watch_add', 'code': '601138'})['actionPlans'][0]
        with self.assertRaisesRegex(ValueError, 'expired'):
            server.mutate_chat_action_plan(
                'confirm', plan['id'], NOW + timedelta(seconds=server.CHAT_ACTION_PLAN_TTL_SECONDS + 1))
        self.assertEqual(server.load_profile()['data']['chat_action_plans'][0]['status'], 'expired')

    def test_snapshot_is_non_reversible_and_idempotent_by_data_date(self):
        history = []

        def assemble(force=False):
            if not any(row.get('date') == '2026-08-22' for row in history):
                history.append({'date': '2026-08-22', 'temp': 55})
            return {'date': '2026-08-22'}

        with patch.object(server, 'load_history', side_effect=lambda: list(history)), \
                patch.object(server, 'assemble_emotion', side_effect=assemble):
            plan = self.preview({'type': 'record'})['actionPlans'][0]
            result = server.mutate_chat_action_plan('confirm', plan['id'], NOW)
            repeated = server.mutate_chat_action_plan('confirm', plan['id'], NOW + timedelta(seconds=1))
        self.assertFalse(result['plan']['reversible'])
        self.assertTrue(result['receipt']['created'])
        self.assertEqual(result['receipt']['id'], repeated['receipt']['id'])
        self.assertEqual(len(history), 1)
        with self.assertRaisesRegex(ValueError, 'not reversible'):
            server.mutate_chat_action_plan('undo', plan['id'], NOW + timedelta(minutes=1))

    def test_frontend_has_no_reply_phase_profile_writes(self):
        path = os.path.join(os.path.dirname(__file__), '..', 'web', 'js', 'chat.js')
        with open(path, 'r', encoding='utf-8') as handle:
            source = handle.read()
        self.assertNotIn('addWatch(', source)
        self.assertNotIn('removeWatch(', source)
        self.assertNotIn('api.recordSnapshot()', source)
        self.assertIn('previewChatActions', source)
        self.assertIn('data-plan-action="confirm"', source)


if __name__ == '__main__':
    unittest.main()
