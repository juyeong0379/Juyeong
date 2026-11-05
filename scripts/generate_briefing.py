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

KST = timezone(timedelta(hours=9))
MODEL = "claude-haiku-4-5-20251001"
BATCH_SIZE = 10

TOP_N_PER_SECTION = 3      # 섹션별로 보여줄 기사 수
NAVER_PER_KEYWORD = 30     # 키워드당 네이버에서 가져올 기사 수 (언론사 필터 전)
MAX_CANDIDATES = 60        # 섹션별로 Claude에게 보낼 후보 최대 수
MIN_DEPTH = 45             # 심층도가 이보다 낮으면 탈락

# ─────────────────────────────────────────────────────────────
# 메이저 언론사 (도메인 → 화면에 표시할 이름)
# ─────────────────────────────────────────────────────────────
KR_OUTLETS = {
    # IB·자본시장 전문지
    "thebell.co.kr": "더벨",
    "investchosun.com": "인베스트조선",
    "dealsite.co.kr": "딜사이트",
    "paxnetnews.com": "팍스넷뉴스",
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
    "ib_overall": {
        "emoji": "🏛️",
        "no": "01",
        "label": "IB그룹 전반",
        "desc": "거시경제, 금리·환율, 금융정책 및 규제, 생산적금융, 경쟁사 IB 전략·조직개편",
        "keywords": [
            "한국은행 기준금리", "원달러 환율 전망", "국고채 금리",
            "금융위원회 정책", "금융감독원 규제", "생산적 금융",
            "자본시장법 개정", "증권사 IB 조직개편", "증권사 IB 실적",
            "모험자본 공급", "밸류업 프로그램",
        ],
        "criteria": """IB 비즈니스의 '판' 자체를 움직이는 뉴스.
- 거시: 기준금리·시장금리, 환율, 물가·성장률 등 자금조달 환경을 바꾸는 지표
- 정책·규제: 금융위·금감원 정책, 자본시장법·제도 개편, 생산적금융·모험자본 공급 정책, 밸류업
- 업계: 증권사 IB 부문 실적, 조직개편·인력이동, 라이선스(종투사·IMA 등), 경쟁사 IB 전략
- 부적합: 개별 딜 단건(→ 해당 섹션), 일반 기업 산업 뉴스, 개인 재테크""",
    },
    "ipo_ecm": {
        "emoji": "🚀",
        "no": "02",
        "label": "IPO·ECM",
        "desc": "IPO 시장, 유상증자, 상장제도, 수요예측, 경쟁사 주관 실적",
        "keywords": [
            "IPO 수요예측", "공모주 청약 경쟁률", "상장 예비심사",
            "증권신고서 제출", "코스닥 상장", "유상증자 결정",
            "전환사채 발행", "IPO 주관사 선정", "상장제도 개편", "공모가 희망범위",
        ],
        "criteria": """주식자본시장(ECM) 뉴스.
- IPO: 예비심사·증권신고서, 수요예측·청약 경쟁률, 공모가 확정, 상장 후 주가, 철회·연기
- 유상증자, 전환사채(CB)·교환사채(EB) 등 메자닌, 블록딜
- 상장제도·공모주 배정 제도 변화, IPO 주관사 선정과 ECM 주관 실적
- 부적합: 회사채·DCM(→기업금융), 부동산PF(→부동산·인프라), 개인 공모주 투자 권유 기사""",
    },
    "corp_fin": {
        "emoji": "🤝",
        "no": "03",
        "label": "기업금융",
        "desc": "DCM, 회사채, 인수금융, M&A, 기업 자금조달, 경쟁사 주요 거래",
        "keywords": [
            "회사채 수요예측", "회사채 발행", "여전채 발행", "신용등급 하향",
            "크레딧 스프레드", "M&A 인수", "경영권 매각", "사모펀드 인수",
            "인수금융 주선", "기업 자금조달", "DCM 주관",
        ],
        "criteria": """부채자본시장(DCM)과 M&A·인수금융 뉴스.
- DCM: 회사채·여전채·공사채 발행과 수요예측 결과, 크레딧 스프레드, 신용등급 변동, 주관사 실적
- M&A: 경영권 매각, PE 거래, 매각 주관·자문사 선정, 거래 구조
- 인수금융·기업 자금조달(신디케이티드론 등)
- 발행 규모, 금리, 경쟁률, 거래 참여자(매도자/인수자/주관사)가 드러날수록 높은 점수
- 부적합: 국고채 시황만 다룬 기사(→IB그룹 전반), 유상증자·IPO(→IPO·ECM), 부동산PF(→부동산·인프라)""",
    },
    "re_infra": {
        "emoji": "🏗️",
        "no": "04",
        "label": "부동산·인프라",
        "desc": "부동산PF, 리파이낸싱, 데이터센터, 신재생에너지, 발전·교통 인프라, 프로젝트금융",
        "keywords": [
            "부동산 PF", "PF 리파이낸싱", "브릿지론", "PF 정상화",
            "데이터센터 투자", "신재생에너지 금융", "해상풍력 프로젝트",
            "프로젝트파이낸싱", "민자사업 추진", "인프라 펀드", "물류센터 매각",
        ],
        "criteria": """부동산·인프라 금융 뉴스.
- 부동산PF: 신규 조달, 리파이낸싱, 브릿지론, 사업성 평가, PF 연착륙·정상화 정책, 건전성
- 실물: 오피스·물류센터·데이터센터 매매와 개발, 리츠
- 인프라: 발전(신재생·해상풍력·원전), 교통·민자사업, 프로젝트파이낸싱, 인프라 펀드
- 거래 규모, 주선사·투자자, 금리·구조가 드러날수록 높은 점수
- 부적합: 아파트 분양가·청약 등 주택 소비자 기사, 단순 부동산 시세 기사""",
    },
    "sto_new": {
        "emoji": "🔗",
        "no": "05",
        "label": "STO·신사업",
        "desc": "STO 법제화, 발행·유통 플랫폼, 증권사 컨소시엄, 경쟁사 신사업 진출",
        "keywords": [
            "토큰증권 STO", "토큰증권 법제화", "조각투자",
            "증권사 신사업", "가상자산 제도", "스테이블코인 제도",
            "증권사 플랫폼 제휴", "디지털 자산 수탁", "혁신금융서비스 지정",
        ],
        "criteria": """토큰증권(STO)과 증권사 신사업 뉴스.
- STO: 전자증권법·자본시장법 개정 등 법제화, 발행·유통 플랫폼, 조각투자, 혁신금융서비스 지정
- 디지털자산: 가상자산 제도, 스테이블코인, 수탁(커스터디), 증권사 진출
- 증권사 신사업: 컨소시엄·제휴, 신규 라이선스, 플랫폼·AI 등 신규 비즈니스 모델
- 부적합: 코인 시세·투자 권유 기사, 일반 IT 기술 기사""",
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
    if age <= timedelta(hours=72):
        return 60      # 심층 기사는 하루 이틀 늦게 나오기도 해서 완전히 0점 주지 않음
    return 0


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

점수 기준 (0~100 정수):
- importance: IB 실무자가 알아야 할 중요도 (딜 규모, 시장 영향, 정책 파급력)
- depth: 심층성. 분석·해설이고 숫자와 맥락이 풍부하면 높게, 사실 전달뿐이면 낮게
- quality: 내용이 구체적이고 신뢰할 만한지

summary 작성 규칙 (개조식):
- 2~3개 항목. 각 항목 50~70자로 맥락이 드러나게
- 명사형 종결 ("~했다" X → "~흥행", "~확대", "~부담 가중" O)
- 1번째: 무슨 일인지. 숫자·금액·당사자를 구체적으로
- 2번째: 배경이나 맥락. 왜 지금 이런 일이 벌어졌는지
- 3번째: IB 관점의 시사점. 조달 여건·딜 환경·경쟁 구도에 주는 영향
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
        print(f"  [경고] '{meta['label']}' 배치 하나를 건너뜁니다.")
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

        final_score = (
            0.15 * freshness_score(c["pub_dt"], now)
            + 0.35 * to_int(j.get("importance"))
            + 0.35 * depth
            + 0.15 * to_int(j.get("quality"))
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
        c for c in collect_candidates(meta["keywords"], MAX_CANDIDATES)
        if c["url"] not in used_urls
    ]
    print(f"  후보 {len(candidates)}건 → Claude 분류 요청 중...")

    scored = []
    for start in range(0, len(candidates), BATCH_SIZE):
        scored.extend(classify_batch(meta, candidates[start:start + BATCH_SIZE]))
    scored.sort(key=lambda x: x["score"], reverse=True)

    top = scored[:TOP_N_PER_SECTION]
    used_urls.update(item["url"] for item in top)
    print(f"  → {len(top)}건 선정")

    return [
        {
            "top": (i == 0),
            "headline": item["title"],
            "source": item["source"],
            "pubDate": item["pub_dt"].strftime("%Y-%m-%d"),
            "summary": item["summary"],
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
        data[key] = build_section(meta, used_urls)

    output = {
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "sections": [
            {"key": k, "no": m["no"], "emoji": m["emoji"], "label": m["label"], "desc": m["desc"]}
            for k, m in SECTIONS.items()
        ],
        "categories": data,
    }

    out_path = os.path.join(os.path.dirname(__file__), "..", "docs", "data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"✅ 완료: {out_path}")


if __name__ == "__main__":
    main()
