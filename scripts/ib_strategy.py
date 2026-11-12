"""Conservative IB strategy review points based only on article topic.

These are proposals for verification, not claims about the company's
actual positions, track record, or exposures.
"""
import re

def strategy_insight(section, headline, summary):
    text=(headline+" "+" ".join(str(s) for s in summary)).lower()
    def hit(*terms):
        return any(x.lower() in text for x in terms)
    if section=="ecm":
        if hit("상장폐지","상장심사","중복상장","코너스톤","의무보유"):
            return "상장 제도 변화에 따른 발행사 자문 수요와 IPO 심사 대응체계 점검"
        if hit("구주매출","회수","프리ipo","pre-ipo"):
            return "VC·PEF 회수 수요를 고려한 프리IPO·구주매출 관련 자문 기회 검토"
        return "공모시장 수요예측 및 주관 경쟁구도 분석을 통한 ECM 영업 우선순위 점검"
    if section=="corp":
        if hit("단기","만기","차환","크레딧","신용","회사채"):
            return "신용등급·만기별 발행 여건 분석 및 기업 차환 수요 대응 전략 검토"
        return "기업 자금조달 구조 변화에 맞춘 DCM·여신·주선 협업 가능성 검토"
    if section=="ma":
        if hit("의무공개매수","경영권","공개매수","규제","심사"):
            return "M&A 규제 변화가 거래 구조·인수금융 조건에 미치는 영향 점검"
        return "거래 규모와 차입구조를 고려한 인수금융 주선·자문 기회 및 위험 검토"
    if section=="re":
        if hit("환헤지","환율","리츠"):
            return "리츠 유동성·환헤지 부담 점검과 부동산 금융 익스포저 관리 강화 검토"
        return "PF 사업장 현금흐름·담보가치 점검 및 차환·재구조화 금융 수요 분석"
    if section=="infra":
        if hit("데이터센터","gpu","ai"):
            return "데이터센터 전력·임차계약 위험 점검 및 그룹 공동 자금조달 주선 가능성 검토"
        return "사업별 장기 현금흐름·계약구조를 반영한 인프라 금융 참여 기준 검토"
    if section=="hana":
        if hit("하나은행","하나증권","하나금융"):
            return "그룹 계열사별 기업금융 역할을 비교하고 ONE-IB 공동 사업 기회 점검"
        return "그룹 투자·주선 역량을 연결한 협업 사업의 실행 가능성과 수익성 검토"
    if section=="peer":
        return "경쟁사 IB 조직·상품·자본 운용 전략 비교를 통한 차별화 과제 검토"
    if section=="sto":
        return "토큰증권 발행·유통 인프라 및 투자자 보호 요건에 따른 사업화 단계 검토"
    if section=="productive":
        return "정책자금과 민간자본의 역할 분담에 따른 모험자본 공급·회수 전략 검토"
    return ""
