"""Offline tests for trusted original-publisher RSS article discovery."""
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from briefing_windows import KST, in_edition_window
from rss_sources import parse_rss


class RSSSourceTest(unittest.TestCase):
    def test_original_source_and_timestamp_required(self):
        rss = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel>
          <item><title>증권사 IPO 공모시장 경쟁 확대</title>
            <link>https://www.hankyung.com/article/202611150101</link>
            <description>코스닥 신규 공모가와 기관수요</description>
            <pubDate>Sat, 14 Nov 2026 23:00:00 +0900</pubDate></item>
          <item><title>SEO spam</title>
            <link>https://example.org/story</link>
            <pubDate>Sat, 14 Nov 2026 23:01:00 +0900</pubDate></item>
          <item><title>No publication date</title>
            <link>https://www.hankyung.com/article/other</link></item>
        </channel></rss>"""
        articles = parse_rss(rss, "https://www.hankyung.com/feed/finance")
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["source"], "한국경제")
        self.assertIn("IPO", articles[0]["title"])
        self.assertTrue(in_edition_window(
            articles[0]["pub_dt"],
            datetime(2026, 11, 15, 8, 0, tzinfo=KST)
        ))

    def test_previous_cutoff_and_later_stories_excluded(self):
        rss = """<rss><channel>
           <item><title>전날 기한 이전 기사</title>
             <link>https://www.yna.co.kr/view/AKR0001</link>
             <pubDate>Sat, 14 Nov 2026 07:29:00 +0900</pubDate></item>
           <item><title>오전 8시 이후 기사</title>
             <link>https://www.yna.co.kr/view/AKR0002</link>
             <pubDate>Sun, 15 Nov 2026 08:00:00 +0900</pubDate></item>
        </channel></rss>"""
        articles = parse_rss(rss, "https://www.yna.co.kr/rss/economy.xml")
        now = datetime(2026, 11, 15, 9, 0, tzinfo=KST)
        self.assertEqual(len(articles), 2)
        self.assertFalse(any(in_edition_window(row["pub_dt"], now) for row in articles))


if __name__ == "__main__":
    unittest.main()
