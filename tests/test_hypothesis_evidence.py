import unittest
from datetime import datetime, timedelta, timezone

from hypothesis_evidence import capture_market_baseline, collect_candidate_evidence


BJC = timezone(timedelta(hours=8))


def hypothesis():
    return {
        'id': 'hypothesis:test', 'createdAt': '2026-08-20T15:30:00+08:00',
        'baseline': {'watchlist': [{'code': '601138', 'name': '工业富联'}]},
    }


class HypothesisEvidenceTests(unittest.TestCase):
    def test_first_collection_freezes_point_in_time_baseline(self):
        item = capture_market_baseline(
            hypothesis(), lambda code: {'code': code, 'name': '工业富联', 'price': 60, 'source': 'tdx_local'},
            lambda: [{'code': '000001', 'name': '上证指数', 'price': 3900}],
            datetime(2026, 8, 20, 15, 31, tzinfo=BJC))
        self.assertEqual(60, item['marketBaseline']['watchlist'][0]['price'])
        self.assertTrue(item['marketBaseline']['contract']['pointInTime'])
        self.assertFalse(item['marketBaseline']['contract']['closePriceGuaranteed'])

    def test_relative_performance_uses_broad_market_control(self):
        base = capture_market_baseline(
            hypothesis(), lambda code: {'price': 60},
            lambda: [{'code': '000001', 'name': '上证指数', 'price': 3900}],
            datetime(2026, 8, 20, 15, 31, tzinfo=BJC))
        result = collect_candidate_evidence(
            base, lambda code: {'code': code, 'name': '工业富联', 'price': 66, 'source': 'em'},
            lambda: [{'code': '000001', 'name': '上证指数', 'price': 3978}],
            lambda code: {'items': [], 'source': {'id': 'cninfo', 'name': '巨潮资讯'}},
            datetime(2026, 8, 21, 15, 31, tzinfo=BJC))
        row = next(item for item in result['evidenceCandidates'] if item['kind'] == 'relative_performance')
        self.assertEqual(10.0, row['metrics']['stockReturnPct'])
        self.assertEqual(2.0, row['metrics']['benchmarkReturnPct'])
        self.assertEqual(8.0, row['metrics']['excessReturnPct'])
        self.assertFalse(result['evidenceContract']['causalClaim'])
        self.assertFalse(result['evidenceContract']['automaticOutcome'])

    def test_official_disclosures_are_timestamped_and_filtered(self):
        base = capture_market_baseline(hypothesis(), lambda code: {'price': 60},
                                       lambda: [{'code': '000001', 'price': 3900}])
        result = collect_candidate_evidence(
            base, lambda code: {'price': 61}, lambda: [{'code': '000001', 'price': 3910}],
            lambda code: {'source': {'id': 'cninfo', 'name': '巨潮资讯'}, 'items': [
                {'id': 'old', 'title': '旧公告', 'date': '2026-08-19'},
                {'id': 'same', 'title': '当日时点不明公告', 'date': '2026-08-20'},
                {'id': 'new', 'title': '新公告', 'date': '2026-08-21', 'pdf_url': 'https://example/new.pdf'},
                {'id': 'future', 'title': '未来公告', 'date': '2026-08-22'},
            ]}, datetime(2026, 8, 21, 16, 0, tzinfo=BJC))
        official = [item for item in result['evidenceCandidates'] if item['kind'] == 'official_disclosure']
        self.assertEqual(1, len(official))
        self.assertEqual('official', official[0]['source']['tier'])
        self.assertEqual('2026-08-21', official[0]['knowableAt'])

    def test_daily_observations_are_idempotent(self):
        base = capture_market_baseline(hypothesis(), lambda code: {'price': 60},
                                       lambda: [{'code': '000001', 'price': 3900}])
        loaders = (lambda code: {'price': 61}, lambda: [{'code': '000001', 'price': 3910}],
                   lambda code: {'items': []})
        first = collect_candidate_evidence(base, *loaders, datetime(2026, 8, 21, 10, 0, tzinfo=BJC))
        second = collect_candidate_evidence(first, *loaders, datetime(2026, 8, 21, 14, 0, tzinfo=BJC))
        self.assertEqual(len(first['evidenceCandidates']), len(second['evidenceCandidates']))
        self.assertTrue(all(row.get('firstObservedAt') for row in second['evidenceCandidates']))


if __name__ == '__main__':
    unittest.main()
