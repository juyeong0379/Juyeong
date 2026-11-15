"""
IB그룹 데일리 뉴스 모니터링 자동 생성 스크립트
==================================================

동작 순서
  1) 섹션별 키워드로 네이버 뉴스 검색 API를 호출해 후보 기사를 모은다.
     - 메이저 언론사(경제지·IB 전문지·종합지) 기사만 남긴다.
     - [속보]·[포토]·시황·특징주 같은 단신은 제목 단계에서 제외한다.
  2) 후보 기사를 OpenAI API에 10개씩 보내, 섹션 기준에 맞는지 판단하고
     개조식 요약·기자명·점수를 받는다.
  3) 최종 점수 = 시의성 15% + 중요성 35% + 심층도 35% + 퀄리티 15%
     심층도가 낮은 기사는 탈락. 섹션별 상위 N개만 남긴다.
     (기사 하나는 한 섹션에만 들어간다)
  4) 프론트엔드(docs/index.html)가 읽을 docs/data.json 을 생성한다.

필요한 환경변수
  - NAVER_CLIENT_ID       (NAVER API HUB의 X-NCP-APIGW-API-KEY-ID)
  - NAVER_CLIENT_SECRET   (NAVER API HUB의 X-NCP-APIGW-API-KEY)
  - OPENAI_API_KEY       (platform.openai.com에서 발급한 GitHub Secret)
"""

import os
import re
import json
import html
import time
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
from openai import OpenAI, AuthenticationError, PermissionDeniedError, RateLimitError
from sector_rules import classify_sector, sector_matches
from article_digest import digest, preview_digest
from news_quality import duplicate, validate_digest, deduplicate, numeric_claims_grounded, repeated_without_update
from news_materiality import is_material_news
from insight_policy import accept_insight
from report_sources import collect_reports
from rss_sources import recent_publisher_news
from briefing_windows import (edition_for_run, edition_window, in_edition_window,
                              in_day_window, edition_for_published,
                              EDITION_LABELS, EDITION_ORDER, PER_EDITION_LIMIT,
                              MAX_STORIES_PER_CATEGORY)

KST = timezone(timedelta(hours=9))
MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
BATCH_SIZE = 8

TOP_N_PER_SECTION = PER_EDITION_LIMIT  # 오전 07:30 일괄 브리핑 카테고리별 최대 3건
NAVER_PER_KEYWORD = 100    # 검색어당 최근 100건까지 확인해 24시간 기사 누락 축소
MAX_CANDIDATES = 30        # 최근 24시간의 심층 후보 검토 (선정 점수 기준 유지)
MIN_DEPTH = 67
MIN_QUALITY = 68
MIN_IMPORTANCE = 70
RECENT_HEADLINES = []
# These strategy desk categories should cover verified institutional actions,
# policy developments and concrete funding outcomes, not only systemic shocks.
RELAXED_SECTIONS = {"hana", "peer", "sto", "productive", "growthfund"}
PREFERRED_IB_OUTLETS = {"더벨", "인베스트조선", "딜사이트", "한국경제", "한국금융신문", "법률신문", "딜북"}             # 심층도가 이보다 낮으면 탈락

# ─────────────────────────────────────────────────────────────
# 메이저 언론사 (도메인 → 화면에 표시할 이름)
# ─────────────────────────────────────────────────────────────
KR_OUTLETS = {
    # IB·자본시장 전문지
    "thebell.co.kr": "더벨",
    "investchosun.com": "인베스트조선",
    "dealsite.co.kr": "딜사이트",
    "dealsitetv.com": "딜사이트경제TV",
    "paxnetnews.com": "팍스넷뉴스",
    "dealbook.co.kr": "딜북",
    "dealbook.kr": "딜북",
    "dealbooknews.com": "딜북",
    # 경제지
    "hankyung.com": "한국경제",
    "mk.co.kr": "매일경제",
    "sedaily.com": "서울경제",
    "mt.co.kr": "머니투데이",
    "edaily.co.kr": "이데일리",
    "fnnews.com": "파이낸셜뉴스",
    "asiae.co.kr": "아시아경제",
    "heraldcorp.com": "헤럴드경제",
    "biz.chosun.com": "조선비즈",
    "einfomax.co.kr": "연합인포맥스",
    "bizwatch.co.kr": "비즈워치",
    "businesspost.co.kr": "비즈니스포스트",
    "etnews.com": "전자신문",
    "newspim.com": "뉴스핌",
    "ddaily.co.kr": "디지털데일리",
    "fntimes.com": "한국금융신문",
    "lawtimes.co.kr": "법률신문",
    "economist.co.kr": "이코노미스트",
    "hankyungbiz.com": "한경비즈니스",
    "bloter.net": "블로터",
    "news1.kr": "뉴스1",
    "newsis.com": "뉴시스",
    "ajunews.com": "아주경제",
    # 종합지·통신
    "chosun.com": "조선일보",
    "joongang.co.kr": "중앙일보",
    "donga.com": "동아일보",
    "hani.co.kr": "한겨레",
    "khan.co.kr": "경향신문",
    "hankookilbo.com": "한국일보",
    "yna.co.kr": "연합뉴스",
}

# 제목에 이런 표현이 있으면 단신/시황/보도자료로 보고 제외
SHORT_NEWS_PATTERN = re.compile(
    r"\[(포토|사진|영상|그래픽|속보|1보|2보|인사|부고|게시판|알림|표|공시|주요\s*공시|오늘의[^\]]*|"
    r"개장\s*시황|마감\s*시황|장중\s*시황|시황|날씨|운세|카드뉴스|신간|머니무브|증시캘린더)\]"
    r"|특징주|주요\s*일정|마감\s*시황|개장\s*시황",
)


def outlet_name(url: str):
    """URL의 도메인이 목록에 있으면 언론사 이름을, 없으면 None을 돌려준다."""
    host = re.sub(r"^https?://", "", url or "").split("/")[0].lower().split(":")[0]
    if host.startswith(("sports.", "entertain.", "m.sports.", "m.entertain.")):
        return None
    for domain in sorted(KR_OUTLETS, key=len, reverse=True):  # biz.chosun.com 우선
        if host == domain or host.endswith("." + domain):
            return KR_OUTLETS[domain]
    return None


