import os
import tempfile
import unittest
from unittest.mock import patch

import server


def sample_state(alert=None):
    rows = []
    for index in range(20):
        base = 10 + index * 0.05
        rows.append({
            'date': '2026-08-%02d' % (index + 1),
            'open': base,
            'close': base + (0.08 if index % 2 else -0.04),
            'high': base + 0.14,
            'low': base - 0.12,
        })
    return {
        'generated_at': '2026-08-18T10:30:00+08:00',
        'sequence': 1,
        'market_session': 'OPEN',
        'device': {'mode': 'alert' if alert else 'focus'},
        'emotion': {
            'temperature': 68, 'phase': '高潮期', 'confidence': 86, 'coverage': 92,
            'dimensions': [
                {'key': 'earning', 'value': 66},
                {'key': 'loss_control', 'value': 58},
                {'key': 'continuity', 'value': 72},
                {'key': 'breadth', 'value': 63},
                {'key': 'liquidity', 'value': 55},
                {'key': 'quality', 'value': 69},
            ],
        },
        'focus': {'code': '000001', 'price': 12.34, 'pct': 1.25, 'kline': rows},
        'indices': [
            {'code': '000001', 'pct': 1.1}, {'code': '399001', 'pct': 1.8},
            {'code': '399006', 'pct': 2.2}, {'code': '000688', 'pct': 2.6},
            {'code': '899050', 'pct': 0.9},
        ],
        'market': {'zt': 79, 'dt': 5, 'zb_rate': 0.225, 'height': 4,
                   'up': 4280, 'down': 885, 'flow_yi': 138.6},
        'watch': [
            {'code': '601138', 'name': '工业富联', 'price': 61.82, 'pct': -6.50},
            {'code': '000001', 'name': '平安银行', 'price': 12.34, 'pct': 1.25},
        ],
        'hotspots': [
            {'code': 'BK1452', 'name': '卫浴电器', 'pct': 7.19},
            {'code': 'BK1492', 'name': '焦炭Ⅲ', 'pct': 6.53},
        ],
        'quality': {'tdx_status': 'connected', 'stale': False},
        'alert': alert,
    }


class DeviceConfigTests(unittest.TestCase):
    def test_defaults_are_safe_and_normalization_clamps_intervals(self):
        config = server.normalize_device_config({
            'enabled': True, 'focus_code': 'SZ000001',
            'poll_seconds': 1, 'display_seconds': 9999,
            'partial_before_full': 1,
        })
        self.assertTrue(config['enabled'])
        self.assertEqual(config['port'], 8988)
        self.assertEqual(config['focus_code'], '000001')
        self.assertEqual(config['poll_seconds'], 15)
        self.assertEqual(config['display_seconds'], 1800)
        self.assertEqual(config['partial_before_full'], 2)
        self.assertEqual(config['refresh_policy'], 'smart')
        self.assertGreaterEqual(len(config['token']), 24)
        self.assertFalse(server._device_defaults()['enabled'])

    def test_invalid_focus_code_is_rejected(self):
        with self.assertRaises(ValueError):
            server.normalize_device_config({'focus_code': 'not-a-security'})

    def test_all_display_modes_are_supported(self):
        for mode in server.DEVICE_MODES:
            self.assertEqual(server.normalize_device_config({'mode': mode})['mode'], mode)
        self.assertEqual(server.normalize_device_config({'mode': 'unknown'})['mode'], 'focus')

    def test_refresh_policy_is_allowlisted(self):
        for policy in server.DEVICE_REFRESH_POLICIES:
            config = server.normalize_device_config({'refresh_policy': policy})
            self.assertEqual(config['refresh_policy'], policy)
        config = server.normalize_device_config({'refresh_policy': 'unsafe-custom-lut'})
        self.assertEqual(config['refresh_policy'], 'smart')

    def test_token_is_preserved_until_explicit_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_file = os.path.join(tmp, 'device_config.json')
            with patch.object(server, 'DEVICE_CONFIG_FILE', config_file), \
                    patch.object(server, 'sync_device_gateway'):
                first = server.load_device_config(persist=True)
                saved = server.save_device_config({'device_name': 'Desk EPD'})
                rotated = server.save_device_config({}, rotate_token=True)
        self.assertEqual(first['token'], saved['token'])
        self.assertNotEqual(saved['token'], rotated['token'])
        self.assertEqual(rotated['revision'], 2)

    def test_token_comparison_requires_exact_nonempty_value(self):
        config = {'token': 'a' * 32}
        self.assertTrue(server.device_token_matches('a' * 32, config))
        self.assertFalse(server.device_token_matches('a' * 31, config))
        self.assertFalse(server.device_token_matches('', config))


