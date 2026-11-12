"""IB news materiality gate.

Select developments that change the IB operating environment, not routine
transaction announcements. Evidence must be explicitly grounded in the
headline/search description passed to the model.
"""

import re

MATERIALITY_THRESHOLD = 78
IMPORTANCE_THRESHOLD = 75
DEPTH_THRESHOLD = 70
QUALITY_THRESHOLD = 70

IMPACT_TYPES = {
    "policy_regulation",
    "capital_market_structure",
    "systemic_risk",
    "financing_conditions",
    "transaction_precedent",
    "industry_investment_shift",
    "competitive_strategy",
}
EMPTY_REASONS = re.compile(
    r"^(?:시장 확대|금융 수요 증가|투자 확대|사업 확대|기회 검토|"
    r"비즈니스에 영향|업계 주목|중요한 거래|관심 확대)[.! ]*$"
)
ROUTINE_HEADLINE = re.compile(
    r"인사 발령|임원 인사|업무협약|협약식|개최했다|IR 행사|"
    r"단순 실적|분기 실적 발표|신규 지점|기념식|"
    r"주가 (상승|하락)|실적 개선 기대",
    re.I,
)


def normalize_text(value):
    """Whitespace and formatting normalization only; no fuzzy fabrication."""
    return re.sub(r"\s+", "", str(value or "")).replace("**", "").casefold()


def is_material_news(judgment, title, description):
    """Require concrete financial-system significance with verifiable evidence.

    For a small transaction, select only if it creates a precedent in financial
    structure, risk distribution, capital-market access or regulation. Magnitude
    alone does not qualify, while a market-wide issue may be important without
    naming a single large transaction.
    """
    def number(name):
        try:
            return int(judgment.get(name, 0))
        except (TypeError, ValueError):
            return 0

    if (number("materiality") < MATERIALITY_THRESHOLD
            or number("importance") < IMPORTANCE_THRESHOLD
            or number("depth") < DEPTH_THRESHOLD
            or number("quality") < QUALITY_THRESHOLD):
        return False

    kind = str(judgment.get("impact_type", "")).strip()
    if kind not in IMPACT_TYPES:
        return False

    reason = str(judgment.get("impact_reason", "")).strip()
    if len(reason) < 22 or len(reason) > 180 or EMPTY_REASONS.fullmatch(reason):
        return False

    evidence = str(judgment.get("impact_evidence", "")).strip().strip('"“”')
    if not 7 <= len(evidence) <= 110:
        return False
    reference = normalize_text(title + " " + description)
    if normalize_text(evidence) not in reference:
        return False

    if ROUTINE_HEADLINE.search(title):
        # A routine results/HR story is not promoted solely by model enthusiasm.
        return False

    # Transaction-specific exception: a precedent must explain a concrete
    # financing / underwriting / risk-distribution effect, not deal execution.
    if kind == "transaction_precedent":
        if number("materiality") < 84:
            return False
        if not re.search(
            r"자금조달|조달구조|금융구조|재구조화|차환|인수금융|"
            r"유동성|위험분담|리스크|주관|투자자|심사|상장|"
            r"PF|공모|규제|회수|제도|선례",
            reason,
            re.I,
        ):
            return False

    return True