# ─────────────────────────────────────────────────────────────
# 섹션 정의 (IB그룹 데일리 뉴스 모니터링 구성)
#   딕셔너리 순서 = 화면 탭 순서
# ─────────────────────────────────────────────────────────────
SECTIONS = {
    "ecm": {
        "no": "01", "emoji": "📈", "label": "ECM",
        "desc": "IPO·유상증자·메자닌·블록딜",
        "keywords": ["엠비디", "엠비디 공모주", "IPO", "공모주", "기업공개", "코스닥 상장", "청약 경쟁률", "상장 주관사", "공모가", "증권신고서", "수요예측", "유상증자", "IPO 주식시장", "IPO 기관 수요예측", "중복상장 기업가치", "구주매출 IPO", "중복상장 규제", "중복상장 자회사 IPO", "자회사 상장 주주동의", "IPO 수요예측", "상장예비심사", "코너스톤 투자자", "상장심사 소송", "공모가 산정", "유상증자 주관", "전환사채 발행", "블록딜 매각"],
        "criteria": "IPO·ECM 공모주 청약 흥행/부진, 확정 공모가와 밴드 대비 할인, 유통물량·구주매출, 투자자 확약, 대표주관사 수임 경쟁, 코스닥 상장 성과, 중복상장·코너스톤 등 규제·심사 변화. 특히 엠비디와 하나증권 주관 IPO처럼 대표 사례에서 가격·수요·회수 시사점이 확인되면 선정. 기업 1곳의 단순 일정 나열은 별도 IPO 일정에만 기록하고 기사로는 24시간 내 발행됐으며 구체적 데이터/주관 경쟁/시장 추세가 있는 기사를 우대.",
    },
    "corp": {
        "no": "02", "emoji": "🏦", "label": "기업금융",
        "desc": "DCM·회사채·기업자금 조달",
        "keywords": ["회사채 기업금융", "딤섬본드 외화채", "회사채 금리", "채권 발행구조", "회사채 수요예측", "DCM 주관", "기업 자금조달", "기업 신용등급", "회사채 차환", "신디케이트론", "PRS 조달"],
        "criteria": "회사채·여전채·DCM, 기업 자금조달과 리파이낸싱. 구체적인 발행금액·만기·스프레드·신용등급·주관사 등 맥락 중심. 인수합병·인수금융과 부동산PF는 제외.",
    },
    "ma": {
        "no": "03", "emoji": "🤝", "label": "M&A·인수금융",
        "desc": "M&A·경영권 매각·PEF·인수금융",
        "keywords": ["M&A 기업매각", "인수금융 만기 연장", "금융회사 인수", "대기업 인수합병", "인수금융 주선", "기업 인수합병", "M&A 공정거래 심사", "경영권 매각", "PEF 바이아웃", "M&A 매각 자문", "인수금융 리파이낸싱", "기업 인수 매각"],
        "criteria": "기업 인수합병, 경영권 거래, 사모펀드 투자·회수, 인수금융 조달 및 차환. 실제 거래 구조·금액·참여자와 사업적 영향을 구체적으로 다룬 기사.",
    },
    "re": {
        "no": "04", "emoji": "🏢", "label": "부동산",
        "desc": "부동산PF·리츠·상업용부동산",
        "keywords": ["부동산 PF 금융", "PF보증 심사", "정비사업비 대출", "부동산 PF 정책", "부동산 PF", "브릿지론 차환", "부동산 리파이낸싱", "부동산PF 책임준공", "부동산 PF 연대보증", "리츠 환헤지", "오피스 빌딩 매각", "물류센터 매각", "오피스 리츠", "PF 사업장 정상화", "부동산 PF 주선", "부동산 펀드 차환"],
        "criteria": "부동산PF, 브릿지론·본PF, 리츠, 상업용 부동산 투자·차환과 위험관리. 일반 아파트 청약·시세·매물 정보는 제외.",
    },
    "infra": {
        "no": "05", "emoji": "⚡", "label": "인프라",
        "desc": "데이터센터·전력·재생에너지·민자사업",
        "keywords": ["데이터센터 투자 금융", "데이터센터 신규 투자유치", "에너지 인프라 펀드", "전력망 PF", "데이터센터 PF", "GPU 파이낸싱", "인프라 금융", "해상풍력 금융", "발전 프로젝트 금융", "민자사업 금융", "인프라 신디케이트론"],
        "criteria": "데이터센터, 전력, 해상풍력·재생에너지, 도로·철도 등 프로젝트금융·인프라 투자와 자금조달. 기술 동향만 다루는 기사 제외.",
    },
    "hana": {
        "no": "06", "emoji": "🏛️", "label": "하나금융그룹",
        "desc": "하나금융지주·하나은행·하나증권 IB",
        "keywords": ["하나금융", "하나증권", "하나은행", "하나금융그룹", "하나증권", "하나금융", "하나은행 기업금융", "하나증권 IB 인수금융", "하나은행 하나증권 공동주선", "하나금융 생산적금융 성과", "하나금융 주주환원 IR", "하나증권 투자금융", "하나금융 녹색금융", "하나은행 건전성 대손비용", "하나증권 인프라 투자", "하나금융 그룹 협업"],
        "criteria": "하나금융그룹 관련 뉴스를 균형 있게 선별. 좋은 이야기 2건: 실제 기업금융·투자·인수금융 주선, 그룹 ONE-IB 협업, 생산적금융 공급, IR·주주환원·투자자 소통의 주요 행사 및 전략 성과. 개선 과제 1건: 대손·충당금·자본한도·유동성·건전성·실적 부담 등 객관적 지표가 담긴 비판적 분석. 가능하면 긍정 2+과제 1 구성이되 사실이 없으면 빈자리 유지. 일반 기부·봉사·단순 홍보행사, 소액 거래, 인사 단신 제외.",
    },
    "peer": {
        "no": "07", "emoji": "🏙️", "label": "경쟁사",
        "desc": "국내외 금융사 IB 전략·주선 경쟁",
        "keywords": ["증권사", "금융투자업계", "발행어음", "IMA", "증권사 IB 사업", "증권사 건전성", "증권사 IB 수익성", "증권사 구조조정", "증권사 IB 전략", "증권사 투자금융", "증권사 인수금융 주선", "종합투자계좌 IMA", "증권사 발행어음", "증권사 생산적금융", "증권사 기업금융 조직"],
        "criteria": "경쟁 증권사·금융그룹의 IB 전략, 조직 재편, 투자·주선 실적 및 자본 한도, 라이선스·규제 영향. 단순 실적 기사와 인사 단신 제외. 하나금융그룹 관련 기사는 하나금융그룹 탭으로 분류.",
    },
    "sto": {
        "no": "08", "emoji": "🔗", "label": "STO",
        "desc": "토큰증권·디지털증권 제도 및 인프라",
        "keywords": ["STO", "토큰증권", "조각투자", "스테이블코인", "토큰증권 STO", "원화 스테이블코인 제도", "토큰증권", "디지털자산 결제", "토큰증권 STO", "토큰증권 법제화", "조각투자 증권", "토큰증권 장외거래", "증권사 STO 플랫폼", "디지털 증권 수탁"],
        "criteria": "토큰증권 발행·유통·규제·투자자보호와 증권사 신사업화. 코인 시세, 일반 AI 소식, 단순 IT 제품 기사 제외.",
    },
    "productive": {
        "no": "09", "emoji": "🌱", "label": "생산적금융",
        "desc": "모험자본·성장산업·생산적금융 정책",
        "keywords": ["생산적금융", "모험자본", "벤처투자", "정책금융", "생산적금융 정책", "코스닥 벤처 생태계", "모험자본 금융지원", "AI 스타트업 투자", "생산적금융 투자", "모험자본 공급 증권사", "첨단전략산업 금융", "벤처투자 정책금융", "혁신기업 성장지원", "기업성장집합투자기구 BDC"],
        "criteria": "생산적금융, 모험자본 공급, 혁신기업과 첨단산업의 투자·융자·후속투자·회수 금융. 관련 없는 일반 산업·기술 기사 제외.",
    },
    "growthfund": {
        "no": "10", "emoji": "🏦", "label": "국민성장펀드",
        "desc": "국민성장펀드 정책·조성·투자·집행·운용·출자",
        "keywords": ["국민성장펀드", "국민참여성장펀드", "국민성장펀드 금융지원", "국민성장펀드", "국민참여성장펀드", "국민성장펀드 예산안", "국민성장펀드 실적 집행", "국민성장펀드 투자", "국민성장펀드 인프라 PF", "국민성장펀드 운용", "국민성장펀드 1호 사업", "국민성장펀드 금융지주", "국민성장펀드 출자"],
        "criteria": "국민성장펀드 관련 실제 출자·투자 승인·운용사 모집·프로젝트 선정뿐 아니라 정부/금융그룹의 집행 계획·실적·참여 협약·정책 발표·사업별 위험·예산·정책 운용 논란도 선정 가능. 단순 행사는 제외하지만 투자·재원·집행·제도·금융사 참여에 대한 구체적 사실이 있는 주요 발표는 유효한 소식. 첫 사업의 공정 위험과 EPC·PF도 핵심.",
    },
    "other": {
        "no": "11", "emoji": "🌍", "label": "기타",
        "desc": "글로벌 경제·산업·정책·AI 및 지정학적 핵심 이슈",
        "keywords": ["젠슨 황 사임 엔비디아 반도체 증시", "엔비디아 경영권 AI 투자 불확실성", "젠슨 황 엔비디아 GPU AI", "글로벌 경제 AI 반도체", "글로벌 AI 산업 리스크", "엔비디아 CEO 경영 리스크", "미중 AI 반도체 규제", "미국 반독점 M&A", "글로벌 금융시장 충격", "빅테크 AI 투자 변화", "글로벌 반도체 공급망", "국제 무역 제재 경제 영향"],
        "criteria": "다른 IB 카테고리에 명확히 속하지 않으나 글로벌 산업·경제·정책·기술 경쟁구도에 실질적인 영향을 미치는 핵심 이슈. CEO 관련 의혹은 사실 확인과 공식 조치가 있는 경우만 선정하고 선정적 사생활 보도는 제외. 시장 및 국내 산업·금융 영향 경로가 분명한 심층 분석을 우대.",
    },
}

