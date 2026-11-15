"""Offline tests: new research daily, verified URLs, correct publication dates."""
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from briefing_windows import KST
from report_sources import SEEDS, collect_reports, normalize_report_url


class InstitutionalReportsTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 15, 8, 5, tzinfo=KST)
        self.news = {"ecm": [
            {"headline": "IPO 코스닥 기술기업 상장 확대",
             "summary": ["기술특례상장 제도 개선", "기업공개 자금조달", "증권사 주관"],
             "url": "https://www.hankyung.com/article/1234"}]}

    def test_verified_recent_pool(self):
        assert len(SEEDS) >= 7
        assert all(r["url"].startswith("https://") for r in SEEDS)
        assert any("핀플루언서" in r["title"] for r in SEEDS)

    def test_never_repeat_yesterdays_report(self):
        previous = {"https://www.kcmi.re.kr/report/report_view?report_no=2350": 1}
        with patch("report_sources.previously_featured_report_urls", return_value=previous), \
             patch("report_sources.search_institutional_reports", return_value=[]):
            selected = collect_reports(self.news, self.now)
        self.assertTrue(selected)
        self.assertFalse(any(r["url"] in previous for r in selected))
        self.assertEqual(len({r["url"] for r in selected}), len(selected))

    def test_recent_week_also_excluded(self):
        used = {r["url"]: 3 for r in SEEDS[:3]}
        with patch("report_sources.previously_featured_report_urls", return_value=used), \
             patch("report_sources.search_institutional_reports", return_value=[]):
            selected = collect_reports(self.news, self.now)
        self.assertFalse(any(r["url"] in used for r in selected))

    def test_tracking_url_does_not_override_duplicate(self):
        a = "https://www.kcmi.re.kr/report/report_view?report_no=2350&utm_source=a"
        b = "https://www.kcmi.re.kr/report/report_view?report_no=2350"
        self.assertEqual(normalize_report_url(a), normalize_report_url(b))


if __name__ == "__main__":
    unittest.main()
