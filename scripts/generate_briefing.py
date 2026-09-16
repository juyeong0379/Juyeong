"""
데일리 경제 뉴스 브리핑 자동 생성 스크립트 (증권사 IB 면접 준비용)
===================================================================

동작 순서
  1) 카테고리별로 기사 후보를 모은다.
     - 국내: 네이버 뉴스 검색 API  → 메이저 언론사 도메인만 남김
     - 해외: Google News RSS(무료, 키 불필요) → 해외 메이저 언론사만 남김
     - 제목만 봐도 단신인 기사([속보], [포토], 시황, 특징주 등)는 미리 제외
  2) 후보 기사들을 Claude API에 10개씩 보내 카테고리 적합성·요약·점수를 받는다.
     (중요성 / 심층도 / 퀄리티, 해외 기사는 한국어 제목도 함께)
  3) 최종 점수 = 시의성 15% + 중요성 35% + 심층도 35% + 퀄리티 15%
     심층도가 낮은 기사는 탈락. 카테고리별로 국내 3개 + 해외 2개를 고른다.
     (기사 하나는 한 카테고리에만 들어간다)
  4) 오늘의 주요 기사 중 하나로 '오늘 생각해볼 질문' 1개를 만든다.
  5) 프론트엔드(docs/index.html)가 읽을 docs/data.json 을 생성한다.

필요한 환경변수
  - NAVER_CLIENT_ID       (NAVER API HUB의 X-NCP-APIGW-API-KEY-ID)
  - NAVER_CLIENT_SECRET   (NAVER API HUB의 X-NCP-APIGW-API-KEY)
  - ANTHROPIC_API_KEY     (console.anthropic.com 에서 발급)
"""

import os
import re
import json
import html
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

import requests
import anthropic

KST = timezone(timedelta(hours=9))
MODEL = "claude-haiku-4-5-20251001"
BATCH_SIZE = 10

TOP_N_KR = 3                   # 카테고리별 국내 기사 수
TOP_N_FOREIGN = 2              # 카테고리별 해외 기사 수
NAVER_PER_KEYWORD = 40         # 키워드당 네이버에서 가져올 기사 수 (언론사 필터 전)
FOREIGN_PER_KEYWORD = 10       # 키워드당 해외 기사 최대 수 (언론사 필터 후)
MAX_KR_CANDIDATES = 60         # 카테고리별 Claude에게 보낼 국내 후보 최대 수
MAX_FOREIGN_CANDIDATES = 30    # 카테고리별 Claude에게 보낼 해외 후보 최대 수
MIN_DEPTH = 45                 # 심층도가 이보다 낮으면 탈락

# ─────────────────────────────────────────────────────────────
# 메이저 언론사 목록 (도메인 → 화면에 보일 이름)
# ─────────────────────────────────────────────────────────────
KR_OUTLETS = {
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
    # IB·자본시장 전문지
    "thebell.co.kr": "더벨",
    "investchosun.com": "인베스트조선",
    "dealsite.co.kr": "딜사이트",
    # 종합지·통신
    "chosun.com": "조선일보",
    "joongang.co.kr": "중앙일보",
    "donga.com": "동아일보",
    "hani.co.kr": "한겨레",
    "khan.co.kr": "경향신문",
    "hankookilbo.com": "한국일보",
    "yna.co.kr": "연합뉴스",
}

FOREIGN_OUTLETS = {
    "reuters.com": "Reuters",
    "bloomberg.com": "Bloomberg",
    "ft.com": "Financial Times",
    "wsj.com": "WSJ",
    "nytimes.com": "New York Times",
    "economist.com": "The Economist",
    "cnbc.com": "CNBC",
    "apnews.com": "AP",
    "nikkei.com": "Nikkei Asia",
    "barrons.com": "Barron's",
    "washingtonpost.com": "Washington Post",
    "fortune.com": "Fortune",
    "axios.com": "Axios",
    "techcrunch.com": "TechCrunch",
}

# 제목에 이런 표현이 있으면 단신/시황/보도자료로 보고 미리 제외
SHORT_NEWS_PATTERN = re.compile(
    r"\[(포토|사진|영상|그래픽|속보|1보|2보|인사|부고|게시판|알림|표|공시|주요\s*공시|오늘의[^\]]*|"
    r"개장\s*시황|마감\s*시황|장중\s*시황|시황|날씨|운세|카드뉴스|신간)\]"
    r"|특징주|주요\s*일정|마감\s*시황|개장\s*시황|\bLIVE\b|\bLive:",
    re.IGNORECASE,
)


