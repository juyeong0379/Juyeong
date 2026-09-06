"""
데일리 산업/경제 뉴스 브리핑 자동 생성 스크립트
=================================================

동작 순서
  1) 카테고리별로 "넓은" 키워드로 네이버 뉴스 검색 API를 호출해 후보 기사를 모은다.
     (키워드는 정교할 필요 없음 — 놓치지만 않으면 됨. 정확도는 2단계 Claude가 책임진다.)
  2) 후보 기사들을 Claude API에 묶어서 보내, 진짜 그 카테고리에 맞는 산업/경제 뉴스인지
     판단하고, 요약·세부태그·중요성/퀄리티 점수를 받는다.
  3) 시의성(24시간 이내=100 / 아니면 0, 20%) + 중요성(50%) + 퀄리티(30%)로
     최종 점수를 계산해 카테고리별 상위 N개만 남긴다.
  4) 프론트엔드(docs/index.html)가 읽을 docs/data.json 을 생성한다.

필요한 환경변수
  - NAVER_CLIENT_ID       (네이버 개발자센터에서 발급)
  - NAVER_CLIENT_SECRET
  - ANTHROPIC_API_KEY     (console.anthropic.com 에서 발급)

로컬에서 테스트하려면:
  export NAVER_CLIENT_ID=...
  export NAVER_CLIENT_SECRET=...
  export ANTHROPIC_API_KEY=...
  python scripts/generate_briefing.py
"""

import os
import re
import json
import html
import urllib.parse
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

import requests
import anthropic

# ── 설정값 (필요하면 이 부분만 고쳐도 돼요) ──────────────────────
KST = timezone(timedelta(hours=9))
TOP_N_PER_CATEGORY = 4          # 카테고리별로 최종 몇 개까지 보여줄지
CANDIDATES_PER_KEYWORD = 8      # 키워드 하나당 몇 개씩 후보로 가져올지
MODEL = "claude-haiku-4-5-20251001"   # 속도/비용 우선. 판단 품질을 더 원하면 "claude-sonnet-5" 로 교체

CATEGORIES = {
    "global": {
        "label": "글로벌",
        "keywords": ["연준", "관세 부과", "OPEC", "미중 갈등", "중동 정세"],
    },
    "macro": {
        "label": "국내 거시경제",
        "keywords": ["기준금리", "소비자물가", "고용동향", "가계부채", "수출입 동향", "추경"],
    },
    "industry": {
        "label": "국내 산업",
        "keywords": [
            "반도체", "인공지능 산업", "산업용로봇", "2차전지",
            "전기차", "조선 수주", "철강", "석유화학",
            "바이오 신약", "건설 수주", "이커머스", "K콘텐츠",
        ],
    },
    "finance": {
        "label": "국내 시장·금융",
        "keywords": ["코스피", "원달러 환율", "회사채", "은행 대출", "금융위원회", "원자재 가격"],
    },
}


# ── 1. 네이버 뉴스 수집 ──────────────────────────────────────────
def strip_html(text: str) -> str:
    """네이버 API 응답에 섞여 오는 <b> 태그, &quot; 같은 걸 제거"""
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def fetch_naver_news(query: str, display: int = CANDIDATES_PER_KEYWORD) -> list:
    url = "https://openapi.naver.com/v1/search/news.json"
    headers = {
        "X-Naver-Client-Id": os.environ["NAVER_CLIENT_ID"],
        "X-Naver-Client-Secret": os.environ["NAVER_CLIENT_SECRET"],
    }
    params = {"query": query, "display": display, "sort": "date"}
    res = requests.get(url, headers=headers, params=params, timeout=10)
    res.raise_for_status()

    out = []
    for it in res.json().get("items", []):
        try:
            pub_dt = parsedate_to_datetime(it["pubDate"]).astimezone(KST)
        except Exception:
            continue
        out.append({
            "title": strip_html(it["title"]),
            "description": strip_html(it["description"]),
            "url": it.get("originallink") or it.get("link"),
            "pub_dt": pub_dt,
            "matched_keyword": query,
        })
    return out


def collect_candidates(keywords: list) -> list:
    seen_urls = set()
    candidates = []
    for kw in keywords:
        for item in fetch_naver_news(kw):
            if item["url"] in seen_urls:
                continue
            seen_urls.add(item["url"])
            candidates.append(item)
    return candidates