# ─────────────────────────────────────────────────────────────
# 1) 뉴스 수집
# ─────────────────────────────────────────────────────────────
def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return html.unescape(text).replace("\xa0", " ").strip()


def normalize_title(title: str) -> str:
    return re.sub(r"[\W_]+", "", title).lower()


def fetch_naver_news(query: str, edition: str = None) -> list:
    url = "https://naverapihub.apigw.ntruss.com/search/v1/news"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": os.environ["NAVER_CLIENT_ID"],
        "X-NCP-APIGW-API-KEY": os.environ["NAVER_CLIENT_SECRET"],
    }
    params = {"query": query, "display": NAVER_PER_KEYWORD, "sort": "date"}
    res = requests.get(url, headers=headers, params=params, timeout=10)
    res.raise_for_status()

    out = []
    now = datetime.now(KST)
    edition = edition or edition_for_run(now)
    start_date, end_date = edition_window(now, edition)
    end_date = min(end_date, now)
    for it in res.json().get("items", []):
        link = it.get("originallink") or ""
        source = outlet_name(link)
        if not source:                        # 메이저 언론사가 아니면 제외
            continue
        title = strip_html(it.get("title"))
        if SHORT_NEWS_PATTERN.search(title):  # 단신·시황 제외
            continue
        try:
            pub_dt = parsedate_to_datetime(it["pubDate"]).astimezone(KST)
        except Exception:
            continue
        if not in_edition_window(pub_dt, now, edition):
            continue
        out.append({
            "title": title,
            "description": strip_html(it.get("description")),
            "reporter": "",
            "url": link,
            "source": source,
            "pub_dt": pub_dt,
            "matched_keyword": query,
        })
    return out


def collect_candidates(keywords: list, limit: int = None, edition: str = None, section: str = None) -> list:
    seen_urls, seen_titles, items = set(), set(), []
    from concurrent.futures import ThreadPoolExecutor
    # Source search can be slow; safely parallelize Naver keyword discovery
    # without changing article release-date or dedup requirements.
    def retrieve(kw):
        try:
            return kw, fetch_naver_news(kw, edition=edition)
        except Exception as e:
            print(f"  [경고] '{kw}' 검색 실패, 건너뜁니다: {e}")
            return kw, []
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(retrieve, keywords))
    for kw, fetched in results:
        for it in fetched:
            key_t = normalize_title(it["title"])
            if not it["url"] or it["url"] in seen_urls or key_t in seen_titles:
                continue
            seen_urls.add(it["url"])
            seen_titles.add(key_t)
            items.append(it)
    # Independent, publisher-original RSS supplements narrow Naver queries.
    # RSS records must satisfy the SAME 24-hour KST publication filter.
    if section:
        rss_added = 0
        now = datetime.now(KST)
        for it in recent_publisher_news():
            if not in_edition_window(it["pub_dt"], now, edition or edition_for_run(now)):
                continue
            if not sector_matches(section, it["title"], it["description"]):
                continue
            if SHORT_NEWS_PATTERN.search(it["title"]):
                continue
            key_t = normalize_title(it["title"])
            if it["url"] in seen_urls or key_t in seen_titles:
                continue
            seen_urls.add(it["url"])
            seen_titles.add(key_t)
            items.append(it)
            rss_added += 1
        print(f"  [출처 교차검색] {section}: RSS 원문 직접 발행 뉴스 추가 {rss_added}건")
    items.sort(key=lambda x: x["pub_dt"], reverse=True)  # 최신순으로 자르기
    return items[:limit] if limit else items


# ─────────────────────────────────────────────────────────────
# 2) OpenAI로 분류·요약·점수
# ─────────────────────────────────────────────────────────────
_openai_client = None

def ask_openai_json(prompt: str, max_tokens: int):
    """Ask the OpenAI Responses API for JSON judgments of article candidates."""
    global _openai_client
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY GitHub Actions secret 누락: 기존 뉴스 보존")
    if _openai_client is None:
        _openai_client = OpenAI(timeout=75.0, max_retries=2)
    try:
        result = _openai_client.responses.create(
            model=MODEL,
            instructions=(
                "증권사 IB기획실 뉴스 품질 평가 전문 분석가. "
                "정확한 JSON 배열만 출력. Markdown이나 설명은 금지. "
                "제목과 설명에서 확인되지 않는 사실, 숫자, 당사자를 만들지 말 것."
            ),
            input=prompt,
            max_output_tokens=max_tokens,
            store=False,
        )
        raw = (result.output_text or "").strip()
    except (AuthenticationError, PermissionDeniedError) as exc:
        raise RuntimeError("OpenAI API 인증/권한 오류: API 키 및 결제 설정 확인 필요") from exc
    except RateLimitError as exc:
        raise RuntimeError("OpenAI API 사용 한도/크레딧 오류: 기존 뉴스 보존") from exc
    except Exception as exc:
        print(f"  [경고] OpenAI API 요청 실패 ({type(exc).__name__}): {exc}")
        return None
    if not raw:
        print("  [경고] OpenAI API 응답이 비어 있습니다.")
        return None
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict) and isinstance(payload.get("articles"), list):
            return payload["articles"]
        return payload if isinstance(payload, list) else None
    except json.JSONDecodeError:
        print(f"  [경고] OpenAI JSON 파싱 실패: {raw[:350]}")
        return None


def to_int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def freshness_score(pub_dt: datetime, now: datetime) -> int:
    age = now - pub_dt
    if age <= timedelta(hours=24):
        return 100
    if age <= timedelta(hours=48):
        return 88      # 전일 기사도 08시 브리핑 대상
    if age <= timedelta(days=4):
        return 32      # 월요일에는 직전 금요일 뉴스도 선별 가능
    return 0


FALLBACK_TOPIC_PATTERNS = {
    "ECM": r"IPO|상장|공모|유상증자|전환사채|메자닌|코너스톤|블록딜|CB발행",
    "기업금융": r"회사채|DCM|기업대출|기업금융|채권발행|자금조달|PRS|신디케이트론|차환|크레딧",
    "M&A·인수금융": r"인수금융|인수합병|M&A|경영권 매각|경영권 인수|PEF|바이아웃|매각자문|인수자문|매각 추진",
    "부동산": r"부동산PF|부동산 PF|브릿지론|리츠|오피스|물류센터|상업용부동산|PF 재구조화",
    "인프라": r"데이터센터|GPU|발전소|인프라 금융|인프라 투자|해상풍력|신재생에너지|전력 인프라|민자사업",
    "하나금융그룹": r"하나증권|하나은행|하나금융|하나캐피탈|하나자산신탁|하나대체투자",
    "경쟁사": r"증권사|IB본부|한국투자증권|KB증권|NH투자증권|신한투자증권|미래에셋증권|메리츠증권|발행어음|IMA",
    "STO": r"STO|토큰증권|토큰화|조각투자|RWA|증권형 토큰",
    "생산적금융": r"생산적금융|국민성장펀드|모험자본|벤처투자|유니콘|정책금융|성장금융|성장펀드",
    "기타": r"AI|엔비디아|반도체|미중|빅테크|글로벌|무역|관세|반독점|금융시장|지정학",
}

def fallback_classify_batch(meta: dict, batch: list) -> list:
    """Use verified source-article key points if OpenAI credits are exhausted.

    When the full article cannot be read, the Naver snippet stays explicitly
    labeled and is never presented as a three-point substantive summary.
    """
    pattern = FALLBACK_TOPIC_PATTERNS.get(meta["label"], r"$^")
    out = []
    for c in batch:
        title = c.get("title", "")
        desc = re.sub(r"\s+", " ", c.get("description", "")).strip()
        if not sector_matches(meta["key"], title, desc):
            continue
        if not re.search(pattern, title + " " + desc, re.IGNORECASE):
            continue
        if len(desc) < 55 or SHORT_NEWS_PATTERN.search(title):
            continue
        if re.search(r"업무협약|협약식|기념식|인사발령|지점 오픈|행사 개최", title):
            continue
        # Candidate stage is metadata-only to meet the 08:00 publication window.
        # Full-text requests are made only for the 2–3 final chosen stories.
        summary = [desc[:180]]
        mode = "source_excerpt"
        age_hours = (datetime.now(KST) - c["pub_dt"]).total_seconds() / 3600
        score = 45 + (2 if c["source"] in PREFERRED_IB_OUTLETS else 0)
        score += min(14, len(desc)/20) + max(0, 13 - age_hours/8)
        if mode == "source_digest":
            score += 10
        out.append({**c, "summary":summary, "insight":"",
                    "summary_mode":mode, "reporter":"", "score":score})
    return out

