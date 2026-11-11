"""
IB그룹 데일리 뉴스 모니터링 자동 생성 스크립트
==================================================

동작 순서
  1) 섹션별 키워드로 네이버 뉴스 검색 API를 호출해 후보 기사를 모은다.
     - 메이저 언론사(경제지·IB 전문지·종합지) 기사만 남긴다.
     - [속보]·[포토]·시황·특징주 같은 단신은 제목 단계에서 제외한다.
  2) 후보 기사를 Claude API에 10개씩 보내, 섹션 기준에 맞는지 판단하고
     개조식 요약·기자명·점수를 받는다.
  3) 최종 점수 = 시의성 15% + 중요성 35% + 심층도 35% + 퀄리티 15%
     심층도가 낮은 기사는 탈락. 섹션별 상위 N개만 남긴다.
     (기사 하나는 한 섹션에만 들어간다)
  4) 프론트엔드(docs/index.html)가 읽을 docs/data.json 을 생성한다.

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
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

import requests
import anthropic
from sector_rules import classify_sector, sector_matches
from report_sources import collect_reports

KST = timezone(timedelta(hours=9))
MODEL = "claude-haiku-4-5-20251001"
BATCH_SIZE = 10

TOP_N_PER_SECTION = 3      # 섹션별로 보여줄 기사 수
NAVER_PER_KEYWORD = 30     # 키워드당 네이버에서 가져올 기사 수 (언론사 필터 전)
MAX_CANDIDATES = 40        # 섹션별로 Claude에게 보낼 후보 최대 수
MIN_DEPTH = 45
PREFERRED_IB_OUTLETS = {"더벨", "인베스트조선", "딜사이트", "한국경제", "한국금융신문", "법률신문", "딜북"}             # 심층도가 이보다 낮으면 탈락

# ─────────────────────────────────────────────────────────────
# 메이저 언론사 (도메인 → 화면에 표시할 이름)
# ─────────────────────────────────────────────────────────────
KR_OUTLETS = {
    # IB·자본시장 전문지
    "thebell.co.kr": "더벨",
    "investchosun.com": "인베스트조선",
    "dealsite.co.kr": "딜사이트",
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
        "keywords": ["IPO 수요예측", "상장예비심사", "공모가 산정", "유상증자 주관", "전환사채 발행", "코너스톤 투자자", "블록딜 매각"],
        "criteria": "ECM·IPO·주식연계 자금조달과 상장 제도의 구체적인 변화. 실제 기업명·공모금액·주관사·거래 구조가 있는 분석 기사 우대. 단순 주가 전망·청약 독려 제외.",
    },
    "corp": {
        "no": "02", "emoji": "🏦", "label": "기업금융",
        "desc": "DCM·회사채·기업자금 조달",
        "keywords": ["회사채 수요예측", "DCM 주관", "기업 자금조달", "기업 신용등급", "회사채 차환", "신디케이트론", "PRS 조달"],
        "criteria": "회사채·여전채·DCM, 기업 자금조달과 리파이낸싱. 구체적인 발행금액·만기·스프레드·신용등급·주관사 등 맥락 중심. 인수합병·인수금융과 부동산PF는 제외.",
    },
    "ma": {
        "no": "03", "emoji": "🤝", "label": "M&A·인수금융",
        "desc": "M&A·경영권 매각·PEF·인수금융",
        "keywords": ["인수금융 주선", "기업 인수합병", "경영권 매각", "PEF 바이아웃", "M&A 매각 자문", "인수금융 리파이낸싱", "기업 인수 매각"],
        "criteria": "기업 인수합병, 경영권 거래, 사모펀드 투자·회수, 인수금융 조달 및 차환. 실제 거래 구조·금액·참여자와 사업적 영향을 구체적으로 다룬 기사.",
    },
    "re": {
        "no": "04", "emoji": "🏢", "label": "부동산",
        "desc": "부동산PF·리츠·상업용부동산",
        "keywords": ["부동산 PF", "브릿지론 차환", "부동산 리파이낸싱", "물류센터 매각", "오피스 리츠", "PF 사업장 정상화", "부동산 PF 주선"],
        "criteria": "부동산PF, 브릿지론·본PF, 리츠, 상업용 부동산 투자·차환과 위험관리. 일반 아파트 청약·시세·매물 정보는 제외.",
    },
    "infra": {
        "no": "05", "emoji": "⚡", "label": "인프라",
        "desc": "데이터센터·전력·재생에너지·민자사업",
        "keywords": ["데이터센터 PF", "GPU 파이낸싱", "인프라 금융", "해상풍력 금융", "발전 프로젝트 금융", "민자사업 금융", "인프라 신디케이트론"],
        "criteria": "데이터센터, 전력, 해상풍력·재생에너지, 도로·철도 등 프로젝트금융·인프라 투자와 자금조달. 기술 동향만 다루는 기사 제외.",
    },
    "hana": {
        "no": "06", "emoji": "🏛️", "label": "하나금융그룹",
        "desc": "하나금융지주·하나은행·하나증권 IB",
        "keywords": ["하나증권 IB", "하나금융그룹 기업금융", "하나은행 인수금융", "하나증권 주선", "하나금융 생산적금융", "하나캐피탈 투자금융", "하나카드 기업금융", "하나자산신탁 부동산금융"],
        "criteria": "하나금융그룹 및 계열사의 IB 사업·자금조달·주선·생산적금융·협업 실적과 전략만 포함. 일반 인사·사회공헌·개인고객 이벤트 제외.",
    },
    "peer": {
        "no": "07", "emoji": "🏙️", "label": "경쟁사",
        "desc": "국내외 금융사 IB 전략·주선 경쟁",
        "keywords": ["증권사 IB 전략", "증권사 투자금융", "증권사 인수금융 주선", "종합투자계좌 IMA", "증권사 발행어음", "증권사 생산적금융", "증권사 기업금융 조직"],
        "criteria": "경쟁 증권사·금융그룹의 IB 전략, 조직 재편, 투자·주선 실적 및 자본 한도, 라이선스·규제 영향. 단순 실적 기사와 인사 단신 제외. 하나금융그룹 관련 기사는 하나금융그룹 탭으로 분류.",
    },
    "sto": {
        "no": "08", "emoji": "🔗", "label": "STO",
        "desc": "토큰증권·디지털증권 제도 및 인프라",
        "keywords": ["토큰증권 STO", "토큰증권 법제화", "조각투자 증권", "토큰증권 장외거래", "증권사 STO 플랫폼", "디지털 증권 수탁"],
        "criteria": "토큰증권 발행·유통·규제·투자자보호와 증권사 신사업화. 코인 시세, 일반 AI 소식, 단순 IT 제품 기사 제외.",
    },
    "productive": {
        "no": "09", "emoji": "🌱", "label": "생산적금융",
        "desc": "모험자본·성장산업·생산적금융 정책",
        "keywords": ["생산적금융 투자", "국민성장펀드", "모험자본 공급 증권사", "첨단전략산업 금융", "벤처투자 정책금융", "혁신기업 성장지원", "기업성장집합투자기구 BDC"],
        "criteria": "생산적금융, 국민성장펀드, 모험자본 공급, 혁신기업과 첨단산업의 투자·융자·후속투자·회수 금융. 관련 없는 일반 산업·기술 기사 제외.",
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


def collect_candidates(keywords: list, limit: int = None) -> list:
    seen_urls, seen_titles, items = set(), set(), []
    for kw in keywords:
        try:
            fetched = fetch_naver_news(kw)
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
        time.sleep(0.2)
    items.sort(key=lambda x: x["pub_dt"], reverse=True)  # 최신순으로 자르기
    return items[:limit] if limit else items


# ─────────────────────────────────────────────────────────────
# 2) Claude로 분류·요약·점수
# ─────────────────────────────────────────────────────────────
client = anthropic.Anthropic()
CLAUDE_UNAVAILABLE = False


def ask_claude_json(prompt: str, max_tokens: int):
    """Claude에게 JSON으로만 답하게 하고 파싱해서 돌려준다. 실패하면 None."""
    global CLAUDE_UNAVAILABLE
    if CLAUDE_UNAVAILABLE:
        return None
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
    except Exception as e:
        if "credit balance is too low" in str(e).lower():
            CLAUDE_UNAVAILABLE = True
            print("  [경고] Claude API 크레딧 부족: 출처 발췌 모드로 전환", flush=True)
        else:
            print(f"  [경고] Claude API 호출 실패: {e}")
        return None

    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"  [경고] JSON 파싱 실패\n--- 원본 응답 ---\n{raw[:1200]}\n--- 끝 ---")
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
    if age <= timedelta(days=7):
        return 25      # 뉴스가 부족할 경우 양질의 과거 기사 보완
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
}

def fallback_classify_batch(meta: dict, batch: list) -> list:
    """No generative summary when AI is unavailable: show attributable source snippets.

    These are not AI-verified three-line summaries or strategic insights.
    """
    pattern=FALLBACK_TOPIC_PATTERNS.get(meta["label"],r"$^")
    out=[]
    for c in batch:
        title=c.get("title","")
        desc=re.sub(r"\s+"," ",c.get("description","")).strip()
        if classify_sector(title,desc) != meta["key"]:
            continue
        if not re.search(pattern,title+" "+desc,re.IGNORECASE):
            continue
        if len(desc)<55 or SHORT_NEWS_PATTERN.search(title):
            continue
        if re.search(r"업무협약|협약식|기념식|인사발령|지점 오픈|행사 개최",title):
            continue
        age_hours=(datetime.now(KST)-c["pub_dt"]).total_seconds()/3600
        score=45+(2 if c["source"] in PREFERRED_IB_OUTLETS else 0)
        score+=min(14,len(desc)/20)+max(0,13-age_hours/8)
        out.append({**c,"summary":[desc[:210]],"insight":"",
                    "summary_mode":"source_excerpt","reporter":"","score":score})
    return out

def classify_batch(meta: dict, batch: list) -> list:
    numbered = "\n".join(
        f"{i}. [{c['source']} | {c['pub_dt'].strftime('%m-%d %H:%M')}] {c['title']}"
        + (f" — {c['description']}" if c["description"] else "")
        for i, c in enumerate(batch)
    )

    prompt = f"""너는 증권사 IB그룹 기획실에서 데일리 뉴스 모니터링을 담당하는 애널리스트야.