def outlet_name(url: str, outlets: dict):
    """URL의 도메인이 목록에 있으면 언론사 이름을, 없으면 None을 돌려준다."""
    host = urllib.parse.urlparse(url or "").netloc.lower().split(":")[0]
    # biz.chosun.com 처럼 더 구체적인 도메인을 먼저 확인
    for domain in sorted(outlets, key=len, reverse=True):
        if host == domain or host.endswith("." + domain):
            return outlets[domain]
    return None


# ─────────────────────────────────────────────────────────────
# 카테고리 정의
#   - label       : 화면에 보이는 이름
#   - keywords    : 국내(네이버) 검색어
#   - en_keywords : 해외(Google News) 검색어
#   - criteria    : Claude가 "이 카테고리에 맞는 기사인지" 판단하는 기준
# 딕셔너리 순서 = 화면 탭 순서
# ─────────────────────────────────────────────────────────────
CATEGORIES = {
    "global": {
        "label": "글로벌",
        "keywords": [
            "연준 금리", "미국 국채금리", "ECB 금리", "일본은행 금리",
            "관세 협상", "국제유가", "달러 강세", "국제 금값", "뉴욕증시 전망",
        ],
        "en_keywords": [
            "Federal Reserve interest rates", "Treasury yields", "tariffs trade talks",
            "oil prices OPEC", "dollar outlook", "gold prices", "ECB policy", "Bank of Japan policy",
        ],
        "criteria": """해외 거시 변수 중심: 주요국 금리·통화정책, 무역/관세, 유가·원자재, 환율(달러), 글로벌 주식·채권·금 등 자산가격.
- 부적합: 한국 기업·한국 산업이 주인공인 기사(예: K-배터리 ESS 수주, 국내 기업의 해외 진출), 개별 기업의 제품·기술 뉴스, 단순 외교·정치 기사""",
    },
    "macro": {
        "label": "국내 거시경제",
        "keywords": [
            "한국은행 기준금리", "소비자물가", "고용동향", "가계부채",
            "수출입 동향", "경상수지", "GDP 성장률", "추경 예산", "원달러 환율 전망",
        ],
        "en_keywords": [
            "Bank of Korea", "South Korea economy", "South Korea exports", "Korean won",
        ],
        "criteria": """한국 경제 전체를 보여주는 지표·정책: 기준금리 결정, 물가, 고용, 성장률, 수출입·경상수지, 가계부채, 재정정책, 환율.
- 부적합: 특정 산업·기업 동향(→산업), 주식·채권 시장 움직임이나 딜(→시장·금융/채권), 해외 이슈가 중심인 기사""",
    },
    "industry": {
        "label": "국내 산업",
        "keywords": [
            "반도체 업황", "HBM", "ESS 배터리", "조선 수주", "철강 업황",
            "석유화학 구조조정", "바이오 신약", "방산 수출", "원전 수주",
            "건설 수주", "AI 데이터센터",
        ],
        "en_keywords": [
            "Samsung SK Hynix memory", "South Korea shipbuilders", "South Korea defense exports",
            "Korean battery makers", "South Korea nuclear exports",
        ],
        "criteria": """한국 산업·기업의 실물 동향: 수주, 실적, 수급, 설비투자, 기술 경쟁, 업황, 산업정책의 영향.
- 모빌리티(완성차, 자율주행, 전기차, 차량용 기술 등)는 별도 카테고리이므로 제외
- 배터리는 ESS·소재·업황 관점이면 여기, 전기차 탑재·완성차 협력 관점이면 모빌리티
- 부적합: 주가·증시 전망 위주 기사, M&A·자금조달 등 딜 기사(→시장·금융), 거시지표 기사""",
    },
    "finance": {
        "label": "시장·금융 (IB)",
        "keywords": [
            "M&A 인수", "사모펀드 인수", "경영권 매각", "블록딜",
            "유상증자", "리그테이블", "인수금융", "증권사 IB", "PF 대출",
        ],
        "en_keywords": [
            "South Korea M&A deal", "Asia private equity deal", "Asia M&A",
            "investment banking league table", "block trade Asia",
        ],
        "criteria": """증권사 IB 관점의 딜·자본시장 뉴스: M&A·경영권 매각, PE 거래, 블록딜, 유상증자·메자닌(CB/EB), 인수금융, 부동산PF, IB 리그테이블·주관사 경쟁, 증권사 IB 실적, 글로벌 IB·딜 시장 동향.
- 딜 규모, 참여자(매도자/인수자/주관사/자문사), 거래 구조가 드러나는 기사일수록 높은 점수
- 부적합: 단순 주가 등락·지수 시황, 채권금리 시황(→채권), IPO·벤처투자(→VC·IPO), 개인 재테크 기사""",
    },
    "bond": {
        "label": "채권",
        "keywords": [
            "국고채 금리", "국고채 입찰", "회사채 수요예측", "크레딧 스프레드",
            "채권시장", "여전채", "신용등급 하향", "한전채",
        ],
        "en_keywords": [
            "bond market outlook", "corporate bond issuance", "credit spreads",
            "Korea bonds", "Treasury auction",
        ],
        "criteria": """채권시장 동향: 국고채·통안채·미국채 금리, 회사채·여전채·공사채 발행과 수요예측 결과, 크레딧 스프레드, 신용등급 변동, 채권 수급(외국인·기관), DCM 주관 실적, WGBI 등 제도.
- 발행사·금리·수요예측 경쟁률 등 숫자가 드러나는 기사일수록 높은 점수
- 부적합: 채권이 잠깐 언급만 되는 일반 경제 기사, 개인 대상 채권 투자 권유 기사""",
    },
    "vc_ipo": {
        "label": "VC·IPO",
        "keywords": [
            "IPO 수요예측", "공모주 청약", "코스닥 상장", "상장 예비심사",
            "증권신고서 제출", "벤처투자", "시리즈 투자유치", "스타트업 투자",
        ],
        "en_keywords": [
            "IPO market", "IPO filing", "venture capital funding", "startup funding round",
            "Korea IPO",
        ],
        "criteria": """VC 투자와 IPO 시장: 스타트업 투자유치(라운드·밸류에이션·투자사), VC 펀드 결성, 상장 예비심사·증권신고서, 수요예측·청약 경쟁률, 공모가, 상장 후 주가, 주관사 실적, IPO 제도 변화, 글로벌 IPO·VC 시장 흐름.
- 부적합: 단순 창업 지원 행사, 투자 권유성 공모주 광고 기사""",
    },
    "mobility": {
        "label": "모빌리티",
        "keywords": [
            "현대차 실적", "기아 실적", "현대차그룹 투자", "자동차 관세",
            "테슬라 로보택시", "자율주행", "소프트웨어 중심 자동차", "전기차 판매",
            "차량용 반도체", "도심항공교통", "수소차", "보스턴다이나믹스",
        ],
        "en_keywords": [
            "Hyundai Motor", "Kia", "Tesla robotaxi", "autonomous driving",
            "electric vehicle sales", "Waymo", "BYD", "software-defined vehicle",
        ],
        "criteria": """모빌리티 산업·기술 전체, 특히 현대차·기아·테슬라 중심.
- 완성차: 판매·실적·점유율, 공장·투자, 관세·통상 영향, 전기차/하이브리드 전략, 노사
- 모빌리티 기술: 자율주행·로보택시, SDV(소프트웨어 중심 자동차), 전기차 배터리(차량 탑재·완성차 협력 관점), 차량용 반도체·전장, 충전 인프라, UAM, 수소차, 로보틱스(보스턴다이나믹스 등)
- 글로벌 경쟁사(도요타·BYD·GM·폭스바겐·웨이모 등)와 글로벌 수요 동향
- 현대차·기아·테슬라를 직접 다루거나 이들에게 영향을 주는 이슈일수록 높은 점수
- 부적합: 신차 단순 시승기·마케팅 행사, 자동차 보험·중고차 등 소비자 기사, '기아(굶주림)' 등 무관한 기사""",
    },
}