def classify_batch(meta: dict, batch: list) -> list:
    numbered = "\n".join(
        f"{i}. [{c['source']} | {c['pub_dt'].strftime('%m-%d %H:%M')}] {c['title']}"
        + (f" — {c['description']}" if c["description"] else "")
        for i, c in enumerate(batch)
    )

    market_priority = ("- [최우선 시장성] 개별 기업의 인사·조직 내 인력 이동·통상적 실적 발표는 원칙적으로 제외. 시장 전체 수요, 조달비용, IB 경쟁 판도, 거래 관행, 업계 여러 기업의 재무지표 및 정책·규제 변화에 영향이 큰 이슈를 우선. 중요한 거래라도 업계 선례·가격 신호·금융구조와 위험 분담이 확인되어야 함.\n")
    section_exception = ("- [해당 분야 선정 완화] 하나금융그룹·경쟁사·STO·생산적금융·국민성장펀드는 시장 전체의 구조적 변화를 필수로 요구하지 않는다. 중요 금융기관의 실제 거래·주선·상품 도입·정책 결정·집행계획·실적·사업전략·투자자 설명회 및 산업 전반에 의미 있는 제도 진전은 사실과 수치가 확인되면 채택 가능. 단순 인사·홍보·시장과 무관한 행사는 제외.\n" if meta["key"] in RELAXED_SECTIONS else "")
    ecm_exception = ("- [ECM 실무 중요] 대표 IPO 기업의 공모가 확정, 밴드 대비 할인, 기관 수요예측 경쟁률, 청약 결과, 대표 주관사 성과, 구주/신주 비율 및 상장 추진은 구체적 사실과 공모시장 평가의 의미가 있다면 선정. 단, 반드시 지정된 24시간 내 발행된 실제 기사여야 한다.\n" if meta["key"] == "ecm" else "")
    scope_exception = ("- [기타 카테고리 중요] 공식 확인된 엔비디아 CEO 사임/교체가 AI반도체·GPU 공급망·증시·투자수요·대중국 기술제재에 미치는 영향은 반드시 중요한 시장 이슈로 평가. 스캔들 자체와 확인되지 않은 사생활/첩보 의혹은 선정 사유가 아니지만, 공식 경영진 교체와 투자자·시장 전체의 실제 반응을 다룬 후속 분석은 선정 가능. 글로벌 AI·반도체·금리·미중 기술규제는 특정 IB 거래가 없어도 여러 산업/금융기관에 영향을 주면 대상.\n" if meta["key"] == "other" else "")
    prompt = f"""너는 증권사 IB그룹 기획실에서 데일리 뉴스 모니터링을 담당하는 애널리스트야.
아래는 '{meta['label']}' 섹션의 후보 기사 목록이야. 각 기사를 판단해줘.

이 섹션이 다루는 범위: {meta['desc']}

판단 기준:
{meta['criteria']}

공통 기준:
{market_priority}{scope_exception}{section_exception}{ecm_exception}- 검색어가 들어 있어도 기사의 '주제'가 위 기준과 다르면 반드시 is_relevant: false
- 단신·시황 중계·보도자료 받아쓰기·개별 회사의 임원 인사/조직 내 인력 이동과 일반 홍보 행사는 is_relevant: false
- 원인·배경·영향·전망을 짚는 분석/기획/해설 기사를 선호
- **가장 중요한 원칙: '어떤 회사가 어떤 거래를 성사시켰다'는 실적·동향보다 '이 사건이 IB 사업환경에 무엇을 바꾸는가'를 평가할 것.**
- 다음처럼 여러 기업이나 IB 사업에 파급되는 뉴스에 높은 점수: 실제 시행·확정된 정책 및 규제 변화, 공모/회사채/인수금융/PF 시장의 구조 변화, 신용·유동성 위기, 자본 조달 조건 변화, 금융기관의 전략적 사업 구조 변화, 생산적금융의 자금 공급·배분 방식 변화.
- 개별 발행·인수·매각·IPO·투자는 **시장 가격형성, 자금조달 조건, 투자자 회수, 금융사 경쟁구도를 바꾸는 중요한 거래**만 선정. 단순 성사, 개인 인사 및 개별 회사의 평범한 실적은 제외.
- 금융회사와 상장사의 실적은 **업계 전체 또는 복수 회사 실적·자본효율성·수익구조·부실과 충당금의 구조적 추세**를 평가하는 기사일 때 선정 가능.
- 다만 거래 규모가 크지 않아도 새로운 규제·심사 기준의 첫 적용, 새로운 금융 구조·회수 경로, 위험 분담 관행의 전환처럼 업계에 적용 가능한 선례가 뚜렷하면 선정 가능.
- 기사에 '왜 IB 실무가 주목해야 하는지'를 객관적인 근거로 설명할 수 없다면 is_relevant=false. 거래 발표 자체는 선정 이유가 될 수 없음.
- 시장 전체 또는 업계 복수 기업에 영향을 주는 현상을 우대하되, 영향이 언급되지 않은 단일 기업 실적 기사는 제외.
- 한국경제·한국금융신문·법률신문·자본시장 전문지 등 매체 이름만으로 우대하지 않고, 숫자·거래구조·쟁점·실무 영향을 기준으로 품질 판단
- 법률신문의 법규 해설은 IPO·인수금융·부동산PF·STO 등에 직접 관련되는 경우만 선정
- **매일 한국시간 07:30 일괄 브리핑**: 전일 오전 07:30을 초과한 시각부터 금일 오전 07:30까지 최초 발행된 기사만 검토. 각 카테고리에서 시장 영향도가 높은 2~3건을 목표로 선정하되, 해당 기간에 가치 있는 기사가 부족하면 억지로 채우지 않음. 기존 게시 기사와 같은 내용의 반복 보도는 금지. 공식 결정·승인·금액 변경·계약 체결 등 실질적 신규 사실이 있는 후속 보도만 예외. 주말·월요일에도 동일.
- 직전 브리핑에 게재된 동일 기업·사건을 그대로 반복하는 기사는 제외. 다만 신규 공식 결정·금액·거래 단계·확인된 영향 등 구체적 신규 사실이 있다면 후속 보도로 선정 가능. 단순 재해석이나 제목만 바꾼 보도는 제외.
- 1면이나 검색어 순위 등 화제성은 근거가 있는 경우만 반영하고, 확인되지 않은 순위·화제성을 지어내지 말 것.
- 단순 보도자료를 옮겨 적은 제목·설명에는 낮은 depth/quality를 부여. 데이터·거래구조·법규 쟁점과 왜 중요한지가 확인되는 기사 위주로 선정.
- 언론사 이름 자체가 합격 기준은 아님. IB 전문지·경제지·법률신문·전자신문·딜북 등 해당 주제에 전문성을 지닌 출처도 동등 평가.
- 카테고리별 2~3건을 목표로 다양한 기사를 검토하되, 핵심 소식이 한 건뿐이면 그 한 건만 선정. 지정 완화 카테고리에서는 해당 업무에 직접 의미 있는 사실 중심 보도도 과도하게 배제하지 말 것.
- '하나금융그룹' 카테고리는 다른 분야와 달리 **실제 거래·성과·행사 2건 + 객관적 문제·건전성 위험 1건**을 우선. 하나금융 관련 기사라면 단순 특정 거래가 아니라 그룹 영업·자본효율성·ONE-IB 협업의 의미를 평가.
- 하나금융그룹으로 분류할 때에는 hana_view 필드에 positive(긍정적 실적·신규 금융·주주 소통), risk(문제·규제·대손·부실 등), neutral 중 하나를 기입. 과장이나 억지 구분은 금지.
- '국민성장펀드'는 일반 생산적금융과 분리하며 정책 목표와 실제 집행, 지주별 참여, 대형 투자사업의 금융 위험 등을 다양하게 확인.

점수 기준 (0~100 정수):
- materiality: **IB 비즈니스에 미치는 구조적 중요도**. 금융수요·가격·거래구조·규제·리스크·주관경쟁 등에 실질적인 변화가 있는지. 단순 거래 성사·회사 실적은 50점 이하. 시장 전체 파급/금융관행 선례는 80점 이상.
- importance: 여러 IB 사업·고객에게 미칠 파급력 (거래 규모 자체보다 조달시장·규제·리스크 변화에 높은 점수)
- depth: 심층성. 분석·해설이며 원인·근거·거래구조·맥락이 풍부하면 높게, 사실 전달뿐이면 낮게
- quality: 내용이 구체적이고 신뢰할 만한지
- impact_type: policy_regulation | capital_market_structure | systemic_risk | financing_conditions | transaction_precedent | industry_investment_shift | competitive_strategy 중 하나. 해당 사항이 없는 일반 단일 거래·실적은 is_relevant=false.
- impact_reason: **왜 이 이슈를 IB기획실에 공유해야 하는지** 25~90자. 단순 '수요 확대 전망' '동향 주목'이 아니라 사업·금융구조·시장 위험에 대한 영향 경로를 구체적으로 기술.
- impact_evidence: 제목 또는 검색 설명에 실제 존재하는 7~65자 길이의 **연속된 문구를 그대로 복사**. 이 문구가 핵심 변화의 근거여야 하며 임의로 재작성하면 안 됨. 뚜렷한 원문 근거가 없으면 부적합 처리.
- 제목·검색 설명만 제공되므로 출처에 없는 전망·금액·인과관계는 절대 지어내지 말 것.

summary 작성 규칙 (개조식):
- 반드시 3개 항목. 각 항목 40~75자의 완결된 개조식 문장으로 요약할 것
- 명사형 종결 ("~했다" X → "~흥행", "~확대", "~부담 가중" O)
- 1번째: 시장·정책·금융구조에 어떤 **중요한 변화**가 발생했는지 (객관적 사실)
- 2번째: 그 변화의 **배경과 원인**, 확인된 수치·제도·계약 구조
- 3번째: 해당 변화가 **IB 사업에 미치는 구체적인 영향 경로** (발행조건·인수금융·투자·위험한도·회수·주관 경쟁 등). 원문에 없는 전망이나 효과는 단정하지 말 것.
- 단일 기업의 거래 성사·실적 수치를 세 문장 반복하는 요약은 금지. 주목할 만한 영향이 확인되지 않으면 기사를 선정하지 말 것.
- insight는 **선택 항목**. 기본값은 빈 문자열 ""임.
- 법률·정책·규제의 실제 제·개정/시행, 대규모 금융사고·신용위기, 업계의 중대한 금융수요·자금흐름 변화, 경쟁사의 구조적 전략 전환 등 **IB 전략·손익·위험한도·대내외 협업 전략에 영향이 큰 사건**만 작성.
- 일반적인 IPO 청구·회사채 발행·단순 개별 인수/투자 소식은 원칙적으로 insight="" 처리. 기사 3건 모두 시사점이 없어도 정상임.
- 꼭 넣을 필요 없음. 확인 가능한 변화→새로운 금융 수요/위험→하나증권 IB기획실이 검토할 구체적인 의사결정 또는 실행 방안이 연결될 때만 제시.
- 뉴스 요약의 사실만으로 중요도를 판단하기 어렵거나 하나증권 고유의 전략적 함의가 모호하면 insight="" 반환.
- 단순 '시장 확대', '금융 수요 증가', '대응 필요' 등 기사의 내용을 반복하는 일반론은 금지.
- IB 비즈니스 트렌드로 발생하는 구체적인 자금조달 수요, 위험, 경쟁구도 변화와 연결.
- 하나증권의 ECM·DCM·인수금융·부동산/인프라금융·생산적금융 또는 그룹 협업 전략에 어떤 의미인지 간결하게 제안.
- 확인되지 않은 내부 강점·약점·실적을 지어내지 말고, 필요한 경우 '기존 주선 경험 확인', '익스포저/한도 검토' 등 점검 과제로 작성.
- 한 줄 30~65자, 개조식 종결. 실제 전략적 가치가 없는 기사라면 빈 문자열.
- 좋은 예: 'AI 데이터센터 전력·임차계약 리스크 점검 및 그룹 공동 금융주선 가능성 검토'
- 좋은 예: 'IPO 회수시장 위축에 대응한 프리IPO·후속투자 금융수요 모니터링'
- 각 항목에서 가장 중요한 부분(숫자, 금액, 당사자, 핵심 변화) 1곳을 **별표 두 개**로 감쌀 것
- 근거가 없어 3개의 사실을 작성할 수 없는 기사는 is_relevant=false로 분류하고 추측성 요약을 만들지 말 것
- 예: ["3,000억원 모집에 **2.4조원 주문**이 몰리며 5,000억원으로 증액 발행 결정",
       "연말 기관 자금 집행과 금리 하락 기대가 겹치며 **우량 크레딧 수요 급증**",
       "A급 이하 발행사는 **미매각 부담 여전**, 발행 시점 분산과 금리 가산 불가피"]

기사 목록의 각 줄은 "[언론사 | 날짜] 제목 — 설명" 형식이야.
설명에 기자 이름이 보이면 reporter에 "홍길동 기자" 형태로 넣고, 없으면 빈 문자열로 둬.

각 기사(번호 순서 그대로)에 대해 아래 JSON 배열 형식으로만 답해. 다른 설명은 붙이지 마:
[
  {{
    "index": 0,
    "is_relevant": true,
    "reporter": "기자명 (없으면 빈 문자열)",
    "summary": ["개조식 1", "개조식 2", "개조식 3"],
    "insight": "전략적 검토가 필요할 때만 짧은 개조식, 불필요하면 빈 문자열",
    "importance": 0,
    "depth": 0,
    "quality": 0,
    "hana_view": "positive",
    "materiality": 0,
    "impact_type": "policy_regulation",
    "impact_reason": "왜 여러 IB 사업 또는 금융구조에 중요한지 설명",
    "impact_evidence": "기사 제목 또는 설명에서 그대로 복사한 연속 문구"
  }}
]

부적합한 기사는 "index"와 "is_relevant": false 만 넣어도 돼.
반드시 마지막 항목까지 JSON 배열을 완전히 닫아서 응답해.

후보 기사 목록:
{numbered}
"""

    judgments = ask_openai_json(prompt, max_tokens=5000)
    if not isinstance(judgments, list):
        print(f"  [경고] '{meta['label']}' AI 요약 불가 → 원문 검색 발췌 모드")
        return fallback_classify_batch(meta, batch)

    if meta["key"] == "other":
        yes_count = sum(bool(j.get("is_relevant")) for j in judgments if isinstance(j, dict))
        print(f"  [기타 진단] LLM 입력 {len(batch)}건, 후보 적합 판단 {yes_count}건")
    now = datetime.now(KST)
    results = []
    for j in judgments:
        if not isinstance(j, dict) or not j.get("is_relevant"):
            continue
        idx = to_int(j.get("index"), -1)
        if not 0 <= idx < len(batch):
            continue
        depth = to_int(j.get("depth"))
        # The first morning story has a grounded-news gate, not the stricter
        # afternoon deep-analysis gate. This improves coverage without invention.
        morning = edition_for_run(datetime.now(KST)) == "0730"
        relaxed = meta["key"] in RELAXED_SECTIONS
        depth_floor = 49 if relaxed else (58 if morning else MIN_DEPTH)
        quality_floor = 57 if relaxed else (64 if morning else MIN_QUALITY)
        importance_floor = 52 if relaxed else (60 if morning else MIN_IMPORTANCE)
        if (depth < depth_floor or to_int(j.get("quality")) < quality_floor
                or to_int(j.get("importance")) < importance_floor):
            continue
        c = batch[idx]
        # Hana Group: genuine important deals, investor events and objective
        # credit-quality challenges matter even without economy-wide impact.
        if meta["key"] == "hana":
            evidence = str(j.get("impact_evidence") or "").strip()
            factual = evidence and len(evidence) >= 7 and (
                "".join(evidence.split()) in "".join((c["title"]+" "+c["description"]).split())
            )
            if not (factual and depth >= 49 and to_int(j.get("quality")) >= 57
                    and to_int(j.get("importance")) >= 52):
                continue
        elif meta["key"] == "other":
            # Global system-wide industry/economic shifts need not be a named IB deal.
            # Require concrete evidence in the article metadata, not market gossip.
            evidence = re.sub(r"\s+", "", str(j.get("impact_evidence") or "")).casefold()
            reference = re.sub(r"\s+", "", c["title"] + " " + c["description"]).casefold()
            if (to_int(j.get("importance")) < 75
                    or to_int(j.get("materiality")) < 65
                    or not (7 <= len(evidence) <= 110 and evidence in reference)
                    or (re.search(r"불륜|내연|사생활|루머|음모론", c["title"])
                        and not re.search(r"사임|퇴진|경영진|증시|금융시장|반도체|GPU|AI 투자|주가", c["title"] + " " + c["description"]))):
                continue
        elif not is_material_news(j, c["title"], c["description"]):
            # A verified, strategically informative financing/deal may matter
            # in a specialist desk even without economy-wide materiality.
            evidence = re.sub(r"\s+", "", str(j.get("impact_evidence") or "")).casefold()
            reference = re.sub(r"\s+", "", c["title"] + " " + c["description"]).casefold()
            reason = str(j.get("impact_reason") or "")
            secondary = (to_int(j.get("importance")) >= (60 if morning else 65)
                         and to_int(j.get("materiality")) >= (55 if morning else 60)
                         and to_int(j.get("quality")) >= (64 if morning else 70)
                         and str(j.get("impact_type") or "") in (
                             "transaction_precedent", "financing_conditions",
                             "competitive_strategy", "industry_investment_shift")
                         and len(reason) >= 24
                         and 7 <= len(evidence) <= 110 and evidence in reference)
            if (not secondary and relaxed and meta["key"] != "other"
                    and to_int(j.get("importance")) >= 52
                    and to_int(j.get("materiality")) >= 43
                    and to_int(j.get("quality")) >= 57
                    and len(reason) >= 14
                    and 7 <= len(evidence) <= 110 and evidence in reference
                    and not is_personnel_only(c["title"])):
                secondary = True
            if (not secondary and morning and meta["key"] != "other"
                    and to_int(j.get("importance")) >= 65
                    and to_int(j.get("quality")) >= 64
                    and len(reason) >= 18
                    and 7 <= len(evidence) <= 110 and evidence in reference
                    and not re.search(r"^(?:주가|특징주|증시시황|연예)", c["title"])):
                secondary = True
            if not secondary:
                print(f"  [선정 제외] 중요도/원문 근거 부족: {c['title'][:60]}")
                continue
        if not sector_matches(meta["key"], c["title"], c["description"]):
            continue

        final_score = (
            0.05 * freshness_score(c["pub_dt"], now)
            + 0.45 * to_int(j.get("materiality"))
            + 0.25 * to_int(j.get("importance"))
            + 0.15 * depth
            + 0.10 * to_int(j.get("quality"))
            + (1 if c["source"] in PREFERRED_IB_OUTLETS else 0)
        )
        reporter = re.sub(r"\s+", " ", str(j.get("reporter") or "")).strip()[:20]

        summary = j.get("summary", "")
        if isinstance(summary, list):
            summary = [str(x).strip() for x in summary if str(x).strip()]
        elif summary:
            summary = [str(summary).strip()]
        else:
            summary = []

        # Search metadata cannot substantiate numerical facts that only
        # the model appears to know. Omit the article instead of inventing.
        if (len(summary) != 3
                or any(len(str(line)) < 22 or len(str(line)) > 190
                       or str(line).rstrip().endswith(("…","..."))
                       for line in summary)):
            continue
        if not numeric_claims_grounded(summary, c["title"]+" "+c["description"]):
            print(f"  [검증 제외] 미확인 수치가 포함된 AI 요약: {c['title'][:65]}")
            continue

        results.append({
            **c,
            "summary": summary,
            "insight": accept_insight(
                c["title"], summary, j.get("insight"),
                importance=to_int(j.get("importance")), depth=depth,
            ),
            "reporter": reporter,
            "score": final_score,
            "hana_view": str(j.get("hana_view") or "neutral"),
        })
    return results


