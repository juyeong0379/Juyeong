"""One-time, idempotent 7-day history backfill from genuine article publication dates.

Never fills a missing day with fabricated items. It skips existing daily archives,
uses reputable publishers, and writes only articles that pass the IB relevance
and depth classifier.
"""
import json
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

from generate_briefing import (
    KST, SECTIONS, BATCH_SIZE, SHORT_NEWS_PATTERN, TOP_N_PER_SECTION,
    classify_batch, normalize_title, outlet_name, strip_html,
)

API = "https://naverapihub.apigw.ntruss.com/search/v1/news"
DATA_DIR = Path(__file__).resolve().parents[1] / "docs"
ARCHIVE_DIR = DATA_DIR / "archive"

def historical_items(query, earliest):
    headers = {
        "X-NCP-APIGW-API-KEY-ID": os.environ["NAVER_CLIENT_ID"],
        "X-NCP-APIGW-API-KEY": os.environ["NAVER_CLIENT_SECRET"],
    }
    out = []
    for start in (1, 101, 201, 301, 401):
        try:
            resp = requests.get(API, headers=headers, params={
                "query":query, "display":100, "start":start, "sort":"date",
            }, timeout=12)
            resp.raise_for_status()
            items = resp.json().get("items", [])
        except Exception as exc:
            print(f"  [historical fetch] {query} page {start}: {exc}", flush=True)
            break
        if not items:
            break
        oldest = None
        for item in items:
            try:
                published = parsedate_to_datetime(item.get("pubDate","")).astimezone(KST)
            except Exception:
                continue
            day = published.date()
            if oldest is None or day < oldest:
                oldest = day
            if day < earliest:
                continue
            title = strip_html(item.get("title", ""))
            url = item.get("originallink", "")
            source = outlet_name(url)
            if not source or not title or SHORT_NEWS_PATTERN.search(title):
                continue
            out.append({
                "title":title,
                "description":strip_html(item.get("description", "")),
                "reporter":"",
                "url":url,
                "source":source,
                "pub_dt":published,
                "matched_keyword":query,
            })
        if oldest and oldest < earliest:
            break
        if len(items) < 100:
            break
        time.sleep(0.1)
    return out

def get_missing_days():
    now = datetime.now(KST).date()
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    days = []
    for n in range(6, 0, -1):
        d = now - timedelta(days=n)
        file = ARCHIVE_DIR / (d.isoformat() + ".json")
        if not file.exists():
            days.append(d)
            continue
        try:
            past = json.loads(file.read_text(encoding="utf-8"))
            if any(len(past.get("categories", {}).get(k, [])) < 2 for k in SECTIONS):
                days.append(d)
        except (ValueError, OSError):
            days.append(d)
    return days

def main():
    missing = get_missing_days()
    if not missing:
        print("No missing dates in last 7 days.", flush=True)
        return
    earliest = missing[0]
    print(f"Backfilling real news by publication date: {missing[0]} to {missing[-1]}",flush=True)
    by_day = {day.isoformat(): {key: [] for key in SECTIONS} for day in missing}
    used_per_day = defaultdict(set)

    # Search each sector once for the full week, rather than seven separate
    # API scans per day. Newer daily briefings keep their own archival dates.
    for key, meta in SECTIONS.items():
        print(f"Searching older news: {meta['label']}",flush=True)
        pool = []
        unique = set()
        for query in meta["keywords"][:4]:
            for item in historical_items(query, earliest):
                k = normalize_title(item["title"])
                if item["url"] in unique or k in unique:
                    continue
                unique.update((item["url"],k))
                pool.append(item)
        pool.sort(key=lambda x:x["pub_dt"],reverse=True)
        for day in missing:
            dstr = day.isoformat()
            items = [x for x in pool if x["pub_dt"].date()==day
                     and x["url"] not in used_per_day[dstr]]
            if not items:
                continue
            # 16 substantive candidates per sector/day; do not pad with
            # unverified snippets when reputable deep articles are scarce.
            evaluated = []
            for start in range(0,min(len(items),16),BATCH_SIZE):
                evaluated.extend(classify_batch(meta,items[start:start+BATCH_SIZE]))
            evaluated.sort(key=lambda x:x["score"],reverse=True)
            top=evaluated[:TOP_N_PER_SECTION]
            used_per_day[dstr].update(x["url"] for x in top)
            by_day[dstr][key] = [
                {"top":i==0,"headline":x["title"],"source":x["source"],
                 "pubDate":x["pub_dt"].strftime("%Y-%m-%d"),
                 "summary":x["summary"][:3],"insight":x.get("insight",""),
                 "summary_mode":x.get("summary_mode","ai_summary"),
                 "reporter":x.get("reporter",""),"url":x["url"]}
                for i,x in enumerate(top)
            ]
            print(f"  {dstr} {meta['label']}: {len(top)}",flush=True)
    now=datetime.now(KST)
    for day in missing:
        dstr=day.isoformat()
        categories=by_day[dstr]
        existing_file = ARCHIVE_DIR / (dstr+".json")
        if existing_file.exists():
            old_payload = json.loads(existing_file.read_text(encoding="utf-8"))
            previous = old_payload.get("categories", {})
            for key in SECTIONS:
                verified = list(previous.get(key, []))
                known_urls = {item.get("url") for item in verified}
                for item in categories.get(key, []):
                    if len(verified) >= TOP_N_PER_SECTION:
                        break
                    if item.get("url") not in known_urls:
                        verified.append(item)
                        known_urls.add(item.get("url"))
                categories[key] = verified[:TOP_N_PER_SECTION]
        if not any(categories.values()):
            print(f"  {dstr}: no eligible published articles. Skipping.",flush=True)
            continue
        payload={
            "generated_at":dstr+" 00:00 KST (발행일 기준 소급 구축)",
            "archive_basis":"article_publication_date",
            "sections":[{"key":k,"no":m["no"],"label":m["label"],"desc":m["desc"]}
                        for k,m in SECTIONS.items()],
            "categories":categories,
            "reports":[],
        }
        with (ARCHIVE_DIR / (dstr+".json")).open("w",encoding="utf-8") as f:
            json.dump(payload,f,ensure_ascii=False,indent=2)
    threshold=(now.date()-timedelta(days=29)).isoformat()
    existing=sorted(p.stem for p in ARCHIVE_DIR.glob("????-??-??.json")
                    if p.stem >= threshold)
    with (ARCHIVE_DIR / "index.json").open("w",encoding="utf-8") as f:
        json.dump(existing,f,ensure_ascii=False,indent=2)
    print("Archived days: "+", ".join(existing),flush=True)

if __name__=="__main__":
    main()
