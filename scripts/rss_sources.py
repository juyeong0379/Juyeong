"""Collect genuine source-published news through verified publisher RSS feeds.

Source RSS complements Naver API search. It does not bypass publication-time
checks, original-source URL verification, sector matching, factual summaries,
or duplicate filtering. Fetched only once per job, then reused by 11 sections.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from email.utils import parsedate_to_datetime
from functools import lru_cache
from urllib.parse import urlparse
from xml.etree import ElementTree as ET
import html
import re

import requests

RSS_FEEDS = (
    "https://www.hankyung.com/feed/finance",
    "https://www.hankyung.com/feed/economy",
    "https://www.hankyung.com/feed/realestate",
    "https://www.hankyung.com/feed/international",
    "https://www.hankyung.com/feed/it",
    "https://www.yna.co.kr/rss/economy.xml",
    "https://www.yna.co.kr/rss/industry.xml",
    "https://www.yna.co.kr/rss/market.xml",
    "http://rss.edaily.co.kr/edaily_news.xml",
)

TRUSTED_HOSTS = ("hankyung.com", "yna.co.kr", "edaily.co.kr")
XML_DATE = ("pubDate", "published", "updated")


def _clean(value):
    value = html.unescape(str(value or ""))
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _child_text(item, name):
    for child in item:
        if child.tag.rsplit("}", 1)[-1] == name:
            return (child.text or "").strip()
    return ""


def _parse_date(value):
    if not value:
        return None
    try:
        date = parsedate_to_datetime(value)
        if date and date.tzinfo is not None:
            return date
    except (TypeError, ValueError):
        pass
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is not None:
            return date
    except (TypeError, ValueError):
        pass
    return None


def _trusted_story_link(url):
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in ("https", "http"):
        return False
    host = (parsed.hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in TRUSTED_HOSTS)


def parse_rss(content, feed_url):
    """Only original publisher URLs with a real timezone-aware publication time."""
    root = ET.fromstring(content)
    articles = []
    for elem in root.iter():
        if elem.tag.rsplit("}", 1)[-1] not in ("item", "entry"):
            continue
        url = _child_text(elem, "link")
        if not url:
            for child in elem:
                if child.tag.rsplit("}", 1)[-1] == "link":
                    url = child.get("href", "")
                    if url:
                        break
        if not _trusted_story_link(url):
            continue
        title = _clean(_child_text(elem, "title"))
        desc = _clean(_child_text(elem, "description") or
                      _child_text(elem, "summary") or
                      _child_text(elem, "content"))
        date = _parse_date(next((_child_text(elem, key)
                                 for key in XML_DATE if _child_text(elem, key)), ""))
        if not title or date is None:
            continue
        articles.append({
            "title": title, "description": desc[:650],
            "url": url.strip(), "pub_dt": date,
            "source": "한국경제" if "hankyung" in url else
                      ("연합뉴스" if "yna.co.kr" in url else "이데일리"),
            "reporter": "", "matched_keyword": "publisher_rss",
        })
    return articles


def _fetch(url):
    try:
        response = requests.get(
            url, timeout=6, headers={"User-Agent": "Mozilla/5.0 IBNewsBrief/1.0"},
        )
        response.raise_for_status()
        return parse_rss(response.content, url)
    except (requests.RequestException, ET.ParseError) as exc:
        print(f"  [RSS 검색] {url}: {type(exc).__name__} (네이버 검색 계속)")
        return []


@lru_cache(maxsize=1)
def recent_publisher_news():
    """Eight trusted publisher feeds checked concurrently; no news invented."""
    found = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        tasks = [pool.submit(_fetch, url) for url in RSS_FEEDS]
        for task in as_completed(tasks):
            found.extend(task.result())
    unique = {}
    for article in found:
        unique.setdefault(article["url"], article)
    print(f"  [추가 원천] 국내 경제지/통신사 RSS {len(RSS_FEEDS)}개 -> {len(unique)}건")
    return list(unique.values())
