"""Select up to two recent institutional research publications related to IB news.

Only institution-owned URLs with an explicit publication date are considered.
No invented publications, dates, or PDF download links.
"""
import os
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse

import requests

TRUSTED = {
    "kcmi.re.kr": "자본시장연구원",
    "bok.or.kr": "한국은행",
    "kif.re.kr": "한국금융연구원",
    "kdi.re.kr": "한국개발연구원",
    "kiet.re.kr": "산업연구원",
    "kiri.or.kr": "보험연구원",
    "fsc.go.kr": "금융위원회",
    "korea.kr": "대한민국 정책브리핑",
}
REPORT_WORDS = re.compile(r"보고서|이슈|리서치|금융시장 동향|연구|분석|전망|브리프|포커스|정책자료")
NO_REPORT = re.compile(r"블로그|카페|위키|뉴스기사|연구원 소개|공지사항|채용|홈페이지|검색결과|전체보기|정책브리핑 홈페이지")
DATE = re.compile(r"(20\d{2})[\s./년-]+(0?[1-9]|1[0-2])[\s./월-]+([0-2]?\d|3[01])")
GROUPS = {
    "ecm": ("IPO", "공모", "상장", "주식시장", "메자닌", "벤처", "자본시장"),
    "corp": ("회사채", "기업금융", "금융시장", "신용", "기업대출", "자금조달", "채권"),
    "ma": ("인수금융", "M&A", "사모", "PEF", "기업금융", "회수", "벤처"),
    "re": ("부동산", "PF", "리츠", "주택", "금융시장"),
    "infra": ("인프라", "AI", "데이터센터", "전력", "자금조달", "산업"),
    "hana": ("기업금융", "금융시장", "생산적", "은행", "증권"),
    "peer": ("IB", "증권사", "자본시장", "사모", "기업금융"),
    "sto": ("토큰", "디지털", "STO", "증권", "금융인프라"),
    "productive": ("투자", "벤처", "성장", "산업", "모험자본", "생산적", "금융"),
}
# Existing verified institutional report pages. They remain eligible only
# while sufficiently recent and relevant to the current day's news.
SEEDS = [
    {
        "title": "2026년 10월 금융시장 동향",
        "source": "한국은행",
        "date": "2026-11-11",
        "url": "https://www.bok.or.kr/portal/bbs/P0002017/view.do?menuNo=200365&nttId=11066469",
        "categories": ["corp", "ma", "peer"],
        "type": "금융시장 보고서",
    },
    {
        "title": "AI 산업 자금조달의 주요 특징과 리스크 평가",
        "source": "한국은행",
        "date": "2026-10-27",
        "url": "https://www.bok.or.kr/portal/bbs/B0000565/view.do?menuNo=201757&nttId=11065947",
        "categories": ["infra", "corp", "productive"],
        "type": "국제경제이슈",
    },
    {
        "title": "임팩트투자 생태계 현황과 과제",
        "source": "자본시장연구원",
        "date": "2026-10-26",
        "url": "https://www.kcmi.re.kr/report/report_view?report_no=2341",
        "categories": ["productive", "ma"],
        "type": "조사보고서",
    },
    {
        "title": "K-자본시장 정책시리즈 2: 자본시장 토큰화의 확산: 해외 주요 사례와 국내 도입 과제",
        "source": "자본시장연구원",
        "date": "2026-10-06",
        "url": "https://www.kcmi.re.kr/report/report_view?report_no=2336",
        "categories": ["sto"],
        "type": "이슈보고서",
    },
]

def host_source(url):
    host = (urlparse(url).hostname or "").lower()
    for domain, source in TRUSTED.items():
        if host == domain or host.endswith("." + domain):
            return source
    return None

def strip_tags(s):
    import html
    return html.unescape(re.sub(r"<[^>]*>", "", s or "")).strip()

