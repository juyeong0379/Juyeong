"""Reclassify already-published news with exclusive IB sector rules.

Safe to run multiple times on current and archived news.

Idempotent. Preserves genuine article URL, source, original summary provenance
and publication date; removes stories unrelated to any IB category.
"""
import json
from pathlib import Path
from sector_rules import classify_sector

ROOT=Path(__file__).resolve().parents[1]
KEYS=("ecm","corp","ma","re","infra","hana","peer","sto","productive")
FILES=[ROOT/"docs"/"data.json",*sorted((ROOT/"docs"/"archive").glob("????-??-??.json"))]

def run(path):
    data=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("categories"),dict):
        return
    categories={key:[] for key in KEYS}
    seen=set()
    removed=0
    moved=0
    original=data["categories"]
    for existing,items in original.items():
        if not isinstance(items,list):
            continue
        for article in items:
            if not isinstance(article,dict) or not article.get("url"):
                continue
            url=article["url"]
            if url in seen:
                continue
            seen.add(url)
            title=article.get("headline",article.get("title",""))
            summary=article.get("summary",[])
            excerpt=" ".join(str(x).replace("**","") for x in summary[:2]) if isinstance(summary,list) else ""
            key=classify_sector(title,excerpt)
            if key is None:
                removed+=1
                continue
            if len(categories[key])>=3:
                continue
            if key!=existing:
                moved+=1
            categories[key].append(article)
    for key,items in categories.items():
        for i,article in enumerate(items):
            article["top"]=i==0
            if "summary_mode" not in article and len(article.get("summary",[]))==1:
                article["summary_mode"]="source_excerpt"
    data["categories"]=categories
    reports=data.get("reports",[])
    eligible={a["url"] for items in categories.values() for a in items}
    if isinstance(reports,list):
        for report in reports:
            report["related_article_urls"]=[u for u in report.get("related_article_urls",[]) if u in eligible]
        data["reports"]=[r for r in reports if r.get("related_article_urls")]
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(f"{path.name}: moved={moved}, excluded={removed}, total={sum(map(len,categories.values()))}",flush=True)

def main():
    for path in FILES:
        if path.exists():
            run(path)
if __name__=="__main__":
    main()

# Tested with Meritz/REIT disambiguation and unrelated-article veto.
