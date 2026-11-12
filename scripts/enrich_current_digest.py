"""One-off enrichment of the currently published briefing.

Keeps all titles, publishers, URLs, category assignments and dates unchanged.
Where full publisher text is readable, replaces an incomplete search description
with 2–3 source-grounded key points. Does not generate unsupported analysis.
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime
from article_digest import digest

DOCS = Path(__file__).resolve().parents[1] / "docs"
LATEST = DOCS / "data.json"

def main():
    payload = json.loads(LATEST.read_text(encoding="utf-8"))
    date = str(payload.get("generated_at", ""))[:10]
    articles = [
        item for group in payload.get("categories", {}).values()
        for item in group
        if item.get("url") and item.get("summary_mode")=="source_excerpt"
    ]
    print(f"Digest {len(articles)} search excerpts from {date}",flush=True)
    enriched=0
    def work(item):
        try:
            return item,digest(item.get("headline",""),item["url"]," ".join(item.get("summary",[])))
        except Exception as exc:
            print(f"  unavailable {item.get('url')}: {exc}",flush=True)
            return item,None
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures=[pool.submit(work,item) for item in articles]
        for fut in as_completed(futures):
            item, summary=fut.result()
            if summary:
                item["summary"]=summary
                item["summary_mode"]="source_digest"
                enriched+=1
    if enriched==0:
        print("No publicly readable full articles; preserving original source descriptions.")
        return
    text=json.dumps(payload,ensure_ascii=False,indent=2)+"\n"
    LATEST.write_text(text,encoding="utf-8")
    day=DOCS/"archive"/(date+".json")
    if day.exists():
        archived=json.loads(day.read_text(encoding="utf-8"))
        by_url={item["url"]:item for group in payload["categories"].values() for item in group}
        for group in archived.get("categories",{}).values():
            for item in group:
                other=by_url.get(item.get("url"))
                if other and other.get("summary_mode")=="source_digest":
                    item["summary"]=other["summary"]
                    item["summary_mode"]="source_digest"
        day.write_text(json.dumps(archived,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(f"Enriched {enriched} of {len(articles)} verified original sources; all URLs retained.",flush=True)

if __name__=="__main__":
    main()
