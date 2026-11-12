"""Offline regression tests; this module never calls any API."""
from news_quality import duplicate, numeric_claims_grounded, deduplicate
from news_materiality import is_material_news
from sector_rules import classify_sector

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
    material={
        "materiality":88,"importance":90,"depth":83,"quality":87,
        "impact_type":"policy_regulation",
        "impact_reason":"코너스톤 투자자 제도 도입으로 IPO 기관 배정과 주관 경쟁의 새로운 판단 기준 발생",
        "impact_evidence":"코너스톤 투자자 제도 시행",
    }
    title="코너스톤 투자자 제도 시행, IPO 주관 전략 변화"
    assert is_material_news(material,title,"기관 투자자 배정 방식 변화로 공모시장 주관 경쟁 영향")
    # Amount alone, or a company's ordinary results, is not enough.
    routine={**material,"impact_type":"transaction_precedent","materiality":68,
             "impact_evidence":"2,000억원 회사채 발행"}
    assert not is_material_news(routine,"A사 2,000억원 회사채 발행 완료","2,000억원 회사채 발행 공시")
    generic={**material,"impact_reason":"시장 확대"}
    assert not is_material_news(generic,title,"코너스톤 투자자 제도 시행")
    invented={**material,"impact_evidence":"전혀 없는 정책 변경"}
    assert not is_material_news(invented,title,"코너스톤 투자자 제도 시행")
    # A small individual transaction is eligible only if it establishes
    # a concrete financial-structure precedent and has source evidence.
    precedent={**material,"materiality":90,
               "impact_type":"transaction_precedent",
               "impact_reason":"신규 재구조화 방식의 선례로 부동산 PF 위험 분담과 차환 방식에 영향",
               "impact_evidence":"첫 PF 재구조화 사례"}
    assert is_material_news(precedent,"첫 PF 재구조화 사례 발표","소규모 자산의 새로운 금융구조 적용")
    assert classify_sector("'대기업과 다르다'…자회사 상장 절실한 중견기업들, 내년 주총이 분수령",
                          "중복상장 규제로 FI 투자금 회수 부담 확대") == "ecm"
    assert classify_sector("[중복상장 규제 진단] '솔리다임 닮은꼴' 보스턴다이내믹스, 아틀라스 양산 자금은",
                          "중복상장 규제에 따른 해외 자회사 IPO 조달 제약") == "ecm"
    assert classify_sector("네이버파이낸셜·두나무 기업결합, 중복상장 규제 변수",
                          "향후 IPO와 주주보호 쟁점") == "ecm"
    assert classify_sector(
        "정부 1호 타이틀 신안우이해상풍력 중단 위기",
        "국민성장펀드 1호 사업, EPC 공사대금 미지급 분쟁",
    ) == "growthfund", "Fund's inaugural-project risk must be on Growth Fund tab"
    assert classify_sector(
        "국민성장펀드 10조 목표 차이와 예산심사 논란",
        "연간 40조 투자 목표와 제출 자료의 차이",
    ) == "growthfund"
    assert classify_sector(
        "하나금융, 기관 중심이던 IR 개인주주로 확대",
        "하나금융 기업가치 제고·주주환원 IR 간담회 개최",
    ) == "hana"
    assert classify_sector(
        "하나금융, 누적 순익 3.6조 견조…하나은행 건전성 관리 총력",
        "대손비용과 NPL 커버리지비율 부담",
    ) == "hana"
    # Tomorrow's executive dashboard should retain verified strategic stories
    # from today's dated archive if new IB news is scarce at 07:30.
    from datetime import datetime
    from generate_briefing import SECTIONS, KST, fill_sector_from_recent_archive
    next_day = datetime(2026, 11, 14, 7, 15, tzinfo=KST)
    empty = {key: [] for key in SECTIONS}
    result = fill_sector_from_recent_archive(empty, next_day)
    assert len(result) == 10
    assert all(2 <= len(group) <= 3 for group in result.values())
    assert all(story.get("carried_forward") for items in result.values()
               for story in items)
    assert all(story.get("pubDate") != "2026-11-14"
               for items in result.values() for story in items)
    print("PASS: duplicate-event and source-grounded number and IB materiality checks")

if __name__ == "__main__":
    check()
