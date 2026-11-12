"""Offline regression tests; this module never calls any API."""
from news_quality import duplicate, numeric_claims_grounded, deduplicate

def check():
    first={"title":"금감원, IMA·발행어음 증권사에 고금리 경쟁 자제 당부","url":"https://example.com/a"}
    second={"title":"금감원, 증권사 고금리 발행어음에 주의 당부","url":"https://example.com/b"}
    assert duplicate(first,second), "Same regulatory event must deduplicate"
    assert len(deduplicate([first,second]))==1
    different={"title":"SK렌터카, 2년 만기 회사채 발행 수요예측","url":"https://example.com/c"}
    assert not duplicate(first,different)
    assert numeric_claims_grounded(
        ["회사채 경쟁률 **8%** 기록","2년물 수요 확대","3년물 수요 변화"],
        "회사채 8% 경쟁률, 2년물·3년물 투자 수요 변화"
    )
    assert not numeric_claims_grounded(
        ["프로젝트 2019년 착수","브릿지론 700억원","자금 조달"],
        "프로젝트 2022년 착수, 브릿지론 700억원"
    ), "Unsupported date must be blocked"
    assert not numeric_claims_grounded(
        ["사업 목표 200조원","성장펀드 지원","지역 투자 확대"],
        "사업 목표 150조원, 국민성장펀드 지역 투자 확대"
    ), "Unsupported magnitude must be blocked"
    print("PASS: duplicate-event and source-grounded number checks")

if __name__ == "__main__":
    check()
