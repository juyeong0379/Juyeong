"""Regression tests for the rolling KST IB briefing."""
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_briefing as briefing


class BriefingRulesTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 15, 7, 10, tzinfo=briefing.KST)

    def test_morning_and_afternoon_limits(self):
        for hour, expected in [(6, 2), (9, 2), (11, 2), (13, 3), (15, 4), (17, 4)]:
            with self.subTest(hour=hour):
                self.assertEqual(
                    briefing.target_story_count(self.now.replace(hour=hour)),
                    expected)

    def test_strict_publication_window_and_timestamp(self):
        cases = [
            ("2026-11-14", "19:59 KST", False),
            ("2026-11-14", "20:00 KST", True),
            ("2026-11-15", "06:45 KST", True),
            ("2026-11-15", "07:11 KST", False),
            ("2026-11-13", "23:00 KST", False),
            ("2026-11-15", "", False),
        ]
        for date, time, accepted in cases:
            with self.subTest(date=date, time=time):
                article = {
                    "url": "https://news.example.com/story",
                    "pubDate": date, "pubTime": time,
                }
                self.assertEqual(
                    briefing.is_current_day_article(article, self.now),
                    accepted)

    def test_no_yesterday_reuse_by_url_or_near_identical_headline(self):
        previous = briefing.RECENT_HEADLINES
        try:
            briefing.RECENT_HEADLINES = [{
                "url": "https://news.example.com/one",
                "title": "의무공개매수 법안 본회의 통과 추진",
            }]
            self.assertTrue(briefing.repeated_last_days({
                "url": "https://news.example.com/one",
                "title": "다른 제목",
            }))
            self.assertTrue(briefing.repeated_last_days({
                "url": "https://news.example.com/two",
                "title": "의무공개매수 법안 본회의 통과 추진",
            }))
            self.assertFalse(briefing.repeated_last_days({
                "url": "https://news.example.com/three",
                "title": "풍력 프로젝트 금융약정 신규 체결",
            }))
        finally:
            briefing.RECENT_HEADLINES = previous


if __name__ == "__main__":
    unittest.main()
