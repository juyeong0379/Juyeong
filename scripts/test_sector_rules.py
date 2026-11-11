"""Regression tests: finance-news stories must not bleed into unrelated sectors."""
from sector_rules import classify_sector

CASES = [
    ("부메랑이 된 환율 방패… 해외 리츠 덮친 환헤지 청구서", "해외 부동산 리츠 환헤지", "re"),
    ("환위험 막으려다 현금 부담…제이알·미래에셋리츠 환헤지 덫", "", "re"),
    ("데이터센터 PF 3조원 신디케이트론 금융 약정", "", "infra"),
    ("AI붐 꺼지면 세계경제 휘청…수출 증가분 편중", "AI 수출시장", None),
    ("코스닥 IPO 공모가 산정과 상장예비심사 제도 개편", "", "ecm"),
    ("법원, 코스닥 상장폐지 규정 무효 판결", "", "ecm"),
    ("은행권 회사채 발행 1조원 차환 수요 확대", "", "corp"),
    ("기업어음 CP 시장 위축에 기업 자금 조달 난항", "", "corp"),
    ("기업 경영권 매각 본입찰에 3곳 참여", "", "ma"),
    ("사모펀드 PEF의 바이아웃 인수금융 차환", "", "ma"),
    ("하나증권 IB 인수금융 2조원 주선", "", "hana"),
    ("하나은행 생산적금융 중소기업 대출 확대", "", "hana"),
    ("프렌드투자, AX 과학기술혁신펀드 결성", "", None),
    ("새로운 벤처펀드 1000억원 출자", "", "productive"),
    ("피지컬AI 눈·근육·동력 키운다…산업 과제 지정", "", None),
    ("토큰증권 발행 첫 상품 윤곽", "", "sto"),
    ("메리츠화재 대표 주가 등락은 장기 수익 위한 입장료", "", None),
    ("[금융지주 임원 인사 전망] 우리금융캐피탈 대표 인사", "", None),
    ("위메이드, 게임 매출은 15% 감소하며 흑자전환", "", None),
    ("한국투자증권 IB 인수금융 조직 확충", "", "peer"),
    ("현대차 글로벌 톱3…내부 시스템 혁신 과제", "", None),
    ("증권사 발행어음 IMA 사업 확대, 모험자본 공급 경쟁", "", "peer"),
    ("[Invest]자회사 정리 꼬인 GS건설…본사 채권 디폴트 우려", "", "corp"),
]
for title, description, expected in CASES:
    actual = classify_sector(title, description)
    assert actual == expected, f"{title}\nExpected {expected}, got {actual}"
print(f"PASS: {len(CASES)} sector boundary and publisher-independent tests.")
