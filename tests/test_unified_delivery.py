import os
import tempfile
import time
import unittest
from unittest.mock import patch

import server


class UnifiedDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.profile = os.path.join(self.temp.name, 'profile.json')
        self.profile_patch = patch.object(server, 'PROFILE_FILE', self.profile)
        self.profile_patch.start()
        self.now = int(time.time() * 1000)

    def tearDown(self):
        self.profile_patch.stop()
        self.temp.cleanup()

    def seed(self, **preferences):
        item_delivery = preferences.pop('itemDelivery', 'immediate')
        prefs = {
            'mode': 'balanced', 'quietEnabled': False,
            'desktopSystemEnabled': False, 'epaperDeliveryEnabled': False,
            **preferences,
        }
        item = {
            'id': 'event:601138:earnings', 'kind': 'event', 'priority': 'high',
            'title': '工业富联重要事件', 'detail': '601138 出现新的官方披露',
            'reason': '关注标的发生重要变化', 'page': 'watch',
            'delivery': item_delivery, 'createdAt': self.now,
            'expiresAt': self.now + 3600_000,
        }
        server.save_profile({'attention_preferences': prefs, 'attention_inbox': [item]})
        return item

    def test_channels_are_safe_default_off(self):
        self.seed()
        desktop = server.claim_attention_delivery('desktop', 'test')
        epaper = server.claim_attention_delivery('epaper', 'test')
        self.assertFalse(desktop['enabled'])
        self.assertIsNone(desktop['item'])
        self.assertFalse(epaper['enabled'])
        self.assertIsNone(epaper['item'])

    def test_authorized_new_item_is_delivered_once_per_channel(self):
        self.seed(desktopSystemEnabled=True, desktopSystemEnabledAt=self.now - 1,
                  epaperDeliveryEnabled=True, epaperDeliveryEnabledAt=self.now - 1)
        desktop = server.claim_attention_delivery('desktop', 'windows-app')
        epaper = server.claim_attention_delivery('epaper', 'waveshare-esp32')
        self.assertEqual(desktop['item']['id'], 'event:601138:earnings')
        self.assertEqual(epaper['item']['id'], 'event:601138:earnings')
        self.assertEqual(desktop['receipt']['title'], '工业富联重要事件')
        self.assertEqual(desktop['receipt']['page'], 'watch')
        server.acknowledge_attention_delivery(
            'desktop', desktop['item']['id'], 'delivered', 'windows-app')
        server.acknowledge_attention_delivery(
            'epaper', epaper['item']['id'], 'delivered', 'waveshare-esp32')
        self.assertIsNone(server.claim_attention_delivery('desktop', 'windows-app')['item'])
        self.assertIsNone(server.claim_attention_delivery('epaper', 'waveshare-esp32')['item'])
        status = server.attention_delivery_status()['channels']
        self.assertEqual(status['desktop']['delivered'], 1)
        self.assertEqual(status['epaper']['delivered'], 1)

    def test_failed_delivery_can_be_explicitly_requeued(self):
        self.seed(desktopSystemEnabled=True, desktopSystemEnabledAt=self.now - 1)
        claimed = server.claim_attention_delivery('desktop', 'windows-app')
        server.acknowledge_attention_delivery(
            'desktop', claimed['item']['id'], 'failed', 'windows-app', 'toast unavailable')
        status = server.attention_delivery_status()
        failed = status['recent'][0]
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['title'], '工业富联重要事件')
        self.assertEqual(failed['error'], 'toast unavailable')
        queued = server.retry_attention_delivery('desktop', claimed['item']['id'])
        self.assertEqual(queued['state'], 'queued')
        retried = server.claim_attention_delivery('desktop', 'windows-app')
        self.assertEqual(retried['item']['id'], claimed['item']['id'])
        self.assertEqual(retried['receipt']['attempts'], 2)

    def test_successful_delivery_cannot_be_requeued_as_failure(self):
        self.seed(desktopSystemEnabled=True, desktopSystemEnabledAt=self.now - 1)
        claimed = server.claim_attention_delivery('desktop', 'windows-app')
        server.acknowledge_attention_delivery(
            'desktop', claimed['item']['id'], 'delivered', 'windows-app')
        with self.assertRaisesRegex(ValueError, 'only failed deliveries'):
            server.retry_attention_delivery('desktop', claimed['item']['id'])

    def test_enabling_does_not_replay_older_items(self):
        self.seed(desktopSystemEnabled=True, desktopSystemEnabledAt=self.now + 1)
        result = server.claim_attention_delivery('desktop', 'windows-app')
        self.assertIsNone(result['item'])
        self.assertEqual(result['reasons'].get('before_authorization'), 1)

    def test_item_center_only_never_leaves_inbox_after_digest_wait(self):
        self.seed(itemDelivery='center_only', desktopSystemEnabled=True,
                  desktopSystemEnabledAt=self.now - 1,
                  epaperDeliveryEnabled=True, epaperDeliveryEnabledAt=self.now - 1)
        future = self.now + 59 * 60_000
        with patch.object(server.time, 'time', return_value=future / 1000):
            desktop = server.claim_attention_delivery('desktop', 'windows-app')
            epaper = server.claim_attention_delivery('epaper', 'waveshare-esp32')
            status = server.attention_delivery_status()
        self.assertIsNone(desktop['item'])
        self.assertIsNone(epaper['item'])
        self.assertEqual(desktop['reasons'].get('item_center_only'), 1)
        self.assertEqual(epaper['reasons'].get('item_center_only'), 1)
        self.assertEqual(status['heldInCenter'], 1)
        self.assertEqual(status['heldReasons'].get('item_center_only'), 1)
        self.assertIn('center-only-never-leaves-inbox', status['policy'])

    def test_attention_delivery_item_forces_hardware_alert_scene(self):
        config = server.normalize_device_config({'mode': 'focus', 'focus_code': '601138'})
        item = self.seed(epaperDeliveryEnabled=True, epaperDeliveryEnabledAt=self.now - 1)
        with patch.object(server, 'assemble_emotion', return_value={'engine': {}}), \
                patch.object(server, 'cached', side_effect=RuntimeError('offline')):
            state = server.build_device_state(config, delivery_item=item)
        self.assertEqual(state['device']['mode'], 'alert')
        self.assertTrue(state['alert']['attention'])
        self.assertEqual(state['alert']['code'], '601138')


if __name__ == '__main__':
    unittest.main()
