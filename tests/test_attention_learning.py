import os
import tempfile
import unittest
from unittest.mock import patch

import server


class AttentionLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.profile = os.path.join(self.temp.name, 'profile.json')
        self.profile_patch = patch.object(server, 'PROFILE_FILE', self.profile)
        self.profile_patch.start()

    def tearDown(self):
        self.profile_patch.stop()
        self.temp.cleanup()

    def _item(self, item_id='move-1', kind='move'):
        item = {
            'id': item_id, 'kind': kind, 'title': '测试提醒', 'detail': '结构发生变化',
            'reason': '用户已授权的市场监控', 'createdAt': 1000, 'readAt': None,
        }
        server.save_profile({'attention_inbox': [item]})
        return item

    def test_helpful_feedback_is_explicit_and_does_not_change_delivery(self):
        self._item()
        saved, learning = server.update_attention_feedback('move-1', 'helpful', 'test')
        data = saved['data']
        self.assertEqual(data['attention_inbox'][0]['feedback'], 'helpful')
        self.assertEqual(data['attention_feedback'][0]['signal'], 'helpful')
        self.assertEqual(data['attention_preferences']['kindControls'], {})
        self.assertEqual(learning['feedbackCount'], 1)
        self.assertEqual(learning['counts']['helpful'], 1)
        self.assertEqual(learning['basis'], 'explicit-user-feedback-only')

    def test_too_frequent_and_irrelevant_create_reversible_noise_controls(self):
        self._item('phase-1', 'phase')
        saved, _ = server.update_attention_feedback('phase-1', 'too_frequent')
        control = saved['data']['attention_preferences']['kindControls']['phase']
        self.assertEqual(control['delivery'], 'digest')
        self.assertEqual(control['reason'], 'too_frequent')

        saved, learning = server.update_attention_feedback('phase-1', 'irrelevant')
        control = saved['data']['attention_preferences']['kindControls']['phase']
        self.assertEqual(control['delivery'], 'center_only')
        self.assertEqual(learning['activeControls'], 1)
        # One item has one current outcome, avoiding inflated learning counts.
        self.assertEqual(learning['feedbackCount'], 1)
        self.assertEqual(learning['counts']['irrelevant'], 1)

    def test_price_alert_is_never_silently_downgraded(self):
        self._item('price-1', 'price')
        saved, _ = server.update_attention_feedback('price-1', 'irrelevant')
        self.assertNotIn('price', saved['data']['attention_preferences']['kindControls'])

    def test_done_marks_item_read_and_completed(self):
        self._item('routine-1', 'routine')
        saved, _ = server.update_attention_feedback('routine-1', 'done')
        item = saved['data']['attention_inbox'][0]
        self.assertIsNotNone(item['readAt'])
        self.assertEqual(item['doneAt'], item['feedbackAt'])

    def test_controls_can_be_restored_without_erasing_history(self):
        self._item('move-1', 'move')
        server.update_attention_feedback('move-1', 'too_frequent')
        saved, learning = server.reset_attention_learning('move')
        self.assertEqual(saved['data']['attention_preferences']['kindControls'], {})
        self.assertEqual(len(saved['data']['attention_feedback']), 1)
        self.assertEqual(learning['activeControls'], 0)

    def test_user_can_clear_learning_history_and_controls(self):
        self._item('move-1', 'move')
        server.update_attention_feedback('move-1', 'irrelevant')
        saved, learning = server.reset_attention_learning(clear_history=True)
        self.assertEqual(saved['data']['attention_feedback'], [])
        self.assertEqual(saved['data']['attention_preferences']['kindControls'], {})
        self.assertNotIn('feedback', saved['data']['attention_inbox'][0])
        self.assertEqual(learning['feedbackCount'], 0)

    def test_unknown_item_and_signal_are_rejected(self):
        self._item()
        with self.assertRaises(ValueError):
            server.update_attention_feedback('missing', 'helpful')
        with self.assertRaises(ValueError):
            server.update_attention_feedback('move-1', 'buy_now')


if __name__ == '__main__':
    unittest.main()