def load_recent_headlines(now):
    """Previous three published dates only for duplicate checks, never to refill."""
    rows=[]
    archive=Path(__file__).resolve().parents[1]/"docs"/"archive"
    for offset in (1,2,3):
        date=(now.date()-timedelta(days=offset)).isoformat()
        p=archive/(date+".json")
        if not p.exists():
            continue
        try:
            content=json.loads(p.read_text(encoding="utf-8"))
            for group in content.get("categories",{}).values():
                rows.extend({"title":str(a.get("headline","")),"url":str(a.get("url",""))}
                            for a in group if isinstance(a,dict))
        except (OSError,ValueError) as exc:
            print(f"  [경고] 이전 브리핑 읽기 실패 ({date}): {exc}")
    return rows


def repeated_last_days(candidate):
    """Suppress substantially repeated articles; no quota to fill."""
    return any(
        repeated_without_update(candidate,previous)
        for previous in RECENT_HEADLINES
    )


# ─────────────────────────────────────────────────────────────
# 3) 섹션별 기사 선정
# ─────────────────────────────────────────────────────────────
def build_section(meta: dict, used_urls: set, edition: str, prior_articles: list, target_limit: int = PER_EDITION_LIMIT) -> list:
    # Only select this slot's missing number of genuinely new stories.
    if target_limit <= 0:
        return []
    print(f"▶ [{meta['no']}] {meta['label']} 수집 중...")
    collected = collect_candidates(meta["keywords"], edition=edition, section=meta["key"])
    matching = [
        c for c in collected
        if sector_matches(meta["key"], c["title"], c.get("description", ""))
        and not is_personnel_only(c["title"])
    ]
    eligible_candidates = [
        c for c in matching
        if not repeated_last_days(c)
        and c["url"] not in used_urls
        and not any(repeated_without_update(c, p) for p in prior_articles)
    ]
    if len(matching) == 0:
        print(f"  [수집 진단] {meta['key']}: 24시간 내 수집 {len(collected)}건, 분야 일치 0건. 검색어/출처/시간대 검토 필요")
    else:
        print(f"  [수집 진단] {meta['key']}: 수집 {len(collected)}건 → 분야 일치 {len(matching)}건 → 전일/당일 중복 제외 후 {len(eligible_candidates)}건")
    candidates = eligible_candidates[:MAX_CANDIDATES]
    print(f"  후보 {len(candidates)}건 → OpenAI 분류 요청 중...")

    scored = []
    for start in range(0, len(candidates), BATCH_SIZE):
        scored.extend(classify_batch(meta, candidates[start:start + BATCH_SIZE]))
    scored.sort(key=lambda x: x["score"], reverse=True)
    scored=deduplicate(scored)

    # 2~3건 목표에 못 미치면 더 많은 후보를 검토하되 품질 기준은 그대로 유지.
    if len(scored) < target_limit:
        # Reuse the first discovery pass instead of refetching every keyword.
        extra = eligible_candidates[MAX_CANDIDATES:MAX_CANDIDATES + 24]
        for start in range(0, len(extra), BATCH_SIZE):
            scored.extend(classify_batch(meta, extra[start:start + BATCH_SIZE]))
        scored.sort(key=lambda x: x["score"], reverse=True)
    scored=deduplicate(scored)

    # Source-grounded editorial rescue: the LLM can reject legitimate market
    # analysis in a sparse weekend edition. Only verified *full-body* content
    # can supplement the selected set; never invent a summary or reuse old news.
    if len(scored) < target_limit and edition == "0730":
        from concurrent.futures import ThreadPoolExecutor

        def editorial_importance(candidate):
            headline = candidate.get("title", "")
            context = headline + " " + candidate.get("description", "")
            total = 0
            if re.search(r"엔비디아|NVIDIA|젠슨\s*황", headline, re.I):
                total += 6
                if re.search(r"사임|퇴진|CEO|경영진|임시\s*CEO", context, re.I):
                    total += 9
            if re.search(r"시장\s*전체|금융시장|증시|반도체|GPU|데이터센터|공급망|수출규제|산업\s*전반|시가총액", context, re.I):
                total += 7
            if re.search(r"제도|규제|정책|국민성장펀드|인수금융|자금조달|투자위축|신용위험", headline):
                total += 5
            if candidate.get("source") in PREFERRED_IB_OUTLETS:
                total += 2
            return total

        rescue = [
            candidate for candidate in eligible_candidates
            if not any(duplicate(candidate, old) for old in scored)
        ]
        rescue.sort(key=editorial_importance, reverse=True)
        rescue = rescue[:12 if meta["key"] == "other" else 8]
        if rescue:
            with ThreadPoolExecutor(max_workers=4) as pool:
                summaries = list(pool.map(
                    lambda c: digest(c["title"], c["url"], c.get("description", "")),
                    rescue,
                ))
            for candidate, facts in zip(rescue, summaries):
                if not facts or not validate_digest(candidate["title"], facts):
                    continue
                # For global articles, reject salacious/individual claims
                # without a verifiable wider financial/industrial implication.
                if meta["key"] == "other":
                    context = candidate["title"] + " " + candidate.get("description", "")
                    if not re.search(
                        r"증시|반도체|AI|엔비디아|GPU|금융시장|수출규제|"
                        r"경제|무역|금리|투자|데이터센터|공급망", context, re.I
                    ):
                        continue
                    if re.search(r"불륜|내연|사생활|스캔들", candidate["title"]):
                        if not re.search(
                            r"사임|퇴진|주가|시장|증시|경영진|반도체|"
                            r"AI\s*산업|GPU", context, re.I
                        ):
                            continue
                scored.append({
                    **candidate,
                    "summary": facts,
                    "summary_mode": "source_digest",
                    "insight": "",
                    "reporter": "",
                    "score": 59 + editorial_importance(candidate),
                    "hana_view": "neutral",
                })
                scored = deduplicate(sorted(scored, key=lambda x: x["score"], reverse=True))
                print(f"  [원문 재검토] {candidate['source']} | {candidate['title'][:60]}")
                if len(scored) >= target_limit:
                    break

    # Last-resort SOURCE PREVIEW (not a fabricated article summary).
    # If paywalls/robot blocking prevent fetching body and a news-specific
    # AI filter rejects every meaningful story, publish clearly labelled
    # extractive source-preview text, never pretend it is a 3-point digest.
    # One preview must NOT outrank source-verified three-point summaries.
    if len(scored) < target_limit:
        preview_candidates = [
            c for c in eligible_candidates
            if not any(duplicate(c, old) for old in scored)
            and len(re.sub(r"\s+", " ", c.get("description", "")).strip()) >= 65
            and not is_personnel_only(c.get("title", ""))
            and not SHORT_NEWS_PATTERN.search(c.get("title", ""))
        ]

        def preview_rank(c):
            title = c.get("title", "")
            desc = c.get("description", "")
            high_signal = re.search(
                r"IPO|공모|상장|청약|유상증자|주관|기업대출|회사채|"
                r"인수금융|M&A|매각|인수|PF|리츠|데이터센터|GPU|"
                r"전력망|투자|주선|실적|종투사|발행어음|금융지주|"
                r"STO|토큰증권|스테이블코인|정책금융|생산적금융|"
                r"국민성장펀드|규제|제도|승인|집행|펀드|AI", title, re.I)
            return (35 if high_signal else 0) + min(len(desc), 180) / 20 + (
                9 if c.get("source") in PREFERRED_IB_OUTLETS else 0)

        preview_candidates.sort(key=preview_rank, reverse=True)
        for c in preview_candidates:
            title = c["title"]
            desc = re.sub(r"\s+", " ", c["description"]).strip()
            if meta["key"] == "other" and not re.search(
                r"금융시장|거시|AI|반도체|엔비디아|금리|미중|관세|수출|"
                r"산업|데이터센터|경제|투자|무역", title + " " + desc, re.I):
                continue
            # Avoid listing PR-only or personal announcements as meaningful news.
            if re.search(
                r"임원\s*인사|기부|봉사|캠페인|홍보대사|사생활|"
                r"불륜|화보|팬미팅|광고모델|기념식|지점\s*오픈", title):
                continue
            preview = desc[:230].rstrip(" .,;")
            if len(preview) < 65:
                continue
            scored.append({
                **c, "summary": [preview],
                "summary_mode": "source_excerpt",
                "insight": "", "reporter": "",
                "score": min(56, 35 + preview_rank(c) / 3),
                "hana_view": "neutral",
            })
            scored = deduplicate(sorted(scored, key=lambda x: x["score"], reverse=True))
            print(f"  [추가 원문 후보] {meta['key']}: 출처 제공 기사 설명만 게시 | {title[:65]}")
            if len(scored) >= target_limit:
                break

    if meta["key"] == "hana":
        # Group briefing editorial mix: 2 grounded positives + 1 objectively
        # supported risk/challenge. Never fill a missing side with old articles.
        positive = [x for x in scored if x.get("hana_view") == "positive"]
        risk = [x for x in scored if x.get("hana_view") == "risk"]
        neutral = [x for x in scored if x.get("hana_view") == "neutral"]
        priority_mix = (positive[:2] + risk[:1] + positive[2:] + neutral
                        if target_limit >= 3 else positive + risk + neutral)
        top = deduplicate(priority_mix)[:target_limit]
        # Preserve the 2 positive + 1 challenge target when evidence exists.
        # Include only objectively evaluated neutral strategic developments if
        # the model has not produced enough positive/challenge candidates.
        if len(top) < 1:
            top = scored[:target_limit]
        # Never invent additional news solely to fill a quota.
    else:
        top = scored[:target_limit]
    # Only the chosen stories are read in full; preserve all original source URLs.
    from concurrent.futures import ThreadPoolExecutor, as_completed
    to_enrich = [item for item in top if item.get("summary_mode") == "source_excerpt"]
    if to_enrich:
        with ThreadPoolExecutor(max_workers=3) as executor:
            tasks = {executor.submit(digest, item["title"], item["url"],
                                     item.get("description","")): item for item in to_enrich}
            for future in as_completed(tasks):
                try:
                    summary = future.result()
                    if summary and validate_digest(tasks[future]["title"],summary):
                        tasks[future]["summary"] = summary
                        tasks[future]["summary_mode"] = "source_digest"
                except Exception as exc:
                    print(f"  [경고] 원문 핵심 추출 불가: {exc}")
    # Optional IB strategy note. Never insert generic category-based notes.
    # The vetted model response is the only source; source-only digest has no note.
    for item in top:
        item["insight"] = item.get("insight","") if item.get("summary_mode")=="ai_summary" else ""
    # Label 1-point source excerpts transparently when full text is
    # unavailable; AI/source digests still require three verified points.
    top=[item for item in deduplicate(top)
         if isinstance(item.get("summary"),list)
         and ((item.get("summary_mode") == "source_excerpt"
               and len(item["summary"]) == 1
               and len(item["summary"][0]) >= 45)
              or (len(item["summary"]) == 3
                  and item.get("summary_mode") in ("ai_summary", "source_digest")
                  and (item.get("summary_mode") == "ai_summary"
                       or validate_digest(item["title"],item["summary"]))))]
    # POST-VALIDATION RECOVERY: some scored stories are discarded above
    # because AI output/whole-body extraction was incomplete.  In that case
    # the old flow printed zero although many real dated articles were found.
    # Revisit raw candidate descriptions only after ALL stronger summaries are
    # validated, and transparently show source text instead of fabricated lines.
    if len(top) < target_limit:
        def reliable_preview(candidate):
            title = candidate.get("title", "")
            desc = re.sub(r"\s+", " ", candidate.get("description", "")).strip()
            if len(desc) < 45 or len(title) < 12:
                return False
            if SHORT_NEWS_PATTERN.search(title) or is_personnel_only(title):
                return False
            if re.search(r"연예|사생활|불륜|팬미팅|광고모델|단순인사|포토", title):
                return False
            return True

        additional = [c for c in eligible_candidates
                      if reliable_preview(c)
                      and not any(repeated_without_update(c, v) for v in top)]
        additional.sort(key=lambda c: (
            c.get("source") in PREFERRED_IB_OUTLETS,
            len(c.get("description", "")),
            c["pub_dt"],
        ), reverse=True)
        for c in additional:
            if any(repeated_without_update(c, v) for v in top):
                continue
            # Copy source metadata excerpt exactly as issued, without
            # attributing additional fabricated business or numeric facts.
            snippet = re.sub(r"\s+", " ", c["description"]).strip()[:230].rstrip()
            if len(snippet) < 45:
                continue
            top.append({
                **c, "summary": [snippet],
                "summary_mode": "source_excerpt",
                "insight": "", "reporter": "",
                "score": 32, "hana_view": "neutral",
            })
            print(f"  [선정 보완] {meta['key']}: 본문 확보 전 출처 설명 발췌 | {c['title'][:65]}")
            if len(top) >= target_limit:
                break
        top=deduplicate(top)[:target_limit]
    if len(top) < 2:
        print(f"  [기사 부족] {meta['key']}: 마지막 원문 재검증 후 {len(top)}건 (누락 방지 검색 확대 필요)")
    used_urls.update(item["url"] for item in top)
    print(f"  → {len(top)}건 선정")

    return [
        {
            "top": (i == 0),
            "headline": item["title"],
            "source": item["source"],
            "pubDate": item["pub_dt"].strftime("%Y-%m-%d"),
            "pubTime": item["pub_dt"].strftime("%H:%M KST"),
            "publishedAt": item["pub_dt"].isoformat(),  # seconds retained for strict 07:30 boundary
            "summary": item["summary"][:3],
            "summary_mode": item.get("summary_mode", "ai_summary"),
            "insight": item.get("insight", ""),
            "reporter": item["reporter"],
            "url": item["url"],
            "edition": edition,
            "editionLabel": EDITION_LABELS[edition],
        }
        for i, item in enumerate(top)
    ]


