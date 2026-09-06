# 데일리 산업/경제 뉴스 브리핑

매일 아침, 네이버 뉴스에서 4개 카테고리(🌍글로벌 / 📈국내거시경제 / 🏭국내산업 / 💰국내시장금융)의
최신 기사를 넓게 모은 뒤, Claude가 "진짜 그 주제에 맞는 기사인지" 판단하고
시의성·중요성·퀄리티 점수로 걸러서 웹페이지로 보여주는 개인용 뉴스 대시보드예요.

- **완전 자동**: GitHub Actions가 매일 정해진 시간에 스스로 실행해요.
- **무료 호스팅**: GitHub Pages로 배포해요.
- **비용**: 네이버 API는 무료(하루 25,000건 한도), Claude API(Haiku 4.5 기준)는
  하루 한 번 실행 시 몇 센트 수준이에요. (2026년 9월 기준 $1/$5 per 1M 토큰 — 최신 가격은
  https://docs.claude.com/en/docs/about-claude/pricing 에서 확인하세요.)

---

## 폴더 구조

```
news-briefing/
├── .github/workflows/daily-briefing.yml   # 매일 자동 실행 설정
├── scripts/generate_briefing.py            # 뉴스 수집 + Claude 분석 스크립트
├── docs/
│   ├── index.html                          # 실제로 보게 될 웹페이지
│   └── data.json                           # 스크립트가 매일 새로 써주는 결과 (지금은 샘플 데이터)
├── requirements.txt
└── README.md
```

---

## 처음 설정하는 방법 (한 번만 하면 돼요)

### 1) 이 폴더를 GitHub 저장소로 만들기

1. [github.com](https://github.com) 에서 새 저장소(Repository)를 하나 만드세요. (Public이어도 되고 Private이어도 돼요)
2. 이 폴더 전체를 그 저장소에 업로드하세요. 터미널을 쓴다면:
   ```bash
   cd news-briefing
   git init
   git add .
   git commit -m "init"
   git branch -M main
   git remote add origin https://github.com/내아이디/내저장소이름.git
   git push -u origin main
   ```
   터미널이 낯설면, GitHub 웹사이트의 "Add file → Upload files" 버튼으로 폴더째 드래그해서 올려도 돼요.

### 2) 네이버 뉴스 검색 API 키 발급받기

1. [네이버 개발자센터](https://developers.naver.com/apps/#/register) 접속 → 로그인
2. "애플리케이션 등록" 클릭
3. 사용 API에서 **검색** 선택
4. 등록 후 발급되는 **Client ID / Client Secret**을 복사해두세요

### 3) Anthropic(Claude) API 키 발급받기

1. [console.anthropic.com](https://console.anthropic.com) 접속 → 가입/로그인
2. API Keys 메뉴 → Create Key
3. 발급된 키를 복사해두세요 (다시 볼 수 없으니 안전한 곳에 저장!)
4. 결제 수단 등록이 필요해요 (사용한 만큼만 과금돼요)

### 4) GitHub 저장소에 키 등록하기

저장소 페이지에서 **Settings → Secrets and variables → Actions → New repository secret**
으로 아래 3개를 각각 등록하세요.

| Name | Value |
|---|---|
| `NAVER_CLIENT_ID` | 2번에서 받은 Client ID |
| `NAVER_CLIENT_SECRET` | 2번에서 받은 Client Secret |
| `ANTHROPIC_API_KEY` | 3번에서 받은 API 키 |

### 5) GitHub Pages 켜기

저장소 **Settings → Pages** 에서:
- Source: **Deploy from a branch**
- Branch: **main**, 폴더: **/docs**
- Save

몇 분 뒤 `https://내아이디.github.io/내저장소이름/` 주소로 접속하면 대시보드가 보여요.
(처음엔 샘플 데이터가 보일 거예요 — 아직 자동 실행 전이니까요!)

### 6) 첫 실행해보기

저장소 **Actions** 탭 → **Daily News Briefing** 워크플로우 선택 →
**Run workflow** 버튼을 눌러 수동으로 한 번 실행해보세요.
몇 분 뒤 완료되면 `docs/data.json`이 실제 데이터로 업데이트되고,
웹페이지를 새로고침하면 진짜 브리핑이 보일 거예요.

이후로는 **매일 한국시간 오전 7시**에 자동으로 실행돼요. (`.github/workflows/daily-briefing.yml`의
`cron` 값을 고치면 시간을 바꿀 수 있어요 — UTC 기준이라 KST는 UTC+9예요.)

---

## 로컬 컴퓨터에서 미리 테스트하기

```bash
pip install -r requirements.txt

export NAVER_CLIENT_ID=발급받은값
export NAVER_CLIENT_SECRET=발급받은값
export ANTHROPIC_API_KEY=발급받은값

python scripts/generate_briefing.py
```

실행되면 `docs/data.json`이 새로 생겨요. 그 다음 웹페이지를 로컬에서 확인하려면:

```bash
cd docs
python -m http.server 8000
```

브라우저에서 `http://localhost:8000` 접속. (index.html을 더블클릭해서 직접 열면
브라우저 보안 정책(CORS) 때문에 data.json을 못 읽어와요 — 꼭 위처럼 서버로 열어주세요.)

---

## 커스터마이징 포인트

- **카테고리/키워드 바꾸기**: `scripts/generate_briefing.py` 맨 위 `CATEGORIES` 딕셔너리 수정
- **카테고리별 개수 조절**: `TOP_N_PER_CATEGORY` 값 수정
- **발행 시간 바꾸기**: `.github/workflows/daily-briefing.yml`의 `cron` 값 수정
- **점수 가중치 바꾸기**: `classify_and_score()` 함수의 `final_score` 계산식 수정
- **디자인/색상 바꾸기**: `docs/index.html`의 `THEME` 객체와 `<style>` 부분 수정
- **더 똑똑한 판단이 필요하면**: `MODEL`을 `"claude-haiku-4-5-20251001"`에서 `"claude-sonnet-5"`로 교체
  (비용은 조금 더 들지만 판단 품질이 좋아져요)

---

## 다음에 더 발전시키면 좋을 것들

- **관련기사 묶기 고도화**: 지금은 "같은 검색 키워드로 걸린 기사" 중 점수 높은 걸 관련기사로 붙이는
  간단한 방식이에요. 제목 임베딩 유사도 등을 쓰면 "진짜 같은 사건"만 더 정확히 묶을 수 있어요.
- **중복 카테고리 처리**: 반도체 관세처럼 여러 카테고리에 걸치는 이슈를 자동으로 "크로스 이슈"로
  표시하는 로직을 추가하면 처음 기획했던 크로스 산업 연결형에 더 가까워져요.
- **알림 추가**: data.json이 갱신될 때 이메일/슬랙 알림을 보내는 스텝을 워크플로우에 추가할 수 있어요.