# 배정 순서: 전문 카테고리를 먼저 채운다.
# 예) 현대차 기사는 '국내 산업'이 아니라 '모빌리티'로, 회사채 기사는 '시장·금융'이 아니라 '채권'으로 간다.
PROCESS_ORDER = ["mobility", "bond", "vc_ipo", "finance", "industry", "macro", "global"]


# ─────────────────────────────────────────────────────────────
# 1) 뉴스 수집
# ─────────────────────────────────────────────────────────────
def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return html.unescape(text).replace("\xa0", " ").strip()


def normalize_title(title: str) -> str:
    return re.sub(r"[\W_]+", "", title).lower()


def fetch_naver_news(query: str) -> list:
    url = "https://naverapihub.apigw.ntruss.com/search/v1/news"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": os.environ["NAVER_CLIENT_ID"],
        "X-NCP-APIGW-API-KEY": os.environ["NAVER_CLIENT_SECRET"],
    }
    params = {"query": query, "display": NAVER_PER_KEYWORD, "sort": "date"}
    res = requests.get(url, headers=headers, params=params, timeout=10)
    res.raise_for_status()

    out = []
    for it in res.json().get("items", []):
        link = it.get("originallink") or ""
        source = outlet_name(link, KR_OUTLETS)
        if not source:                      # 메이저 언론사가 아니면 제외
            continue
        title = strip_html(it.get("title"))
        if SHORT_NEWS_PATTERN.search(title):  # 단신·시황 제외
            continue
        try:
            pub_dt = parsedate_to_datetime(it["pubDate"]).astimezone(KST)
        except Exception:
            continue
        out.append({
            "title": title,
            "description": strip_html(it.get("description")),
            "url": link,
            "source": source,
            "foreign": False,
            "pub_dt": pub_dt,
            "matched_keyword": query,
        })
    return out


