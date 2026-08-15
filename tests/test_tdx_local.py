import unittest
from unittest.mock import patch

import server
import tdx_local


class TdxLocalAdapterTests(unittest.TestCase):
    def test_trade_methods_are_blocked_by_code(self):
        with self.assertRaises(tdx_local.TdxLocalError):
            tdx_local.rpc_call('order_stock', {'stock_code': '600519.SH'})

    def test_a_share_code_mapping(self):
        self.assertEqual(tdx_local.stock_code('600519'), '600519.SH')
        self.assertEqual(tdx_local.stock_code('000001'), '000001.SZ')
        self.assertEqual(tdx_local.stock_code('830799'), '830799.BJ')

    @patch.object(tdx_local.platform, 'system', return_value='Linux')
    def test_environment_reports_non_windows_as_unsupported(self, _system):
        status = tdx_local.environment_status()
        self.assertFalse(status['supported'])
        self.assertEqual(status['status'], 'unsupported')

    def test_environment_accepts_valid_signed_running_client_when_winreg_is_hidden(self):
        signed = {'name': '通达信金融终端64', 'location': r'C:\new_tdx64',
                  'detection': 'signed_running_process'}
        with patch.object(tdx_local.platform, 'system', return_value='Windows'), \
                patch.object(tdx_local, '_registry_install', return_value=None), \
                patch.object(tdx_local, '_process_running', return_value=True), \
                patch.object(tdx_local, '_signed_install_cache', None), \
                patch.object(tdx_local, '_signed_running_install', return_value=signed):
            status = tdx_local.environment_status()
        self.assertTrue(status['installed'])
        self.assertTrue(status['process_running'])
        self.assertEqual(status['status'], 'unobserved')

    def test_environment_rejects_unsigned_running_client(self):
        with patch.object(tdx_local.platform, 'system', return_value='Windows'), \
                patch.object(tdx_local, '_registry_install', return_value=None), \
                patch.object(tdx_local, '_process_running', return_value=True), \
                patch.object(tdx_local, '_signed_install_cache', None), \
                patch.object(tdx_local, '_signed_running_install', return_value=None):
            status = tdx_local.environment_status()
        self.assertFalse(status['installed'])
        self.assertEqual(status['status'], 'not_installed')

    def test_probe_stops_before_rpc_when_client_is_not_installed(self):
        status = {'supported': True, 'installed': False, 'process_running': False,
                  'service_ready': False, 'status': 'not_installed'}
        with patch.object(tdx_local, 'environment_status', return_value=status), \
                patch.object(tdx_local, 'rpc_call') as rpc:
            result = tdx_local.probe_status()
        self.assertEqual(result['status'], 'not_installed')
        rpc.assert_not_called()

    def test_quote_normalizes_snapshot_and_metadata(self):
        responses = [
            {'value': {'Now': '1500', 'LastClose': '1470', 'Open': '1480',
                       'Max': '1512', 'Min': '1468', 'Volume': '1024',
                       'Amount': '8000000'}, 'latency_ms': 2},
            {'value': {'ZAF': '2.04', 'fHSL': '0.66', 'fLianB': '1.25',
                       'Zsz': '19000', 'Ltsz': '19000'}, 'latency_ms': 1},
            {'value': {'Name': '贵州茅台'}, 'latency_ms': 1},
        ]
        with patch.object(tdx_local, 'rpc_call', side_effect=responses):
            quote = tdx_local.quote('600519')
        self.assertEqual(quote['name'], '贵州茅台')
        self.assertEqual(quote['price'], 1500.0)
        self.assertEqual(quote['pct'], 2.04)
        self.assertEqual(quote['source'], 'tdx_local')

    def test_columnar_kline_is_converted_to_rows(self):
        value = {'600519.SH': {
            'Date': ['20260813', '20260814'],
            'Open': ['1350', '1355'], 'High': ['1360', '1359'],
            'Low': ['1340', '1338'], 'Close': ['1355', '1342'],
            'Volume': ['100', '200'], 'Amount': ['1000', '2000'],
        }}
        rows = tdx_local._row_list(value)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['Close'], '1342')

    def test_short_local_kline_falls_back_instead_of_returning_fake_history(self):
        response = {'value': {'600519.SH': {
            'Date': ['20260814'], 'Open': ['1355'], 'High': ['1359'],
            'Low': ['1338'], 'Close': ['1342'], 'Volume': ['200'],
            'Amount': ['2000'],
        }}, 'latency_ms': 1}
        with patch.object(tdx_local, 'rpc_call', return_value=response):
            with self.assertRaises(tdx_local.TdxLocalError):
                tdx_local.kline('600519', n=5)

    def test_unavailable_professional_stats_do_not_break_tdx_quotes(self):
        with patch.object(server, '_tdx_require_ready'), \
                patch.object(server.tdx_local_api, 'emotion_snapshot',
                             side_effect=tdx_local.TdxLocalError('ErrorId=10')), \
                patch.object(server, '_mark_host_down') as mark_down:
            result = server.tdx_emotion_verification()
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['reason'], 'professional_market_data_unavailable')
        mark_down.assert_not_called()

    def test_server_prefers_tdx_quote_when_available(self):
        expected = {'code': '600519', 'price': 1500, 'source': 'tdx_local'}
        with patch.object(server, 'tdx_read_quote', return_value=expected), \
                patch.object(server, 'em_quote_any') as eastmoney:
            result = server.quote_with_fallback('600519')
        self.assertEqual(result, expected)
        eastmoney.assert_not_called()

    def test_ready_probe_clears_stale_tdx_circuit(self):
        server._mark_host_down(server.TDX_HOST, 30)
        try:
            with patch.object(server, 'tdx_status', return_value={'service_ready': True}):
                server._tdx_require_ready()
            self.assertTrue(server._host_ok(server.TDX_HOST))
        finally:
            server._clear_host_down(server.TDX_HOST)


if __name__ == '__main__':
    unittest.main()
