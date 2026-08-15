import json
import unittest
from unittest.mock import patch

import server


class OfficialSourceTests(unittest.TestCase):
    def test_cninfo_disclosures_keep_only_the_requested_security(self):
        payload = {
            'totalAnnouncement': 2,
            'announcements': [
                {
                    'secCode': '600519', 'secName': '<em>贵州茅台</em>',
                    'announcementId': 'a1',
                    'announcementTitle': '<em>贵州茅台</em>重大风险提示公告',
                    'announcementTime': 1786723200000,
                    'adjunctUrl': 'finalpage/2026-08-15/a1.PDF',
                },
                {
                    'secCode': '000001', 'secName': '平安银行',
                    'announcementId': 'a2', 'announcementTitle': '无关公告',
                    'announcementTime': 1786723200000,
                    'adjunctUrl': 'finalpage/2026-08-15/a2.PDF',
                },
            ],
        }
        with patch.object(server, 'fetch', return_value=json.dumps(payload, ensure_ascii=False)):
            result = server.cninfo_disclosures('600519')

        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['items'][0]['title'], '贵州茅台重大风险提示公告')
        self.assertTrue(result['items'][0]['focus'])
        self.assertEqual(result['items'][0]['source_tier'], 'official')
        self.assertEqual(result['items'][0]['pdf_url'],
                         'https://static.cninfo.com.cn/finalpage/2026-08-15/a1.PDF')

    def test_source_catalog_does_not_claim_unobserved_hosts_are_online(self):
        with server._source_lock:
            server._source_stats.clear()
        tdx_env = {
            'supported': True, 'installed': False, 'process_running': False,
            'service_ready': False, 'status': 'not_installed', 'read_only': True,
        }
        with patch.object(server.tdx_local_api, 'environment_status', return_value=tdx_env):
            items = {item['id']: item for item in server.source_catalog()['items']}
        self.assertEqual(items['cninfo']['status'], 'unobserved')
        self.assertEqual(items['sse']['status'], 'reference')
        self.assertEqual(items['tdx_local']['status'], 'not_installed')
        self.assertTrue(items['tdx_local']['environment']['read_only'])


if __name__ == '__main__':
    unittest.main()