def fetch_foreign_news(query: str) -> list:
    """Google News RSS에서 최근 3일 기사를 받아 해외 메이저 언론사만 남긴다."""
    url = "https://news.google.com/rss/search"
    params = {"q": f"{query} when:3d", "hl": "en-US", "gl": "US", "ceid": "US:en"}
    headers = {"User-Agent": "Mozilla/5.0 (daily-briefing-bot)"}
    res = requests.get(url, params=params, headers=headers, timeout=15)
    res.raise_for_status()
    root = ET.fromstring(res.content)

    out = []
    for item in root.iter("item"):
        src_el = item.find("source")
        if src_el is None:
            continue
        source = outlet_name(src_el.get("url", ""), FOREIGN_OUTLETS)
        if not source:                      # 해외 메이저 언론사가 아니면 제외
            continue

        title = strip_html(item.findtext("title"))
        suffix = f" - {(src_el.text or '').strip()}"
        if title.endswith(suffix):          # "제목 - Reuters" → "제목"
            title = title[: -len(suffix)].strip()
        if SHORT_NEWS_PATTERN.search(title):
            continue
        try:
            pub_dt = parsedate_to_datetime(item.findtext("pubDate")).astimezone(KST)
        except Exception:
            continue

        desc = strip_html(item.findtext("description"))
        if normalize_title(desc).startswith(normalize_title(title)):
            desc = ""                       # 설명이 제목 반복이면 비움
        out.append({
            "title": title,
            "description": desc,
            "url": item.findtext("link"),
            "source": source,
            "foreign": True,
            "pub_dt": pub_dt,
            "matched_keyword": query,
        })
        if len(out) >= FOREIGN_PER_KEYWORD:
            break
    return out


def gather(fetch_fn, keywords: list, limit: int) -> list:
    seen_urls, seen_titles, items = set(), set(), []
    for kw in keywords:
        try:
            fetched = fetch_fn(kw)
        except Exception as e:  # 키워드 하나가 실패해도 전체는 계속 진행
            print(f"  [경고] '{kw}' 검색 실패, 건너뜁니다: {e}")
            continue
        for it in fetched:
            key_t = normalize_title(it["title"])
            if not it["url"] or it["url"] in seen_urls or key_t in seen_titles:
                continue
            seen_urls.add(it["url"])
            seen_titles.add(key_t)
            items.append(it)
        time.sleep(0.3)
    items.sort(key=lambda x: x["pub_dt"], reverse=True)  # 최신순으로 자르기
    return items[:limit]


# ─────────────────────────────────────────────────────────────
# 2) Claude로 분류·요약·점수
# ─────────────────────────────────────────────────────────────
client = anthropic.Anthropic()


def ask_claude_json(prompt: str, max_tokens: int):
    """Claude에게 JSON으로만 답하게 하고 파싱해서 돌려준다. 실패하면 None."""
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
    except Exception as e:
        print(f"  [경고] Claude API 호출 실패: {e}")
        return None

    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"  [경고] JSON 파싱 실패\n--- 원본 응답 ---\n{raw[:1500]}\n--- 끝 ---")
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
    if age <= timedelta(hours=72):
        return 60      # 심층 기사는 하루 이틀 늦게 나오기도 해서 완전히 0점 주지 않음
    return 0


