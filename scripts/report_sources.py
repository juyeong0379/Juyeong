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
    "growthfund": ("국민성장펀드", "성장펀드", "정책금융", "출자", "투자집행", "첨단산업"),
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
    {
        "title": "투자판단에서의 핀플루언서 의존 실태와 규제 방안",
        "source": "자본시장연구원", "date": "2026-11-11",
        "url": "https://www.kcmi.re.kr/report/report_view?report_no=2350",
        "categories": ["peer", "ecm", "sto"], "type": "이슈보고서",
    },
    {
        "title": "전망공시의 현황과 특성 및 정보가치",
        "source": "자본시장연구원", "date": "2026-10-30",
        "url": "https://www.kcmi.re.kr/report/report_view?report_no=2344",
        "categories": ["ecm", "peer", "corp"], "type": "이슈보고서",
    },
    {
        "title": "미국 사모신용시장의 건전성, 유동성 및 시스템리스크",
        "source": "자본시장연구원", "date": "2026-10-12",
        "url": "https://www.kcmi.re.kr/report/report_view?report_no=2337",
        "categories": ["corp", "ma", "peer"], "type": "이슈보고서",
    },
    {
        "title": "기술특례상장기업의 성장을 위한 코스닥시장의 과제",
        "source": "자본시장연구원", "date": "2026-11-02",
        "url": "https://www.kcmi.re.kr/publications/pub_detail_view?cno=6883&syear=2026&zcd=002001016&zno=1944",
        "categories": ["ecm", "productive", "peer"], "type": "자본시장포커스",
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
        f"site:kcmi.re.kr 최신보고서 {today.year}",
        f"site:kcmi.re.kr 자본시장포커스 {today.year}",
        f"site:bok.or.kr 금융시장 동향 {today.year}",
        f"site:bok.or.kr 이슈노트 {today.year}",
        f"site:kif.re.kr 금융브리프 {today.year}",
        f"site:kdi.re.kr 경제동향 {today.year}",
    ]
    headers = {"X-NCP-APIGW-API-KEY-ID": key, "X-NCP-APIGW-API-KEY": secret}
    found = []
    for query in queries:
        try:
            res = requests.get(url, headers=headers, params={"query": query, "display": 35}, timeout=5)
            if res.status_code in (401, 403):
                print("  [자료 검색] 기관 문서 검색 API 미승인: 후속 무효 요청 중단, 검증된 기존 보고서만 사용")
                break
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

BRIDGES = {
    "금융시장": ("회사채", "채권", "금리", "기업대출", "DCM", "발행", "자금조달"),
    "임팩트투자": ("벤처", "모험자본", "유니콘", "성장펀드", "정책펀드", "투자금융"),
    "AI 산업": ("데이터센터", "GPU", "AI", "인공지능", "전력"),
    "토큰화": ("토큰증권", "STO", "RWA", "장외거래", "증권형"),
}

def _topic_terms(report_title):
    terms = [w for w in re.findall(r"[가-힣]{2,}|[a-zA-Z]{2,}", report_title)
             if len(w) > 2 and w not in ("보고서", "특징과", "주요", "동향과",
                                         "2026년", "2026", "10월", "금융의", "국내")]
    for label, related in BRIDGES.items():
        if label.casefold() in report_title.casefold():
            terms += list(related)
    return terms

def previously_featured_report_urls(now, days=30):
    """Collect report URLs by actual briefing date to prevent yesterday repeats."""
    import json
    from pathlib import Path

    archive = Path(__file__).resolve().parents[1] / "docs" / "archive"
    history = {}
    for offset in range(1, days + 1):
        day = (now.date() - timedelta(days=offset)).isoformat()
        path = archive / (day + ".json")
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        for report in payload.get("reports", []):
            if not isinstance(report, dict):
                continue
            url = report.get("url", "").strip()
            if url and url not in history:
                history[url] = offset
    return history


def normalize_report_url(url):
    """Match the canonical page URL, ignoring tracking fragments."""
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    parts = urlsplit(str(url or ""))
    params = [(k, v) for k, v in parse_qsl(parts.query) if not k.startswith("utm_")]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                       parts.path.rstrip("/"), urlencode(params), ""))


def collect_reports(categories, now):
    """Two different verified reports/day; never repeat the previous briefing."""
    today = now.date()
    prior = previously_featured_report_urls(now)
    prior_normalized = {normalize_report_url(u): age for u, age in prior.items()}
    candidates = SEEDS + search_institutional_reports(today)
    scored = []
    for report in candidates:
        url = str(report.get("url", "")).strip()
        source = host_source(url)
        if source is None:
            continue
        try:
            age = (today - datetime.strptime(report["date"], "%Y-%m-%d").date()).days
        except (KeyError, ValueError, TypeError):
            continue
        if not 0 <= age <= 60:
            continue
        recent_use = prior_normalized.get(normalize_report_url(url), 999)
        if recent_use == 1:
            # A report must be visibly different from yesterday, regardless
            # of whether it is the newest or previously highly scored.
            continue
        if recent_use <= 7:
            # Prefer genuinely unfeatured research across the full week.
            continue

        terms = _topic_terms(report["title"])
        matched_urls = []
        overlap = 0
        for key in report.get("categories", []):
            for story in categories.get(key, []):
                article_text = " ".join(
                    [story.get("headline", "")] +
                    [str(x).replace("**", "") for x in story.get("summary", [])]
                )
                matched = [term for term in terms if term.casefold() in article_text.casefold()]
                if matched and story.get("url"):
                    matched_urls.append(story["url"])
                    overlap += min(3, len(matched))
        if not matched_urls:
            # Research is a distinct section, not a fake news article.
            # A source-verified research item can still be useful on a sparse
            # news day, provided its publication is recent and relevant to IB.
            if age > 30:
                continue
        score = 85 - age + min(25, 5 * overlap)
        if not matched_urls:
            score -= 8
        if recent_use <= 30:
            score -= 20  # avoid repeatedly selecting same older publication
        scored.append((score, report, sorted(set(matched_urls))))

    scored.sort(key=lambda x: x[0], reverse=True)
    chosen = []
    used = set()
    for _, report, urls in scored:
        url = normalize_report_url(report["url"])
        if url in used:
            continue
        used.add(url)
        chosen.append({
            "id": "report-" + str(len(chosen) + 1),
            "title": report["title"], "source": report["source"],
            "date": report["date"], "url": report["url"],
            "type": report["type"], "related_article_urls": urls,
        })
        if len(chosen) == 2:
            break
    print(f"  [기관자료 교체] 최근 7일 사용자료 제외, 당일 신규 추천 {len(chosen)}건")
    return chosen