class FrameTests(unittest.TestCase):
    @staticmethod
    def _is_black(frame, x, y):
        byte = frame[y * (server.EPAPER_WIDTH // 8) + x // 8]
        return not bool(byte & (0x80 >> (x % 8)))

    def test_focus_frame_is_exact_display_size_and_bmp_is_valid(self):
        frame = server.render_epaper_frame(sample_state())
        bmp = server.epaper_frame_to_bmp(frame)
        self.assertEqual(len(frame), 48000)
        self.assertEqual(len(bmp), 48062)
        self.assertEqual(bmp[:2], b'BM')
        self.assertNotEqual(frame, bytes([0xFF]) * len(frame))

    def test_alert_demo_produces_a_distinct_frame(self):
        focus = server.render_epaper_frame(sample_state())
        alert = server.render_epaper_frame(sample_state({
            'demo': True, 'code': '000001', 'dir': 'up', 'price': 12.80,
        }))
        self.assertEqual(len(alert), 48000)
        self.assertNotEqual(focus, alert)

    def test_overview_mode_has_its_own_layout(self):
        focus_state = sample_state()
        overview_state = sample_state()
        overview_state['device']['mode'] = 'overview'
        self.assertNotEqual(server.render_epaper_frame(focus_state),
                            server.render_epaper_frame(overview_state))

    def test_new_decision_modes_render_distinct_full_frames(self):
        frames = {}
        for mode in ('focus', 'emotion', 'watch', 'hotspot'):
            state = sample_state()
            state['device']['mode'] = mode
            frames[mode] = server.render_epaper_frame(state)
            self.assertEqual(len(frames[mode]), 48000)
        self.assertEqual(len(set(frames.values())), len(frames))

    def test_dictionary_kline_rows_are_rendered(self):
        with_rows = server.render_epaper_frame(sample_state())
        empty = sample_state()
        empty['focus']['kline'] = []
        without_rows = server.render_epaper_frame(empty)
        self.assertNotEqual(with_rows, without_rows)

    def test_focus_layout_uses_the_former_footer_space(self):
        frame = server.render_epaper_frame(sample_state())
        chart_right_edge = sum(
            self._is_black(frame, 569, y) for y in range(126, 460))
        self.assertGreaterEqual(chart_right_edge, 330)

    def test_focus_layout_has_market_fallback_when_indices_are_missing(self):
        state = sample_state()
        state['indices'] = []
        frame = server.render_epaper_frame(state)
        black = sum(
            self._is_black(frame, x, y)
            for y in range(286, 448)
            for x in range(600, 770)
        )
        self.assertGreater(black, 300)

    def test_content_hash_ignores_clock_but_tracks_decision_changes(self):
        first = sample_state()
        second = sample_state()
        second['generated_at'] = '2026-08-18T10:31:00+08:00'
        first_frame = server.render_epaper_frame(first)
        second_frame = server.render_epaper_frame(second)
        self.assertNotEqual(first_frame, second_frame)
        self.assertEqual(server.epaper_content_sha256(first_frame),
                         server.epaper_content_sha256(second_frame))
        second['focus']['price'] = 13.14
        changed_frame = server.render_epaper_frame(second)
        self.assertNotEqual(server.epaper_content_sha256(first_frame),
                            server.epaper_content_sha256(changed_frame))


class DeviceStateTests(unittest.TestCase):
    def test_demo_forces_alert_layout_without_changing_saved_mode(self):
        config = server.normalize_device_config({'mode': 'focus', 'focus_code': '000001'})
        with patch.object(server, 'assemble_emotion', return_value={'engine': {}}), \
                patch.object(server, 'cached', side_effect=RuntimeError('offline')), \
                patch.object(server, 'load_profile', return_value={'data': {}}):
            state = server.build_device_state(config, demo='alert')
        self.assertEqual(state['device']['mode'], 'alert')
        self.assertTrue(state['alert']['demo'])

    def test_preview_mode_overrides_saved_mode_without_persisting(self):
        config = server.normalize_device_config({'mode': 'focus', 'focus_code': '000001'})
        emotion = {'engine': {'dimensions': [{'key': 'earning', 'value': 70}], 'raw': {}}}
        with patch.object(server, 'assemble_emotion', return_value=emotion), \
                patch.object(server, 'cached', side_effect=RuntimeError('offline')), \
                patch.object(server, 'load_profile', return_value={'data': {}}):
            state = server.build_device_state(config, demo='emotion')
        self.assertEqual(config['mode'], 'focus')
        self.assertEqual(state['device']['mode'], 'emotion')
        self.assertEqual(state['emotion']['dimensions'][0]['value'], 70)

    def test_only_recent_triggered_alert_is_sent_to_hardware(self):
        config = server.normalize_device_config({'mode': 'alert', 'focus_code': '000001'})
        profile = {'data': {'alerts': [
            {'code': 'old', 'triggered': True, 'triggered_at': 1},
            {'code': '000001', 'triggered': True, 'triggered_at': 950000,
             'dir': 'up', 'price': 12.8},
        ]}}
        emotion = {
            'date': '2026-08-18',
            'engine': {'temp': 68, 'phase': '高潮期', 'confidence': 80,
                       'coverage': 90, 'flags': [], 'missing': []},
            'tdx_local': {'status': 'connected'},
        }
        quote = {'name': '平安银行', 'price': 12.3, 'pct': 1.2}
        kline = {'rows': sample_state()['focus']['kline']}

        def fake_cached(key, ttl, loader):
            if key == 'indices':
                return [{'code': '000001', 'name': '上证', 'price': 1, 'pct': 1}]
            if key.startswith('quote_'):
                return quote
            if key.startswith('device_kline_'):
                return kline
            return loader()

        with patch.object(server, 'assemble_emotion', return_value=emotion), \
                patch.object(server, 'cached', side_effect=fake_cached), \
                patch.object(server, 'load_profile', return_value=profile), \
                patch.object(server.time, 'time', return_value=1000):
            state = server.build_device_state(config)

        self.assertEqual(state['device']['mode'], 'alert')
        self.assertEqual(state['alert']['code'], '000001')
        self.assertEqual(state['quality']['tdx_status'], 'connected')


if __name__ == '__main__':
    unittest.main()