# Previous-day archive filler intentionally removed: never recycle old news.

# Suppress routine staffing changes, even if the headline mentions IB.
PERSONNEL_ONLY = re.compile(
    r"임원\s*(?:인사|선임|교체|영입)|대표\s*(?:취임|선임|교체|인사)|"
    r"사장\s*(?:교체|선임|취임)|CEO\s*(?:취임|영입|선임|인사)|"
    r"인사\s*발령|조직\s*내\s*이동|본부장\s*(?:선임|교체)|전담팀\s*꾸린다|본부장\s*인사",
    re.I,
)


def is_personnel_only(title: str) -> bool:
    return bool(PERSONNEL_ONLY.search(title or ""))


def article_timestamp(article: dict):
    if not isinstance(article, dict) or not article.get("url"):
        return None
    exact = str(article.get("publishedAt") or "").strip()
    if exact:
        try:
            parsed = datetime.fromisoformat(exact)
            if parsed.tzinfo:
                return parsed.astimezone(KST)
        except ValueError:
            pass
    try:
        return datetime.strptime(
            str(article.get("pubDate", "")) + " " +
            str(article.get("pubTime", "")).replace(" KST", ""),
            "%Y-%m-%d %H:%M"
        ).replace(tzinfo=KST)
    except (ValueError, TypeError):
        return None


