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

    def test_server_prefers_tdx_quote_when_available(self):
        expected = {'code': '600519', 'price': 1500, 'source': 'tdx_local'}
        with patch.object(server, 'tdx_read_quote', return_value=expected), \
                patch.object(server, 'em_quote_any') as eastmoney:
            result = server.quote_with_fallback('600519')
        self.assertEqual(result, expected)
        eastmoney.assert_not_called()


if __name__ == '__main__':
    unittest.main()
