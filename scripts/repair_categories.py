"""Reclassify already-archived headlines by their dominant IB financial sector.

No fabricated articles. Articles are moved only when a sector is supported
by their headline / source excerpt; uncertain articles are omitted.
"""
import json
import re
from pathlib import Path
from sector_rules import classify_sector

ROOT=Path(__file__).resolve().parents[1]/"docs"
FILES=[ROOT/"data.json"]+sorted((ROOT/"archive").glob("????-??-??.json"))
SECTORS=("ecm","corp","ma","re","infra","hana","peer","sto","productive")

def normalized(item):
    title=str(item.get("headline") or item.get("title") or "")
    summary=item.get("summary") or []
    if isinstance(summary,str):
        summary=[summary]
    desc=re.sub(r"\*+", "", " ".join(str(x) for x in summary))
    return classify_sector(title,desc)

def repair(payload):
    if not isinstance(payload.get("categories"),dict):
        return payload,0,0
    initial=payload["categories"]
    fixed={key:[] for key in SECTORS}
    moved=0
    dropped=0
    seen=set()
    for old,items in initial.items():
        if not isinstance(items,list):
            continue
        for article in items:
            if not isinstance(article,dict):
                continue
            url=article.get("url")
            if not url or url in seen:
                dropped+=1
                continue
            target=normalized(article)
            if target not in fixed:
                dropped+=1
                continue
            if len(fixed[target])>=3:
                dropped+=1
                continue
            seen.add(url)
            if old!=target:
                moved+=1
            fixed[target].append(article)
    for items in fixed.values():
        for idx,item in enumerate(items):
            item["top"]=(idx==0)
    payload["categories"]=fixed
    valid_urls={item.get("url") for items in fixed.values() for item in items}
    for report in payload.get("reports",[]):
        if isinstance(report,dict):
            urls=report.get("related_article_urls")
            if isinstance(urls,list):
                report["related_article_urls"]=[url for url in urls if url in valid_urls]
    return payload,moved,dropped

def main():
    for p in FILES:
        if not p.is_file():
            continue
        original=p.read_text(encoding="utf-8")
        payload=json.loads(original)
        updated,moved,dropped=repair(payload)
        rendered=json.dumps(updated,ensure_ascii=False,indent=2)+"\n"
        if rendered!=original:
            p.write_text(rendered,encoding="utf-8")
        print(f"{p.name}: moved={moved}, dropped={dropped}, totals="
              +", ".join(f"{k}:{len(v)}" for k,v in updated["categories"].items()),flush=True)

if __name__=="__main__":
    main()