def is_current_day_article(article: dict, now: datetime) -> bool:
    published = article_timestamp(article)
    return published is not None and in_day_window(published, now)


def main():
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY 미등록: 기존 브리핑 보존")
    global RECENT_HEADLINES
    now = datetime.now(KST)
    edition = edition_for_run(now)
    window_start, window_end = edition_window(now, edition)
    print(f"▶ 매일 {EDITION_LABELS[edition]} 기준 기사 발행구간: "
          f"{window_start:%m-%d %H:%M} ~ {window_end:%m-%d %H:%M} KST")
    RECENT_HEADLINES = load_recent_headlines(now)
    today_path = (Path(__file__).resolve().parents[1] / "docs" / "archive"
                  / (now.date().isoformat() + ".json"))

    previous_payload = {}
    if today_path.exists():
        try:
            previous_payload = json.loads(today_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            print(f"  [경고] 당일 아카이브 읽기 실패: {exc}")

    previous_sections = previous_payload.get("categories", {})
    used_urls = set()
    prior_articles = []
    data = {}
    for key in SECTIONS:
        retained = []
        for article in previous_sections.get(key, []):
            if not is_current_day_article(article, now):
                continue
            if is_personnel_only(article.get("headline", "")):
                continue
            if repeated_last_days({
                    "title": article.get("headline", ""),
                    "url": article.get("url", "")}):
                continue
            if article["url"] in used_urls:
                continue
            if any(repeated_without_update(article, p) for p in prior_articles):
                continue
            article = dict(article)
            article["edition"] = "0730"  # migrate older multi-edition articles
            article["editionLabel"] = EDITION_LABELS[article["edition"]]
            retained.append(article)
            used_urls.add(article["url"])
            prior_articles.append(article)
        data[key] = retained[:MAX_STORIES_PER_CATEGORY]

    # Single daily briefing: up to 3 current stories per category.
    # Manual retries may add verified stories without carrying old news.
    for key, meta in SECTIONS.items():
        already = sum(item.get("edition") == edition for item in data[key])
        remaining = max(0, min(PER_EDITION_LIMIT - already,
                               MAX_STORIES_PER_CATEGORY - len(data[key])))
        if remaining:
            added = build_section(
                {**meta, "key": key}, used_urls,
                edition=edition,
                prior_articles=prior_articles,
                target_limit=remaining,
            )
            for item in added:
                if item["url"] in {p["url"] for p in data[key]}:
                    continue
                if any(repeated_without_update(item, p) for p in prior_articles):
                    continue
                data[key].append(item)
                used_urls.add(item["url"])
                prior_articles.append(item)
                if sum(a.get("edition") == edition for a in data[key]) >= PER_EDITION_LIMIT:
                    break
        # Newest verified story first. Historical dates remain available.
        data[key].sort(key=lambda item: (
            EDITION_ORDER.index(item.get("edition", "0730")),
            str(item.get("pubDate", "")),
            str(item.get("pubTime", "")),
        ), reverse=True)
        for i, item in enumerate(data[key]):
            item["top"] = i == 0
        n_new = sum(x.get("edition") == edition for x in data[key])
        print(f"  [회차별 누적] {key}: 이번 {n_new}건 / 총 {len(data[key])}건")

    produced_at = datetime.now(KST)
    try:
        reports = collect_reports(data, produced_at)
        print(f"  → 관련 기관 보고서 {len(reports)}건 선정")
    except Exception as exc:
        print(f"  [경고] 자료 검색 실패: {exc}")
        reports = []

    output = {
        "generated_at": produced_at.strftime("%Y-%m-%d %H:%M KST"),
        "last_edition": edition,
        "editions_completed": ["0730"],
        "reports": reports,
        "sections": [
            {"key": k, "no": m["no"], "emoji": m["emoji"], "label": m["label"], "desc": m["desc"]}
            for k, m in SECTIONS.items()
        ],
        "categories": data,
    }

    out_path = os.path.join(os.path.dirname(__file__), "..", "docs", "data.json")
    publishable=sum(len(v) for v in data.values())
    # Target 2-3 genuinely important stories per category; no stale backfill.
    if publishable == 0:
        print("  [공지] 대상 시간대에 검증된 중요 기사가 없습니다. 빈 오늘자 브리핑을 정상 게시합니다.")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    # 선택 날짜의 뉴스를 보관하여 최근 30일 검색과 과거 조회에 활용
    archive_dir = Path(out_path).parent / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / (datetime.now(KST).strftime("%Y-%m-%d") + ".json")
    with archive_path.open("w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    # 보존된 브리핑 날짜 목록을 게시하여 검색 시 존재하지 않는 날짜를 요청하지 않음
    threshold = (datetime.now(KST).date() - timedelta(days=29)).isoformat()
    for old in archive_dir.glob("????-??-??.json"):
        if old.stem < threshold:
            old.unlink()
    archive_index = sorted(p.stem for p in archive_dir.glob("????-??-??.json"))
    with (archive_dir / "index.json").open("w", encoding="utf-8") as f:
        json.dump(archive_index, f, ensure_ascii=False, indent=2)
    print(f"✅ 완료: {out_path}, {archive_path}")


if __name__ == "__main__":
    main()
