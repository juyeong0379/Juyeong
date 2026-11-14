"""Regression tests for one daily 07:30 IB executive briefing."""
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import generate_briefing as briefing
from briefing_windows import (
    KST, EDITION_ORDER, EDITION_LABELS, edition_for_run,
    edition_window, edition_for_published, in_edition_window, in_day_window,
    MAX_STORIES_PER_CATEGORY, PER_EDITION_LIMIT,
)
from news_quality import repeated_without_update, substantive_update


class BriefingRulesTest(unittest.TestCase):
    def setUp(self):
        self.day = datetime(2026, 11, 15, 8, 0, tzinfo=KST)

    def at(self, day=15, hour=7, minute=30, second=0):
        return self.day.replace(day=day, hour=hour, minute=minute, second=second)

    def test_single_daily_edition(self):
        self.assertEqual(EDITION_ORDER, ("0730",))
        self.assertEqual(EDITION_LABELS["0730"], "07:30")
        for hour in [0, 6, 7, 8, 10, 15, 23]:
            self.assertEqual(edition_for_run(self.at(hour=hour)), "0730")
        self.assertEqual(MAX_STORIES_PER_CATEGORY, 3)
        self.assertEqual(PER_EDITION_LIMIT, 3)

    def test_previous_0730_until_today_0730_strict(self):
        start, end = edition_window(self.day, "0730")
        self.assertEqual(start, self.at(14, 7, 30))
        self.assertEqual(end, self.at(15, 7, 30))
        cases = [
            (self.at(14, 7, 29), False),
            (self.at(14, 7, 30), False),  # previous day's cutoff belongs to yesterday
            (self.at(14, 7, 30, 1), True),
            (self.at(14, 7, 31), True),
            (self.at(14, 15, 0), True),
            (self.at(15, 7, 29), True),
            (self.at(15, 7, 30), True),
            (self.at(15, 7, 31), False),
            (self.at(15, 14, 0), False),
        ]
        for published, expected in cases:
            with self.subTest(published=published):
                self.assertEqual(in_edition_window(published, self.day), expected)
                self.assertEqual(in_day_window(published, self.day), expected)

    def test_delayed_run_does_not_include_post_0730_news(self):
        late = self.at(hour=10, minute=0)
        self.assertTrue(in_edition_window(self.at(hour=7, minute=30), late))
        self.assertFalse(in_edition_window(self.at(hour=7, minute=31), late))
        self.assertEqual(edition_for_published(self.at(hour=8, minute=0), late), None)

    def test_early_manual_run_does_not_include_future_articles(self):
        early = self.at(hour=7, minute=20)
        self.assertTrue(in_edition_window(self.at(14, 20, 0), early))
        self.assertFalse(in_edition_window(self.at(hour=7, minute=25), early))

    def test_retained_archive_uses_only_this_24h(self):
        cases = [
            (self.at(14, 7, 30), False),
            (self.at(14, 7, 31), True),
            (self.at(15, 7, 30), True),
            (self.at(15, 7, 31), False),
        ]
        for published, expected in cases:
            with self.subTest(published=published):
                article = {
                    "url": "https://example.org/real-story",
                    "pubDate": published.strftime("%Y-%m-%d"),
                    "pubTime": published.strftime("%H:%M KST"),
                }
                self.assertEqual(
                    briefing.is_current_day_article(article, self.day), expected)

    def test_exact_seconds_are_preserved_at_start_boundary(self):
        actual = self.at(14, 7, 30, 1)
        article = {
            "url": "https://example.org/new-development",
            "pubDate": "2026-11-14",
            "pubTime": "07:30 KST",
            "publishedAt": actual.isoformat(),
        }
        self.assertEqual(briefing.article_timestamp(article), actual)
        self.assertTrue(briefing.is_current_day_article(article, self.day))
        article["publishedAt"] = self.at(14, 7, 30).isoformat()
        self.assertFalse(briefing.is_current_day_article(article, self.day))

    def test_identical_article_or_same_issue_rejected(self):
        original = {"title": "의무공개매수 법안 국회 본회의 상정", "url": "https://a.com/1"}
        self.assertTrue(repeated_without_update(original, original))
        renamed = {"title": original["title"], "url": "https://a.com/2"}
        self.assertTrue(repeated_without_update(renamed, original))

    def test_confirmed_significant_update_exception(self):
        prev = {"title": "의무공개매수 법안 국회 상정 논의", "url": "https://a.com/1"}
        newer = {"title": "의무공개매수 법안 국회 통과 확정", "url": "https://a.com/2"}
        self.assertTrue(substantive_update(newer, prev))
        self.assertFalse(repeated_without_update(newer, prev))

    def test_unconfirmed_rehash_blocked(self):
        prev = {"title": "의무공개매수 법안 도입 추진 논의", "url": "https://a.com/1"}
        guess = {"title": "의무공개매수 법안 도입 추진 전망", "url": "https://a.com/2"}
        self.assertTrue(repeated_without_update(guess, prev))

    def test_market_wide_metrics_not_personnel(self):
        from sector_rules import classify_sector
        self.assertTrue(briefing.is_personnel_only("A증권 임원 인사 단행"))
        self.assertFalse(briefing.is_personnel_only("증권업계 순이익 증가와 자본효율성 경쟁"))
        self.assertEqual(classify_sector("증권사 3분기 누적 순이익 비교, 업계 자본효율 경쟁"), "peer")
        self.assertIsNone(classify_sector("VIG, 그로쓰 전담팀 꾸린다"))

    def test_jensen_huang_market_shock_goes_to_other_not_ecm(self):
        from sector_rules import classify_sector
        headline = "'젠슨황 스캔들'에 짓눌린 증시…엔비디아 실적·앤트로픽 상장 주시"
        desc = "엔비디아 전 CEO 사임 이후 미국 반도체 시장·AI 투자 불확실성"
        self.assertEqual(classify_sector(headline, desc), "other")
        self.assertFalse(briefing.is_personnel_only(headline))

    def test_previous_briefing_dedup(self):
        before = briefing.RECENT_HEADLINES
        try:
            briefing.RECENT_HEADLINES = [
                {"title": "국민성장펀드 1호 사업 확정", "url": "https://example.org/1"}]
            self.assertTrue(briefing.repeated_last_days(
                {"title": "국민성장펀드 1호 사업 확정", "url": "https://example.org/1"}))
            self.assertFalse(briefing.repeated_last_days(
                {"title": "전력 인프라 공급망 투자 위험 확대", "url": "https://example.org/2"}))
        finally:
            briefing.RECENT_HEADLINES = before


if __name__ == "__main__":
    unittest.main()
