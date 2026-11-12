"""Decide when an IB strategic-planning implication is genuinely warranted.

Ordinary IPO, company-bond, investment and peer news has NO default insight.
An insight needs a concrete new policy, systemic market disruption or
strategically consequential change, plus a testable IB planning action.
"""
import re

# A significant *change* matters; merely mentioning a regulated product does not.
POLICY_CHANGE = re.compile(
    r"(법안|법률|시행령|감독규정|금융위|금감원|공정위|당국|국회|규제|제도|정책|"
    r"가이드라인|의무공개매수|중복상장|토큰증권|코너스톤).{0,85}"
    r"(개정|도입|시행|확정|의결|발의|완화|강화|폐지|변경|개편|규제|제재|조사)|"
    r"(개정|도입|시행|확정|의결|발의|완화|강화|폐지|변경|개편).{0,85}"
    r"(법안|법률|시행령|감독규정|금융위|금감원|공정위|규제|제도|정책)",
    re.I,
)
MAJOR_RISK = re.compile(
    r"채무불이행|기한이익상실|디폴트|파산|법정관리|워크아웃|대규모\s*손실|"
    r"유동성\s*(위기|고갈|경색)|미매각\s*(급증|사태)|증권사\s*부실|"
    r"부동산PF\s*위기|대형\s*인수합병|적대적\s*M&A|신용등급\s*(강등|하향)|"
    r"발행\s*중단|금융\s*사고|대규모\s*해킹",
    re.I,
)
STRUCTURAL_SHIFT = re.compile(
    r"(시장|업계|증권사|금융권|자금조달|회사채|공모주|인수금융|PF|M&A|"
    r"자산운용|IB|발행어음|모험자본).{0,80}"
    r"(구조\s*재편|대전환|규칙\s*변경|급격한\s*위축|급감|급증|"
    r"기존\s*관행\s*변경|전면\s*개편|시장\s*경색)|"
    r"(구조\s*재편|대전환|급감|급증|시장\s*경색).{0,80}"
    r"(시장|업계|증권사|자금조달|회사채|공모주|인수금융|PF|IB)",
    re.I,
)
STRATEGIC_MOVE = re.compile(
    r"(증권사|금융그룹|금융지주|하나금융|하나은행|하나증권|경쟁사).{0,70}"
    r"(인수\s*확정|사업\s*철수|신규\s*인가|라이선스\s*취득|조직\s*전면\s*개편|"
    r"자본\s*재배분|대규모\s*투자\s*확정|자체투자\s*한도\s*변경)|"
    r"(인수\s*확정|사업\s*철수|신규\s*인가|라이선스\s*취득).{0,70}(증권사|금융그룹)",
    re.I,
)
MATERIAL_DIGIT = re.compile(r"\d[\d,.]*(?:조|억|만|%|bp|퍼센트|원|달러)")
ACTION = re.compile(
    r"검토|점검|평가|비교|분석|재설계|재검토|조정|모니터링|관리|기획|"
    r"주선|자문|전략|한도|익스포저|수익성|리스크|협업",
    re.I,
)
EMPTY_CLICHE = re.compile(
    r"^(?:시장\s*확대|수요\s*증가|대응\s*필요|지속\s*관심|변화\s*주시|"
    r"향후\s*추이\s*모니터링|관련\s*기회\s*검토)[. ]*$",
    re.I,
)

def significant_change(title, summary):
    text=" ".join([str(title or "")]+[str(x) for x in (summary or [])])
    if POLICY_CHANGE.search(text) or MAJOR_RISK.search(text) or STRATEGIC_MOVE.search(text):
        return True
    # An otherwise ordinary event is elevated only for a substantive market
    # shift backed by an actual quantity, rather than a broad trend claim.
    return bool(STRUCTURAL_SHIFT.search(text) and MATERIAL_DIGIT.search(text))

def accept_insight(title, summary, suggestion, importance=0, depth=0):
    note=str(suggestion or "").strip()
    if not note or len(note)<24 or len(note)>110:
        return ""
    if not significant_change(title, summary):
        return ""
    if int(importance or 0)<78 or int(depth or 0)<62:
        return ""
    if not ACTION.search(note) or EMPTY_CLICHE.fullmatch(note):
        return ""
    if re.search(r"최고|확실한\s*기회|무조건|반드시\s*수익|지금\s*당장",note):
        return ""
    return note