def classify_batch(category_label: str, criteria: str, batch: list) -> list:
    numbered = "\n".join(
        f"{i}. [{c['source']} | {c['pub_dt'].strftime('%Y-%m-%d %H:%M')}] {c['title']}"
        + (f" — {c['description']}" if c["description"] else "")
        for i, c in enumerate(batch)
    )

    prompt = f"""아래는 '{category_label}' 카테고리 후보 기사 목록이야. (국내·해외 기사가 섞여 있어) 각 기사에 대해 판단해줘.

이 카테고리의 기준:
{criteria}

공통 기준:
- 검색어가 들어 있어도 기사의 '주제'가 위 기준과 다르면 반드시 is_relevant: false
- 단신·속보·시황 중계·보도자료 받아쓰기·행사/인사 소식은 is_relevant: false
- 원인·배경·영향·전망을 짚는 분석/기획/해설/인터뷰 기사를 선호

점수 기준 (0~100 정수):
- importance: 증권사 IB 지원자가 면접에서 활용할 만한 정도
- depth: 심층성. 분석·해설·기획이고 숫자와 맥락이 풍부하면 높게, 사실 한두 줄 전달이면 낮게
- quality: 제목·내용이 구체적이고 신뢰할 만한지

각 기사(번호 순서 그대로)에 대해 아래 JSON 배열 형식으로만 답해. 다른 설명은 붙이지 마:
[
  {{
    "index": 0,
    "is_relevant": true,
    "title_ko": "영어 기사일 때만: 자연스러운 한국어 제목 (한국어 기사면 빈 문자열)",
    "summary": "한국어 2문장 요약. 핵심 사실 + 왜 중요한지. 원문을 베끼지 말고 재구성",
    "tag": "세부 토픽 1~3개, 쉼표로 구분 (예: 금리, 수요예측)",
    "importance": 0,
    "depth": 0,
    "quality": 0
  }}
]

부적합한 기사는 "index"와 "is_relevant": false 만 넣어도 돼.
반드시 마지막 항목까지 JSON 배열을 완전히 닫아서 응답해.

후보 기사 목록:
{numbered}
"""

    judgments = ask_claude_json(prompt, max_tokens=4000)
    if not isinstance(judgments, list):
        print(f"  [경고] '{category_label}' 배치 하나를 건너뜁니다.")
        return []

    now = datetime.now(KST)
    results = []
    for j in judgments:
        if not isinstance(j, dict) or not j.get("is_relevant"):
            continue
        idx = to_int(j.get("index"), -1)
        if not 0 <= idx < len(batch):
            continue
        depth = to_int(j.get("depth"))
        if depth < MIN_DEPTH:
            continue
        c = batch[idx]

        importance = to_int(j.get("importance"))
        quality = to_int(j.get("quality"))
        final_score = (
            0.15 * freshness_score(c["pub_dt"], now)
            + 0.35 * importance
            + 0.35 * depth
            + 0.15 * quality
        )

        title_ko = (j.get("title_ko") or "").strip() if c["foreign"] else ""
        results.append({
            **c,
            "display_title": title_ko or c["title"],
            "summary": j.get("summary", ""),
            "tag": j.get("tag", ""),
            "score": final_score,
        })
    return results


def classify_and_score(category_label: str, criteria: str, candidates: list) -> list:
    all_results = []
    for start in range(0, len(candidates), BATCH_SIZE):
        batch = candidates[start:start + BATCH_SIZE]
        all_results.extend(classify_batch(category_label, criteria, batch))
    return all_results


# ─────────────────────────────────────────────────────────────
# 3) 카테고리별 기사 선정 (국내 3 + 해외 2)
# ─────────────────────────────────────────────────────────────
def pick_top(scored: list) -> list:
    kr = [x for x in scored if not x["foreign"]]
    fr = [x for x in scored if x["foreign"]]
    total = TOP_N_KR + TOP_N_FOREIGN

    picked = kr[:TOP_N_KR] + fr[:TOP_N_FOREIGN]
    # 한쪽이 모자라면 다른 쪽으로 채움
    leftovers = kr[TOP_N_KR:] + fr[TOP_N_FOREIGN:]
    leftovers.sort(key=lambda x: x["score"], reverse=True)
    picked += leftovers[: max(0, total - len(picked))]

    picked.sort(key=lambda x: x["score"], reverse=True)
    return picked


def attach_related(selected: list, all_scored: list, used_urls: set) -> None:
    for item in selected:
        pool = [
            c for c in all_scored
            if c["url"] not in used_urls
            and c["matched_keyword"] == item["matched_keyword"]
        ]
        pool.sort(key=lambda x: x["score"], reverse=True)
        item["related"] = [
            {"title": r["display_title"], "src": r["source"], "url": r["url"]}
            for r in pool[:2]
        ]


