"""Deterministic IB sector gate shared by daily and historical briefings.

Never infer sector from the search query alone. A real-estate title stays in RE
even when a bond/FX keyword also matches a generic corporate-finance search.
Unclear or off-topic news is excluded rather than assigned speculatively.
"""
import re

PRECEDENCE = ("re","infra","sto","hana","ecm","ma","growthfund","productive","corp","peer")
PREFIX = re.compile(r"^(?:\s*\[(?:더벨|Invest|단독|IPO 레이더|기획|분석|이슈|특징|리포트|Company Watch|THE COMPASS|투자노트|금융지주 임원 인사 전망)[^\]]*\]\s*)+",re.I)

def has(pattern, text):
    return bool(re.search(pattern,text,re.I))

def classify_sector(title, description=""):
    title=PREFIX.sub("",title or "").strip()
    desc=description or ""
    if not title:
        return None

    # Exclude generic corporate earnings, promotions, profiles and commentary.
    if has(r"인사 전망|임원 인사|취임|실적 발표|주가 등락|연예|이혼|스캔들|이익 감소|흑자전환|게임 매출|성장률|CEO 인터뷰|치과의사|성공 스토리",title):
        if not has(r"IPO|공모|회사채|채권 발행|기업금융|인수금융|M&A|상장|신주|토큰증권|PF|리츠",title):
            return None

    # 1) Real estate must be the financial asset, not a family-office/back-office term.
    if has(r"(?<!메)리츠|REIT|부동산\s*PF|부동산PF|브릿지론|본PF|PF\s*사업장|(?:프라임\s*)?오피스\s*(?:빌딩|매각|임대|공실|시장|자산|리츠|투자|PF)|물류센터|상업용\s*부동산|부동산(금융|펀드|투자|리파이낸싱)|부동산\s*대출|부동산(시장|매각)|물류창고\s*(매각|PF|투자)|상업시설\s*(매각|PF|투자)",title):
        if not has(r"(?<!메)리츠|REIT|부동산|PF|금융|자금|차환|리파이낸싱|공실|임대|매각|인수|오피스|물류|환헤지|투자",title+" "+desc):
            return None
        return "re"
    # Deal-specific RE asset names rarely stand alone; no generic '건설' cue.

    # 2) Infrastructure finance, not every AI/energy industry headline.
    infra_asset=has(r"데이터센터|AI\s*데이터센터|GPU|인프라|해상풍력|육상풍력|신재생(에너지)?|발전소|송전망|전력망|민자사업|PPP|SOC|태양광",title)
    infra_deal=has(r"PF|프로젝트\s*금융|파이낸싱|자금\s*조달|신디케이트|투자유치|투자금|대출|주선|금융약정|펀드|금융지원|사업비|민간투자|인프라\s*투자|착공",title+" "+desc)
    if infra_asset and infra_deal:
        if not has(r"주가|투자노트|수출\s*증가|글로벌\s*경제|실적\s*발표",title):
            return "infra"

    # 3) STO and capital-markets digital securities; not coin price.
    if has(r"토큰증권|토큰\s*증권|STO|증권\s*토큰|조각투자|토큰화\s*(시장|증권|자산)|RWA|토큰주식",title):
        if not has(r"코인\s*가격|비트코인\s*시세|알트코인\s*급등",title):
            return "sto"

    # 4) Group news must explicitly name an affiliate in the headline.
    hana=has(r"하나(증권|은행|금융(그룹|지주)?|캐피탈|자산신탁|대체투자|금융티아이|생명|카드|벤처스|자산운용)",title)
    finance=has(r"IB|기업금융|금융(주선|지원|전략|투자)|대출|회사채|채권|자금|펀드|투자금융|인수|M&A|IPO|공모|생산적금융|모험자본|RWA|자본|CET1|사업\s*전략|투자|실적",title+" "+desc)
    if hana and finance:
        return "hana"

    # Competitor IB strategy belongs in peer, even when its strategy refers to
    # acquisition finance or productive capital. Actual individual transactions
    # without a strategy/organization angle remain in the business sector.
    if has(r"증권사|투자증권|미래에셋증권|메리츠증권|KB증권|NH투자증권|삼성증권|키움증권|신한투자증권|한국투자증권|종투사|발행어음|\bIMA\b",title):
        if has(r"IB|기업금융|인수금융|모험자본|생산적금융|발행어음|IMA|종투사|투자금융",title):
            if has(r"조직|전략|본부|시장|사업|확충|경쟁|진출|도입|라이선스|실적|성과|자기자본|개편|확대",title):
                return "peer"

    # 5) Equity financing and listing regulation.
    if has(r"\bIPO\b|공모(주|가|청약|시장|규모)?|상장(예비심사|심사|추진|철회|폐지|규정|기업|주관|예정)|유상증자|메자닌|블록딜|전환사채|신주인수권부사채|코너스톤|프리\s*IPO|주식\s*발행",title):
        if not has(r"상장사\s*실적|상장사\s*영업이익|상장\s*이후\s*매출",title) or has(r"공모|IPO|상장규정|상장폐지|상장심사",title):
            return "ecm"

    # 6) M&A must describe a real purchase, sale, takeover, PEF deal,
    #    or acquisition-financing transaction (not just business performance).
    if has(r"인수금융|인수합병|M&A|바이아웃|경영권\s*(매각|인수|분쟁)|기업\s*(인수|매각)|매각\s*(추진|본입찰|예비입찰|주관|가격|협상)|공개\s*매각|매각\s*입찰|공개매수|매각자문|인수자문|합병\s*(추진|결정|계약)|PEF\s*(인수|매각|회수)|사모펀드\s*(매각|인수|회수)",title):
        return "ma"
    if has(r"PEF|사모펀드",title) and has(r"경영권|인수금융|바이아웃|매각|인수|회수|인수합병",title+" "+desc):
        return "ma"

    # 7) National Growth Fund: specifically about the Fund's funding,
    # selection, investment, allocation or announced governance.
    # Other policy/venture finance stays in productive finance.
    national = has(r"국민\\s*성장\\s*펀드|국민성장펀드|국민성장기금", title)
    national_context = has(r"국민\\s*성장\\s*펀드|국민성장펀드|국민성장기금", desc)
    decision = has(r"출자|투자|배분|공급|집행|선정|조성|금융지원|계획|운용|사업", title + " " + desc)
    if (national or (national_context and decision
                      and has(r"정책금융|성장펀드|지방.*금융|지역.*금융", title))) and decision:
        return "growthfund"

    # 7) Productive finance must be actual investment / policy finance.
    if has(r"생산적\s*금융|국민\s*성장\s*펀드|모험\s*자본|벤처(투자|펀드|캐피탈)|정책\s*금융|성장\s*펀드|BDC|기업\s*성장\s*집합\s*투자|창업\s*투자",title):
        if has(r"투자|자금|금융|지원|펀드|출자|회수|대출|육성|조달|정책",title+" "+desc):
            return "productive"

    # 8) Corporate financing needs a capital raise, credit or refinancing
    #    transaction explicitly in its headline.
    if has(r"회사채|기업어음|\bCP\b|전자단기사채|여전채|신종자본증권|후순위채|금융채|\bDCM\b|기업(금융|대출|자금조달)|신디케이트론|PRS|크레딧|채권(발행|차환|시장|만기|금리|디폴트|엔|의|에|\s*디폴트|\s*상환)|차환\s*(발행|조달|수요)|사모채|자금\s*조달|금융\s*약정|유동화\s*증권",title):
        return "corp"

    # 9) Competitor activity requires a securities house / IB-specific
    #    product or an investment-banking strategy, not general bank news.
    peer=has(r"증권사|투자증권|미래에셋증권|메리츠증권|KB증권|NH투자증권|삼성증권|키움증권|신한투자증권|한국투자증권|종투사|발행어음|\bIMA\b",title)
    ib=has(r"\bIB\b|기업금융|인수금융|모험자본|생산적금융|발행어음|\bIMA\b|인수\s*주선|자기자본\s*투자|투자금융|증권사\s*전략|주선\s*실적|종투사|자본\s*규제|자본\s*확충",title+" "+desc)
    if peer and ib:
        return "peer"
    return None

def sector_matches(section, title, description=""):
    return classify_sector(title,description)==section