# ── 2. Claude로 관련성 판단 + 요약 + 점수 매기기 ─────────────────
client = anthropic.Anthropic()  # ANTHROPIC_API_KEY 환경변수를 자동으로 읽어요


def classify_and_score(category_label: str, candidates: list) -> list:
    if not candidates:
        return []

    numbered = "\n".join(
        f"{i}. [{c['pub_dt'].strftime('%Y-%m-%d %H:%M')}] {c['title']} — {c['description']}"
        for i, c in enumerate(candidates)
    )

    prompt = f"""아래는 '{category_label}' 카테고리 후보 기사 목록이야. 각 기사에 대해 판단해줘.

기준:
- 산업/경제의 실질적 동향(실적, 수급, 정책 영향, 기술, 시장 반응)을 다루면 적합
- 단순 행사/시상식/인사 소식, 정치적 수사에만 그치면 부적합

각 기사(번호 순서 그대로)에 대해 아래 JSON 배열 형식으로만 답해. 다른 설명은 붙이지 마:
[
  {{
    "index": 0,
    "is_relevant": true,
    "summary": "2~3문장 한국어 요약 (원문을 베끼지 말고 내용을 재구성해서)",
    "tag": "세부 토픽 (예: 수출, 금리, 실적, 규제)",
    "importance": 0~100 사이 정수 (단일 기업 이슈면 낮게, 산업 전체나 거시경제급 파급력이면 높게),
    "quality": 0~100 사이 정수 (단순 사실 나열이면 낮게, 배경·수치·맥락이 풍부하면 높게)
  }}
]

부적합한 기사는 "is_relevant": false 만 넣고 나머지 필드는 생략해도 돼.

후보 기사 목록:
{numbered}
"""

    resp = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()
    raw = re.sub(r"^```json|```$", "", raw, flags=re.MULTILINE).strip()

    try:
        judgments = json.loads(raw)
    except json.JSONDecodeError:
        print(f"[경고] '{category_label}' JSON 파싱 실패, 이번엔 스킵합니다.\n{raw[:300]}")
        return []

    now = datetime.now(KST)
    results = []
    for j in judgments:
        if not j.get("is_relevant"):
            continue
        idx = j.get("index")
        if idx is None or idx >= len(candidates):
            continue
        c = candidates[idx]

        freshness = 100 if (now - c["pub_dt"]) <= timedelta(hours=24) else 0
        importance = j.get("importance", 0)
        quality = j.get("quality", 0)
        final_score = 0.2 * freshness + 0.5 * importance + 0.3 * quality

        results.append({
            **c,
            "summary": j.get("summary", ""),
            "tag": j.get("tag", ""),
            "score": final_score,
        })
    return results


# ── 3. 관련기사 묶기 ─────────────────────────────────────────────
# (간단 버전: 같은 검색 키워드로 걸린 다른 기사 중 점수 높은 순으로 2개.
#  더 정교하게 하려면 제목 임베딩 유사도 등으로 업그레이드해도 좋아요.)
def attach_related(selected: list, all_scored: list) -> None:
    for item in selected:
        pool = [
            c for c in all_scored
            if c["url"] != item["url"] and c["matched_keyword"] == item["matched_keyword"]
        ]
        pool.sort(key=lambda x: x["score"], reverse=True)
        item["related"] = [
            {
                "title": r["title"],
                "src": urllib.parse.urlparse(r["url"]).netloc,
                "url": r["url"],
            }
            for r in pool[:2]
        ]


# ── 4. 카테고리별 처리 ───────────────────────────────────────────
def build_category(meta: dict) -> list:
    print(f"▶ {meta['label']} 수집 중...")
    candidates = collect_candidates(meta["keywords"])
    print(f"  후보 {len(candidates)}건 수집, Claude에게 분류 요청 중...")

    scored = classify_and_score(meta["label"], candidates)
    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:TOP_N_PER_CATEGORY]

    attach_related(top, scored)

    return [
        {
            "top": (i == 0),
            "headline": item["title"],
            "pubDate": item["pub_dt"].strftime("%Y-%m-%d"),
            "summary": item["summary"],
            "tag": item["tag"] or meta["label"],
            "url": item["url"],
            "related": item["related"],
        }
        for i, item in enumerate(top)
    ]


def main():
    data = {key: build_category(meta) for key, meta in CATEGORIES.items()}
    output = {
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "categories": data,
    }

    out_path = os.path.join(os.path.dirname(__file__), "..", "docs", "data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"✅ 완료: {out_path}")


if __name__ == "__main__":
    main()
