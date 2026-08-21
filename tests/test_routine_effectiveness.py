import os
import tempfile
import unittest
from unittest.mock import patch

import server


class RoutineEffectivenessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.profile = os.path.join(self.temp.name, 'profile.json')
        self.profile_patch = patch.object(server, 'PROFILE_FILE', self.profile)
        self.profile_patch.start()
        server.save_routine_config({'tasks': {'pre_market': True, 'intraday': True}})

    def tearDown(self):
        self.profile_patch.stop()
        self.temp.cleanup()

    def _seed(self, signals):
        inbox, receipts = [], []
        for index, signal in enumerate(signals):
            item_id = 'routine:intraday:2026-08-%02d' % (10 + index)
            inbox.append({'id': item_id, 'kind': 'routine', 'routineKind': 'intraday',
                          'title': '盘中结构检查', 'createdAt': 1000 + index})
            receipts.append({'id': item_id, 'kind': 'intraday',
                             'serviceDate': '2026-08-%02d' % (10 + index)})
        server.save_profile({'attention_inbox': inbox, 'routine_receipts': receipts})
        for item, signal in zip(inbox, signals):
            server.update_attention_feedback(item['id'], signal, 'test')

    def test_unanswered_services_are_not_counted_as_negative(self):
        self._seed([])
        server.save_profile({'routine_receipts': [
            {'id': 'routine:pre_market:2026-08-10', 'kind': 'pre_market', 'serviceDate': '2026-08-10'}
        ]})
        status = server.routine_effectiveness_status()
        pre = next(row for row in status['periods'] if row['kind'] == 'pre_market')
        self.assertEqual(pre['generated'], 1)
        self.assertEqual(pre['feedbackCount'], 0)
        self.assertEqual(pre['negativeCount'], 0)
        self.assertEqual(status['recommendations'], [])
        self.assertEqual(status['basis'], 'explicit-feedback-only')

    def test_feedback_is_attributed_to_exact_routine_period(self):
        self._seed(['helpful', 'done', 'too_frequent'])
        status = server.routine_effectiveness_status()
        intraday = next(row for row in status['periods'] if row['kind'] == 'intraday')
        self.assertEqual(intraday['feedbackCount'], 3)
        self.assertEqual(intraday['helpedCount'], 2)
        self.assertEqual(intraday['completedCount'], 1)
        self.assertEqual(intraday['negativeCount'], 1)
        self.assertEqual(status['totals']['helpedCount'], 2)
        self.assertEqual(status['recommendations'], [])

    def test_negative_majority_suggests_but_never_auto_applies(self):
        self._seed(['too_frequent', 'irrelevant', 'too_frequent'])
        status = server.routine_effectiveness_status()
        self.assertTrue(server.load_routine_config()['tasks']['intraday'])
        self.assertEqual(len(status['recommendations']), 1)
        self.assertTrue(status['recommendations'][0]['requiresConfirmation'])
        self.assertFalse(status['automaticChanges'])

    def test_confirmed_adjustment_can_be_undone(self):
        self._seed(['too_frequent', 'irrelevant', 'too_frequent'])
        suggestion = server.routine_effectiveness_status()['recommendations'][0]
        with self.assertRaises(ValueError):
            server.mutate_routine_effect('apply_suggestion', suggestion['id'])
        saved, status = server.mutate_routine_effect(
            'apply_suggestion', suggestion['id'], confirmed=True)
        self.assertFalse(saved['data']['market_routine']['tasks']['intraday'])
        action = status['activeActions'][-1]
        restored, restored_status = server.mutate_routine_effect('undo', action_id=action['id'])
        self.assertTrue(restored['data']['market_routine']['tasks']['intraday'])
        self.assertEqual(restored_status['activeActions'], [])


if __name__ == '__main__':
    unittest.main()