def publication_date(text, today):
    for match in DATE.finditer(text):
        try:
            d = datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).date()
            if today - timedelta(days=60) <= d <= today:
                return d.isoformat()
        except ValueError:
            pass
    return None

def search_institutional_reports(today):
    """Naver API HUB web-document search; unlike news, covers research websites."""
    key = os.getenv("NAVER_CLIENT_ID")
    secret = os.getenv("NAVER_CLIENT_SECRET")
    if not key or not secret:
        return []
    url = "https://naverapihub.apigw.ntruss.com/search/v1/webkr"
    queries = [
        "site:kcmi.re.kr 최신보고서 2026",
        "site:kcmi.re.kr 자본시장포커스 2026",
        "site:bok.or.kr 금융시장 동향 2026",
        "site:bok.or.kr 이슈노트 2026",
        "site:kif.re.kr 금융브리프 2026",
        "site:kdi.re.kr 경제동향 2026",
    ]
    headers = {"X-NCP-APIGW-API-KEY-ID": key, "X-NCP-APIGW-API-KEY": secret}
    found = []
    for query in queries:
        try:
            res = requests.get(url, headers=headers, params={"query": query, "display": 35}, timeout=8)
            res.raise_for_status()
            items = res.json().get("items", [])
        except Exception as exc:
            print(f"  [자료 검색] {query}: {exc}")
            continue
        for row in items:
            title = strip_tags(row.get("title", ""))
            desc = strip_tags(row.get("description", ""))
            link = row.get("link", "").strip()
            source = host_source(link)
            if not source or not link.startswith("https://") or not REPORT_WORDS.search(title):
                continue
            if NO_REPORT.search(title) or len(title) < 8:
                continue
            path = urlparse(link).path.lower()
            if path in ("", "/", "/main", "/main.do", "/index.do") or "/search" in path:
                continue
            # Search API does not give publication dates: require visible explicit date.
            date = publication_date(title + " " + desc, today)
            if not date:
                continue
            categories = [key for key, terms in GROUPS.items()
                          if sum(1 for term in terms if term.casefold() in (title + " " + desc).casefold()) >= 2]
            if not categories:
                continue
            found.append({"title": title, "source": source, "date": date,
                          "url": link, "categories": categories, "type": "기관 보고서"})
    return found

def collect_reports(categories, now):
    """Up to 2 distinct current/recent research sources, linked to actual news URLs."""
    today = now.date()
    candidates = SEEDS + search_institutional_reports(today)
    scored = []
    for r in candidates:
        try:
            day = datetime.strptime(r["date"], "%Y-%m-%d").date()
        except (ValueError, KeyError):
            continue
        age = (today - day).days
        if age < 0 or age > 45:  # Relevance should not justify stale materials.
            continue
        matched = []
        texts = []
        for key in r["categories"]:
            for story in categories.get(key, []):
                url = story.get("url")
                if url:
                    matched.append(url)
                    texts.append(story.get("headline", ""))
        if not matched:
            continue
        words = re.findall(r"[가-힣]{2,}|[a-zA-Z]{2,}", r["title"])
        keywords = [word for word in words if len(word) > 2 and word not in ("보고서", "특징과", "주요", "동향과")]
        overlap = sum(any(word.casefold() in title.casefold() for title in texts) for word in keywords)
        # Require a specific theme overlap, or same category with a strong topic keyword.
        theme_match = overlap > 0
        if not theme_match:
            continue
        score = (50 - age) + min(25, 7 * overlap)
        scored.append((score, r, matched))
    scored.sort(key=lambda x: x[0], reverse=True)
    chosen, used = [], set()
    for _, r, urls in scored:
        if r["url"] in used:
            continue
        used.add(r["url"])
        chosen.append({
            "id": "report-" + str(len(chosen)+1),
            "title": r["title"],
            "source": r["source"],
            "date": r["date"],
            "url": r["url"],
            "type": r["type"],
            "related_article_urls": sorted(set(urls)),
        })
        if len(chosen) == 2:
            break
    return chosen
