"""Source-grounded extractive IB article digest.

No LLM or fabricated inference. Extracts the article body (when publicly
readable), ranks fact-bearing sentences, and returns up to 3 short points.
If access is blocked/paywalled, returns only a clearly marked search excerpt.
"""
import html
import json
import re
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup

HEADERS={"User-Agent":"Mozilla/5.0 (compatible; IBNewsDigest/1.0; +https://github.com/juyeong0379/Juyeong)",
         "Accept-Language":"ko-KR,ko;q=0.9"}
BAD=re.compile(r"^(사진|자료|출처|기자|관련 기사|관련기사|이 기사는|본 기사는|Copyright|ⓒ|무단전재|구독|광고|기사제보|페이스북|카카오톡|네이버|구글|표제|기획|홈으로|전체메뉴|더보기|ADVERTISEMENT)",re.I)
MARKET=re.compile(r"억원|조원|억달러|%|bp|금리|차환|주관|인수|지분|발행|계약|PF|M&A|IPO|공모|사채|신용|투자|펀드|리츠|대출|규제|심사|자금")
SELECTORS=("article","[itemprop=articleBody]","[class*=article-body]","[class*=article_body]",
           "[id*=articleBody]","[id*=article_body]","[class*=news_view]","[id*=newsView]",
           "[class*=news_content]","[class*=view-content]","[class*=articleView]")

def clean(text):
    text=html.unescape(str(text or ""))
    return re.sub(r"\s+"," ",text.replace("\u200b"," ")).strip()

def _jsonld_text(tree):
    for script in tree.select('script[type="application/ld+json"]'):
        try:
            j=json.loads(script.string or script.get_text())
        except (ValueError,TypeError):
            continue
        stack=[j]
        while stack:
            item=stack.pop()
            if isinstance(item,list):
                stack.extend(item)
            elif isinstance(item,dict):
                if item.get("articleBody") and isinstance(item["articleBody"],str):
                    return clean(item["articleBody"])
                stack.extend(v for v in item.values() if isinstance(v,(dict,list)))
    return ""

def get_article_body(url):
    if not url.startswith(("https://","http://")):
        return ""
    try:
        res=requests.get(url,headers=HEADERS,timeout=7,allow_redirects=True)
        if res.status_code!=200 or len(res.content)>4_000_000:
            return ""
        if "html" not in res.headers.get("content-type","text/html").lower():
            return ""
        tree=BeautifulSoup(res.content,"html.parser")
        structured=_jsonld_text(tree)
        for tag in tree.select("script,style,nav,header,footer,aside,form,button,iframe,figure,figcaption"):
            tag.decompose()
        if len(structured)>420:
            return structured[:16000]
        candidates=[clean(el.get_text(" ",strip=True)) for selector in SELECTORS
                    for el in tree.select(selector)]
        candidates=[v for v in candidates if len(v)>=350]
        if not candidates:
            return ""
        # Prefer the shortest qualifying article block to avoid website-wide boilerplate.
        candidates.sort(key=lambda x:(abs(len(x)-2600),len(x)))
        body=candidates[0][:16000]
        if len(body)>9000 and len(body.split(" "))<50:
            return ""
        return body
    except (requests.RequestException,ValueError,UnicodeError):
        return ""

def sentences(body):
    parts=re.split(r"(?<=[.!?。])\s+|(?<=다\.)\s*|(?<=임\.)\s*|[\r\n]+",body)
    normalized=[]
    seen=set()
    for item in parts:
        t=clean(item).strip(" \t-·●")
        if not 35<=len(t)<=230 or BAD.search(t):
            continue
        if re.search(r"로그인|동의합니다|개인정보|구독 신청|기사 공유|이메일 주소|보도자료 다운로드",t):
            continue
        key=re.sub(r"[\W_]","",t.lower())[:80]
        if key in seen:continue
        seen.add(key)
        normalized.append(t)
    return normalized

def digest(title,url,description="",max_points=3):
    body=get_article_body(url)
    if len(body)<420:
        return None
    ss=sentences(body)
    if len(ss)<3:
        return None
    title_terms=set(w.lower() for w in re.findall(r"[가-힣]{2,}|[A-Za-z]{2,}",title)
                    if len(w)>=3 and w not in {"더벨","기획","단독","특집"})
    ranked=[]
    for idx,t in enumerate(ss[:90]):
        overlap=sum(w in t.lower() for w in title_terms)
        score=min(8,overlap*2)+min(6,len(MARKET.findall(t))*2)+max(0,3-idx//7)
        if re.search(r"반면|다만|그러나|한편|이에 따라|따르면|예상|전망|부담",t):
            score+=2
        if len(t)>150:score-=3
        ranked.append((score,idx,t))
    ranked.sort(key=lambda x:(-x[0],x[1]))
    selected=[]
    for _,idx,t in ranked:
        if any(len(set(re.findall(r"[가-힣A-Za-z0-9]+",t)) & set(re.findall(r"[가-힣A-Za-z0-9]+",prev))) > 8 for _,prev in selected):
            continue
        selected.append((idx,t))
        if len(selected)==max_points:
            break
    if len(selected)<2:
        return None
    selected.sort()
    # Selected passages are short quotations from the readable source, not invented prose.
    return [t[:125].rstrip(" ,;:") + ("…" if len(t)>125 else "") for _,t in selected]
