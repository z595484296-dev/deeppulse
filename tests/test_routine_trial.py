import os
import tempfile
import time
import unittest
from datetime import datetime
from unittest.mock import patch

import server


class RoutineTrialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.profile = os.path.join(self.temp.name, 'profile.json')
        self.history = os.path.join(self.temp.name, 'history.json')
        self.profile_patch = patch.object(server, 'PROFILE_FILE', self.profile)
        self.history_patch = patch.object(server, 'HISTORY_FILE', self.history)
        self.profile_patch.start()
        self.history_patch.start()
        with server._routine_trial_lock:
            server._routine_trials.clear()

    def tearDown(self):
        with server._routine_trial_lock:
            server._routine_trials.clear()
        self.history_patch.stop()
        self.profile_patch.stop()
        self.temp.cleanup()

    @staticmethod
    def _emotion():
        return {
            'date': '2026-08-21',
            'breadth': {'up': 3200, 'down': 1700},
            'engine': {
                'date': '2026-08-21', 'temp': 58, 'phase': '发酵期',
                'degraded': False,
                'raw': {'zt': 48, 'dt': 6, 'zb': 15},
            },
        }

    def _trial(self, kind='close_review'):
        return server.preview_routine_trial(
            kind,
            datetime(2026, 8, 21, 15, 20, tzinfo=server.BJC),
            emotion_loader=lambda force: self._emotion(),
        )

    def test_trial_uses_real_generator_and_is_zero_write(self):
        before = server.load_profile()
        trial = self._trial()
        after = server.load_profile()
        self.assertEqual(after, before)
        self.assertTrue(trial['noWrite'])
        self.assertTrue(trial['noDelivery'])
        self.assertEqual(trial['generator'], 'same-local-routine-generator')
        self.assertIn('发酵期', trial['output']['title'])
        self.assertIn('涨停 48', trial['output']['detail'])
        self.assertEqual(trial['output']['dataDate'], '2026-08-21')
        self.assertEqual(trial['proposedAuthorization']['delivery'], 'center_only')
        self.assertNotIn('attention_inbox', after['data'])
        self.assertNotIn('routine_receipts', after['data'])
        self.assertNotIn('routine_authorization_receipts', after['data'])

    def test_confirm_requires_explicit_consent_and_creates_center_only_receipt(self):
        trial = self._trial()
        with self.assertRaisesRegex(ValueError, '明确确认'):
            server.confirm_routine_trial(trial['trialId'], trial['profileRevision'])
        result = server.confirm_routine_trial(
            trial['trialId'], trial['profileRevision'], confirmed=True)
        data = server.load_profile()['data']
        self.assertTrue(data['market_routine']['tasks']['close_review'])
        self.assertEqual(data['market_routine']['delivery'], 'center_only')
        self.assertEqual(len(data['routine_authorization_receipts']), 1)
        receipt = result['authorizationReceipt']
        self.assertEqual(receipt['action'], 'enabled_after_trial')
        self.assertEqual(receipt['delivery'], 'center_only')
        self.assertEqual(receipt['trialOutputFingerprint'], trial['outputFingerprint'])
        item = server.build_routine_attention(
            'close_review', datetime(2026, 8, 21, 15, 20, tzinfo=server.BJC),
            emotion_loader=lambda force: self._emotion())
        self.assertEqual(item['delivery'], 'center_only')

    def test_profile_change_invalidates_trial_without_enabling(self):
        trial = self._trial('intraday')
        server.save_profile({'watchlist': [{'code': '601138', 'name': '工业富联'}]})
        with self.assertRaisesRegex(ValueError, '档案已变化'):
            server.confirm_routine_trial(
                trial['trialId'], trial['profileRevision'], confirmed=True)
        config = server.load_routine_config()
        self.assertFalse(config['tasks']['intraday'])

    def test_expired_trial_cannot_enable_service(self):
        trial = self._trial('intraday')
        with server._routine_trial_lock:
            server._routine_trials[trial['trialId']]['createdEpoch'] = (
                time.time() - server.ROUTINE_TRIAL_TTL_SECONDS - 1)
        with self.assertRaisesRegex(ValueError, '已过期'):
            server.confirm_routine_trial(
                trial['trialId'], trial['profileRevision'], confirmed=True)
        self.assertFalse(server.load_routine_config()['enabled'])

    def test_trial_token_is_single_use(self):
        trial = self._trial('pre_market')
        server.confirm_routine_trial(
            trial['trialId'], trial['profileRevision'], confirmed=True)
        with self.assertRaisesRegex(ValueError, '不存在或已经使用'):
            server.confirm_routine_trial(
                trial['trialId'], trial['profileRevision'], confirmed=True)

    def test_unconfirmed_config_route_cannot_bypass_first_trial(self):
        with self.assertRaisesRegex(ValueError, '请先试运行'):
            server.save_routine_config(
                {'tasks': {'pre_market': True}}, allow_new_enable=False)
        self.assertFalse(server.load_routine_config()['enabled'])


if __name__ == '__main__':
    unittest.main()