def build_category(meta: dict, used_urls: set) -> list:
    print(f"▶ {meta['label']} 수집 중...")
    kr = gather(fetch_naver_news, meta["keywords"], MAX_KR_CANDIDATES)
    fr = gather(fetch_foreign_news, meta["en_keywords"], MAX_FOREIGN_CANDIDATES)
    candidates = [c for c in kr + fr if c["url"] not in used_urls]
    print(f"  후보: 국내 {len(kr)}건 / 해외 {len(fr)}건 → Claude에게 분류 요청 중...")

    scored = classify_and_score(meta["label"], meta["criteria"], candidates)
    scored.sort(key=lambda x: x["score"], reverse=True)
    top = pick_top(scored)
    used_urls.update(item["url"] for item in top)

    attach_related(top, scored, used_urls)
    n_fr = sum(1 for x in top if x["foreign"])
    print(f"  → {len(top)}건 선정 (국내 {len(top) - n_fr} / 해외 {n_fr})")

    return [
        {
            "top": (i == 0),
            "headline": item["display_title"],
            "original_headline": item["title"] if item["foreign"] else "",
            "source": item["source"],
            "foreign": item["foreign"],
            "pubDate": item["pub_dt"].strftime("%Y-%m-%d"),
            "summary": item["summary"],
            "tag": item["tag"] or meta["label"],
            "url": item["url"],
            "related": item["related"],
        }
        for i, item in enumerate(top)
    ]


# ─────────────────────────────────────────────────────────────
# 4) 오늘 생각해볼 질문
# ─────────────────────────────────────────────────────────────
def generate_daily_question(data: dict):
    """오늘 뽑힌 주요 기사 중 하나를 골라, 가볍게 생각해볼 질문 1개를 만든다."""
    pool = []
    for key, items in data.items():
        for item in items[:2]:  # 카테고리별 상위 2개만 후보로
            pool.append({"category": key, **item})
    if not pool:
        return None

    numbered = "\n".join(
        f"{i}. [{CATEGORIES[p['category']]['label']}] {p['headline']} — {p['summary']}"
        for i, p in enumerate(pool)
    )
    prompt = f"""너는 증권사 IB 지원자의 면접 준비를 돕는 선배야.
아래 오늘의 주요 기사 중 하나를 골라, 오늘 하루 가볍게 생각해볼 질문을 딱 1개 만들어줘.

조건:
- 깊은 분석 말고, 가장 기본적이고 뻔한 질문
  (예: "미국 금리가 오르면 원/달러 환율은 왜 오를까?",
       "회사채 수요예측 경쟁률이 높다는 건 무슨 뜻일까?",
       "관세가 오르면 현대차 실적엔 어떤 영향이 있을까?")
- 기사를 읽지 않아도 이해할 수 있는 한 문장, 50자 안팎
- hint는 생각의 출발점이 되는 한 문장 (정답을 다 말하지 말 것)

아래 JSON 형식으로만 답해. 다른 설명은 붙이지 마:
{{"index": 0, "question": "...", "hint": "..."}}

기사 목록:
{numbered}
"""
    q = ask_claude_json(prompt, max_tokens=500)
    if not isinstance(q, dict) or not q.get("question"):
        print("[경고] 오늘의 질문 생성 실패, 생략합니다.")
        return None

    idx = to_int(q.get("index"), 0)
    picked = pool[idx] if 0 <= idx < len(pool) else pool[0]

    return {
        "question": q["question"],
        "hint": q.get("hint", ""),
        "category": picked["category"],
        "headline": picked["headline"],
        "url": picked["url"],
    }


# ─────────────────────────────────────────────────────────────
# 5) 실행
# ─────────────────────────────────────────────────────────────
def main():
    used_urls = set()
    built = {key: build_category(CATEGORIES[key], used_urls) for key in PROCESS_ORDER}
    data = {key: built[key] for key in CATEGORIES}  # 화면 탭 순서대로 저장

    print("▶ 오늘 생각해볼 질문 만드는 중...")
    output = {
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "daily_question": generate_daily_question(data),
        "categories": data,
    }

    out_path = os.path.join(os.path.dirname(__file__), "..", "docs", "data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"✅ 완료: {out_path}")


if __name__ == "__main__":
    main()
