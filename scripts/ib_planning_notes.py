"""IB planning notes only where the story provides a clear, concrete trigger.

These are review prompts rather than assertions about Hana Securities' deal
history or unverified internal capabilities. No speculative numeric forecasts.
"""
import re

RULES = {
 "ecm": [
  (r"상장예비심사|상장\s*심사|IPO|공모가|공모|구주매출", "IPO 심사·공모시장 변화에 따른 발행사 자문 수요와 주관 역량 점검"),
  (r"유상증자|전환사채|블록딜|메자닌", "ECM 조달구조별 발행 수요와 투자자 보호 이슈를 연계한 제안 방안 검토"),
 ],
 "corp":[
  (r"단기물|만기|금리|회사채|차환|신용", "발행사별 회사채 만기·신용등급별 차환 수요와 DCM 주관 전략 점검"),
  (r"신디케이트|자금조달|기업대출", "기업 조달수요 변화에 따른 그룹 공동 기업금융 주선 가능성 검토"),
 ],
 "ma":[
  (r"의무공개매수|공개매수|주주 보호|M&A|인수|매각|합병", "경영권 거래구조·규제 변화에 따른 M&A 자문 및 인수금융 기회·위험 점검"),
 ],
 "re":[
  (r"리츠|환헤지|차환|부동산PF|브릿지론|부동산|물류센터", "부동산 자산별 유동성·차환·환헤지 위험을 반영한 신규 익스포저 관리 기준 점검"),
 ],
 "infra":[
  (r"데이터센터|GPU|전력|해상풍력|발전|인프라|PF", "인프라 사업의 장기 현금흐름·담보·전력계약 위험과 그룹 공동 금융주선 가능성 검토"),
 ],
 "hana":[
  (r"하나은행|하나증권|하나금융|하나캐피탈", "계열사별 자금공급·주선 역할을 비교해 ONE-IB 공동사업 및 그룹 시너지 확대 방안 점검"),
 ],
 "peer":[
  (r"증권사|투자증권|IB|발행어음|IMA|종투사", "경쟁사 IB 사업구조·자본 활용 방식 비교를 통한 차별화 분야 점검"),
 ],
 "sto":[
  (r"토큰|STO|조각투자|디지털증권", "토큰증권 발행·유통 규제 변화에 따른 신규 사업모델과 수익성·내부통제 요건 검토"),
 ],
 "productive":[
  (r"생산적금융|성장펀드|벤처|모험자본|정책금융|혁신기업", "정책성 자본공급과 IB 투자·주선 연계 구조 및 회수 경로 검토"),
 ],
}

def planning_insight(section, title, summary):
    article=" ".join([title]+list(summary or []))
    for pattern,note in RULES.get(section, []):
        if re.search(pattern,article,re.I):
            return note
    return ""