아래는 '{meta['label']}' 섹션의 후보 기사 목록이야. 각 기사를 판단해줘.

이 섹션이 다루는 범위: {meta['desc']}

판단 기준:
{meta['criteria']}

공통 기준:
- 검색어가 들어 있어도 기사의 '주제'가 위 기준과 다르면 반드시 is_relevant: false
- 단신·속보·시황 중계·보도자료 받아쓰기·행사/인사 소식은 is_relevant: false
- 원인·배경·영향·전망을 짚는 분석/기획/해설 기사를 선호
- 한국시간 오전 8시 브리핑용이므로 오늘과 전날 발행된 중요한 기사 우대. 신뢰할 만한 분석이 부족한 경우 7일 이내 기사 선택 가능.

점수 기준 (0~100 정수):
- importance: IB 실무자가 알아야 할 중요도 (딜 규모, 시장 영향, 정책 파급력)
- depth: 심층성. 분석·해설이고 숫자와 맥락이 풍부하면 높게, 사실 전달뿐이면 낮게
- quality: 내용이 구체적이고 신뢰할 만한지

summary 작성 규칙 (개조식):
- 2~3개 항목. 각 항목 50~70자로 맥락이 드러나게
- 명사형 종결 ("~했다" X → "~흥행", "~확대", "~부담 가중" O)
- 1번째: 무슨 일인지. 숫자·금액·당사자를 구체적으로
- 2번째: 배경이나 맥락. 왜 지금 이런 일이 벌어졌는지
- 3번째: 중요 사실 또는 구체적인 시장 변화 (시사점은 별도 insight 필드로 작성)
- insight: 하나증권 IB기획실 관점의 전략 검토사항이 명확할 때만 작성. 없으면 빈 문자열 ""로 반환.
- 단순 '시장 확대', '금융 수요 증가', '대응 필요' 등 기사의 내용을 반복하는 일반론은 금지.
- IB 비즈니스 트렌드로 발생하는 구체적인 자금조달 수요, 위험, 경쟁구도 변화와 연결.
- 하나증권의 ECM·DCM·인수금융·부동산/인프라금융·생산적금융 또는 그룹 협업 전략에 어떤 의미인지 간결하게 제안.
- 확인되지 않은 내부 강점·약점·실적을 지어내지 말고, 필요한 경우 '기존 주선 경험 확인', '익스포저/한도 검토' 등 점검 과제로 작성.
- 한 줄 30~65자, 개조식 종결. 실제 전략적 가치가 없는 기사라면 빈 문자열.
- 좋은 예: 'AI 데이터센터 전력·임차계약 리스크 점검 및 그룹 공동 금융주선 가능성 검토'
- 좋은 예: 'IPO 회수시장 위축에 대응한 프리IPO·후속투자 금융수요 모니터링'
- 각 항목에서 가장 중요한 부분(숫자, 금액, 당사자, 핵심 변화) 1곳을 **별표 두 개**로 감쌀 것
- 기사에 근거가 없는 내용은 쓰지 말 것. 3번째가 억지스러우면 2개만 써도 됨
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
        print(f"  [경고] '{meta['label']}' AI 요약 불가 → 원문 검색 발췌 모드")
        return fallback_classify_batch(meta, batch)

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
        if classify_sector(c["title"],c["description"]) != meta["key"]:
            continue

        final_score = (
            0.15 * freshness_score(c["pub_dt"], now)
            + 0.35 * to_int(j.get("importance"))
            + 0.35 * depth
            + 0.15 * to_int(j.get("quality"))
            + (2 if c["source"] in PREFERRED_IB_OUTLETS else 0)
        )
        reporter = re.sub(r"\s+", " ", str(j.get("reporter") or "")).strip()[:20]

        summary = j.get("summary", "")
        if isinstance(summary, list):
            summary = [str(x).strip() for x in summary if str(x).strip()]
        elif summary:
            summary = [str(summary).strip()]
        else:
            summary = []

        results.append({
            **c,
            "summary": summary,
            "insight": str(j.get("insight") or "").strip(),
            "reporter": reporter,
            "score": final_score,
        })
    return results


