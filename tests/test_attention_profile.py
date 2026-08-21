import json
import os
import tempfile
import unittest
from unittest.mock import patch

import server


class AttentionProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.profile = os.path.join(self.temp.name, 'profile.json')
        self.profile_patch = patch.object(server, 'PROFILE_FILE', self.profile)
        self.profile_patch.start()

    def tearDown(self):
        self.profile_patch.stop()
        self.temp.cleanup()

    def test_attention_preferences_are_saved_as_object(self):
        result = server.save_profile({'attention_preferences': {
            'mode': 'balanced', 'quietEnabled': True, 'quietStart': '22:30', 'quietEnd': '08:00'
        }})
        self.assertEqual(result['data']['attention_preferences']['mode'], 'balanced')

    def test_attention_inbox_is_bounded(self):
        result = server.save_profile({'attention_inbox': [{'id': str(index)} for index in range(205)]})
        self.assertEqual(len(result['data']['attention_inbox']), 200)
        self.assertEqual(result['data']['attention_inbox'][0]['id'], '5')

    def test_atomic_attention_merge_preserves_other_items(self):
        server.update_attention_item({'id': 'one', 'title': 'first'})
        result = server.update_attention_item({'id': 'two', 'title': 'second'})
        self.assertEqual([item['id'] for item in result['data']['attention_inbox']], ['one', 'two'])
        updated = server.update_attention_item({'id': 'one', 'title': 'updated'})
        self.assertEqual([item['id'] for item in updated['data']['attention_inbox']], ['two', 'one'])
        self.assertEqual(updated['data']['attention_inbox'][-1]['title'], 'updated')

    def test_atomic_attention_remove(self):
        server.update_attention_item({'id': 'one', 'title': 'first'})
        result = server.update_attention_item({'id': 'one'}, remove=True)
        self.assertEqual(result['data']['attention_inbox'], [])

    def test_preferences_reject_array(self):
        with self.assertRaisesRegex(ValueError, 'must be an object'):
            server.save_profile({'attention_preferences': []})


if __name__ == '__main__':
    unittest.main()
