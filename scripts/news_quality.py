"""Newsroom quality gates: remove near-duplicate news and reject unrelated digest.

No guessing based on a match in generic terms such as 'IB', '투자' or 'IPO'.
"""
import re
from collections import Counter

ENTITIES=("패스트파이브","크리오스","코어위브","롯데렌탈","삼양홀딩스",
          "다우기술","미래에셋증권","GS","하나금융","삼성증권","메리츠증권",
          "삼성바이오로직스","신촌","순천시","GS칼텍스","토큰증권")
IGNORE={"더벨","데일리","인터뷰","단독","특징","기획","분석","오늘","투자","기업",
        "금융","업계","공개","상장","시장","관련","최대","한국","사업","이번",
        "증권","회사채","기사","속보","종합","올해","본격","추진","규모",
        "사실","시사점","거래소","주식","주관사","공모","자본","추가"}
BAD_BODY=re.compile(r"삼성바이오로직스.{0,80}생산직|세금.{0,60}연말정산|"
                    r"원본보기 아이콘|로그인.*회원가입|전체기사.*많이 본 뉴스",re.I)

def cleanup_title(title):
    title=re.sub(r"^(?:\[[^\]]{2,70}\]\s*)+","",title or "")
    return re.sub(r"[“”\"'…·/:,()]+"," ",title).strip()

def words(text):
    return set(t.lower() for t in re.findall(r"[가-힣A-Za-z0-9]{2,}",cleanup_title(text))
               if t not in IGNORE and len(t)>2)

def entity_tokens(title):
    return {t for t in ENTITIES if t.casefold() in (title or "").casefold()}

NUMERIC_CLAIM = re.compile(
    r"(?<![A-Za-z0-9])\d[\d,.]*(?:\s*)(?:조원|억원|억달러|만달러|"
    r"조|억|만|%|bp|GW|MW|년|월|일|개월|명|건|곳|개|배|주)",
    re.I,
)

def numeric_claims_grounded(summary, source):
    """Reject numbers absent from the article title/search description.

    The quality gate is intentionally conservative: a valid fact that is not
    independently present in the supplied source metadata should not be
    published as though the model had read the full article.
    """
    def normalize(x):
        return re.sub(r"[\s,]", "", str(x)).lower()
    reference=normalize(source)
    numbers={normalize(x) for line in summary
             for x in NUMERIC_CLAIM.findall(str(line))}
    return all(token in reference for token in numbers)


def event_signature(title):
    """Cluster recurring coverage of the same specific policy or transaction."""
    t=cleanup_title(title)
    if re.search(r"금감원|금융감독원|금융당국",t) and "발행어음" in t:
        return "fss-commercial-paper-supervision"
    if "코너스톤" in t and re.search(r"사전수요예측|도입|시행",t):
        return "ipo-cornerstone-start"
    if re.search(r"의무공개매수|동등비례",t) and re.search(r"비례|50%|법안",t):
        return "mandatory-tender-offer-policy"
    return ""

def duplicate(a,b):
    ta=cleanup_title(a.get("headline",a.get("title","")))
    tb=cleanup_title(b.get("headline",b.get("title","")))
    if not ta or not tb:return False
    sig_a,sig_b=event_signature(ta),event_signature(tb)
    if sig_a and sig_a==sig_b:
        return True
    ea,eb=entity_tokens(ta),entity_tokens(tb)
    wa,wb=words(ta),words(tb)
    overlap=len(wa&wb)/max(1,min(len(wa),len(wb)))
    if ea & eb and ("IPO" in ta or "IPO" in tb or "상장" in ta or "상장" in tb):
        return True
    if ea & eb and overlap>=.32:
        return True
    return overlap>=.69 and len(wa&wb)>=2

def validate_digest(title,points):
    if not isinstance(points,list) or len(points)!=3:return False
    if any(not isinstance(x,str) or len(x)<23 or len(x)>190 or
           x.rstrip().endswith(("…","...","따른","경우","등","및","확대에")) for x in points):
        return False
    joined=" ".join(points)
    if BAD_BODY.search(joined):return False
    entities=entity_tokens(title)
    if entities and not any(e.lower() in joined.lower() for e in entities):
        return False
    terms=words(title)
    relevant=sum(bool(terms & words(point)) for point in points)
    return relevant>=2 or (bool(entities) and relevant>=1)

def deduplicate(items):
    """Preserve best-ranked (input order) original article for each news event."""
    kept=[]
    for item in items:
        if any(item.get("url")==prev.get("url") or duplicate(item,prev) for prev in kept):
            continue
        kept.append(item)
    return kept