# ─────────────────────────────────────────────────────────────
# 3) 섹션별 기사 선정
# ─────────────────────────────────────────────────────────────
def build_section(meta: dict, used_urls: set) -> list:
    print(f"▶ [{meta['no']}] {meta['label']} 수집 중...")
    candidates = [
        c for c in collect_candidates(meta["keywords"])
        if c["url"] not in used_urls
        and sector_matches(meta["key"], c["title"], c.get("description", ""))
    ][:MAX_CANDIDATES]
    print(f"  후보 {len(candidates)}건 → Claude 분류 요청 중...")

    scored = []
    for start in range(0, len(candidates), BATCH_SIZE):
        scored.extend(classify_batch(meta, candidates[start:start + BATCH_SIZE]))
    scored.sort(key=lambda x: x["score"], reverse=True)

    # 기사 수 부족 시 후보군 확대. 적합한 실재 기사가 없으면 임의로 채우지 않음.
    if len(scored) < 2:
        seen = {c["url"] for c in candidates}
        extra = [c for c in collect_candidates(meta["keywords"])
                 if c["url"] not in seen and c["url"] not in used_urls
                 and sector_matches(meta["key"], c["title"], c.get("description", ""))][:30]
        for start in range(0, len(extra), BATCH_SIZE):
            scored.extend(classify_batch(meta, extra[start:start + BATCH_SIZE]))
        scored.sort(key=lambda x: x["score"], reverse=True)

    if meta["label"] == "하나금융그룹":
        # 은행, 증권, 다른 계열사별로 적합한 기사 하나씩 우선 선정
        groups = [
            ("하나은행",),
            ("하나증권",),
            ("하나캐피탈", "하나카드", "하나자산신탁", "하나대체투자", "하나금융티아이", "하나생명"),
        ]
        selected, chosen = [], set()
        for terms in groups:
            found = next((x for x in scored if x["url"] not in chosen and any(t in x["title"] for t in terms)), None)
            if found:
                selected.append(found)
                chosen.add(found["url"])
        selected.extend(x for x in scored if x["url"] not in chosen)
        top = selected[:TOP_N_PER_SECTION]
    else:
        top = scored[:TOP_N_PER_SECTION]
    used_urls.update(item["url"] for item in top)
    print(f"  → {len(top)}건 선정")

    return [
        {
            "top": (i == 0),
            "headline": item["title"],
            "source": item["source"],
            "pubDate": item["pub_dt"].strftime("%Y-%m-%d"),
            "summary": item["summary"][:3],
            "summary_mode": item.get("summary_mode", "ai_summary"),
            "insight": item.get("insight", ""),
            "reporter": item["reporter"],
            "url": item["url"],
        }
        for i, item in enumerate(top)
    ]


# ─────────────────────────────────────────────────────────────
# 4) 실행
# ─────────────────────────────────────────────────────────────
def main():
    used_urls = set()
    data = {}
    for key, meta in SECTIONS.items():
        meta = {**meta, "key":key}
        data[key] = build_section(meta, used_urls)

    produced_at = datetime.now(KST)
    try:
        reports = collect_reports(data, produced_at)
        print(f"  → 관련 기관 보고서 {len(reports)}건 선정")
    except Exception as exc:
        print(f"  [경고] 자료 검색 실패: {exc}")
        reports = []

    output = {
        "generated_at": produced_at.strftime("%Y-%m-%d %H:%M KST"),
        "reports": reports,
        "sections": [
            {"key": k, "no": m["no"], "emoji": m["emoji"], "label": m["label"], "desc": m["desc"]}
            for k, m in SECTIONS.items()
        ],
        "categories": data,
    }

    out_path = os.path.join(os.path.dirname(__file__), "..", "docs", "data.json")
    if sum(len(v) for v in data.values()) == 0:
        raise RuntimeError("선정 기사 0건: 기존 브리핑 보존")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    # 선택 날짜의 뉴스를 보관하여 최근 30일 검색과 과거 조회에 활용
    from pathlib import Path
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
