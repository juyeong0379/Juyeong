"""Regressions for the three KST editions, no duplicated coverage, and market relevance."""
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_briefing as briefing
from briefing_windows import (
    KST, EDITION_ORDER, edition_for_run, edition_window,
    edition_for_published, in_edition_window, in_day_window,
    MAX_STORIES_PER_CATEGORY, PER_EDITION_LIMIT,
)
from news_quality import repeated_without_update, substantive_update


class BriefingRulesTest(unittest.TestCase):
    def setUp(self):
        self.day = datetime(2026, 11, 15, 15, 20, tzinfo=KST)

    def at(self, day=15, hour=7, minute=30):
        return self.day.replace(day=day, hour=hour, minute=minute, second=0)

    def test_edition_schedule(self):
        for hour, minute, expected in [
            (7, 15, "0730"), (7, 30, "0730"), (9, 59, "0730"),
            (10, 0, "1000"), (14, 59, "1000"), (15, 0, "1500"),
            (17, 20, "1500"),
        ]:
            with self.subTest(hour=hour, minute=minute):
                self.assertEqual(edition_for_run(self.at(hour=hour, minute=minute)), expected)

    def test_exact_nonoverlapping_publication_windows(self):
        cases = [
            ("0730", self.at(14, 14, 59), False),
            ("0730", self.at(14, 15, 0), True),
            ("0730", self.at(15, 7, 30), True),
            ("0730", self.at(15, 7, 31), False),
            ("1000", self.at(15, 7, 30), False),
            ("1000", self.at(15, 7, 31), True),
            ("1000", self.at(15, 10, 0), True),
            ("1000", self.at(15, 10, 1), False),
            ("1500", self.at(15, 10, 0), False),
            ("1500", self.at(15, 10, 1), True),
            ("1500", self.at(15, 15, 0), True),
            ("1500", self.at(15, 15, 1), False),
        ]
        for edition, published, expected in cases:
            with self.subTest(edition=edition, time=published):
                self.assertEqual(
                    in_edition_window(published, self.day, edition), expected)

    def test_publication_clamped_to_actual_collection_time(self):
        current = self.at(hour=7, minute=20)
        self.assertTrue(in_edition_window(self.at(14, 16, 0), current, "0730"))
        self.assertFalse(in_edition_window(self.at(15, 7, 25), current, "0730"))

    def test_edition_identification(self):
        self.assertEqual(edition_for_published(self.at(14, 16, 0), self.day), "0730")
        self.assertEqual(edition_for_published(self.at(15, 9, 0), self.day), "1000")
        self.assertEqual(edition_for_published(self.at(15, 13, 0), self.day), "1500")
        self.assertEqual(edition_for_published(self.at(14, 14, 0), self.day), None)
        self.assertEqual(MAX_STORIES_PER_CATEGORY, 6)
        self.assertEqual(PER_EDITION_LIMIT, 2)

    def test_retention_never_uses_old_news(self):
        for published, keep in [
            (self.at(14, 14, 59), False),
            (self.at(14, 15, 0), True),
            (self.at(15, 15, 0), True),
            (self.at(15, 15, 1), False),
        ]:
            with self.subTest(published=published):
                item = {
                    "url": "https://example.org/abc",
                    "pubDate": published.strftime("%Y-%m-%d"),
                    "pubTime": published.strftime("%H:%M KST"),
                }
                self.assertEqual(briefing.is_current_day_article(item, self.day), keep)

    def test_identical_article_never_repeats(self):
        prev = {"title": "의무공개매수 제도 국회 본회의 상정", "url": "https://a.com/1"}
        self.assertTrue(repeated_without_update(prev, prev))
        renamed = {"title": "의무공개매수 제도 국회 본회의 상정", "url": "https://a.com/2"}
        self.assertTrue(repeated_without_update(renamed, prev))

    def test_confirmed_material_update_may_be_used(self):
        prev = {"title": "의무공개매수 법안 국회 상정 논의", "url": "https://a.com/1"}
        fresh = {"title": "의무공개매수 법안 국회 통과 확정", "url": "https://a.com/2"}
        self.assertTrue(substantive_update(fresh, prev))
        self.assertFalse(repeated_without_update(fresh, prev))

    def test_unconfirmed_topic_rewrite_rejected(self):
        prev = {"title": "의무공개매수 법안 도입 추진 논의", "url": "https://a.com/1"}
        new = {"title": "의무공개매수 법안 도입 추진 전망", "url": "https://a.com/2"}
        self.assertTrue(repeated_without_update(new, prev))

    def test_personnel_not_market_wide(self):
        self.assertTrue(briefing.is_personnel_only("A증권 임원 인사 단행"))
        self.assertTrue(briefing.is_personnel_only("B그룹 대표 선임"))
        self.assertFalse(briefing.is_personnel_only("증권업계 순이익 증가와 자본효율성 경쟁"))

    def test_marketwide_metrics_and_personnel_sector_rules(self):
        from sector_rules import classify_sector
        self.assertEqual(
            classify_sector("증권사 3분기 누적 순이익 비교, 업계 자본효율 경쟁"),
            "peer",
        )
        self.assertEqual(
            classify_sector("VIG, 그로쓰 전담팀 꾸린다"),
            None,
        )

    def test_repeated_last_days(self):
        old = briefing.RECENT_HEADLINES
        try:
            briefing.RECENT_HEADLINES = [
                {"title": "국민성장펀드 1호 사업 확정", "url": "https://example.org/1"}]
            self.assertTrue(briefing.repeated_last_days(
                {"title": "국민성장펀드 1호 사업 확정", "url": "https://example.org/1"}))
            self.assertFalse(briefing.repeated_last_days(
                {"title": "전력 인프라 공급망 투자 위험 확대", "url": "https://example.org/2"}))
        finally:
            briefing.RECENT_HEADLINES = old


if __name__ == "__main__":
    unittest.main()
