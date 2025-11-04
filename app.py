import os
import re
import time
import requests
from urllib.parse import urlparse
from datetime import datetime
from dateutil import parser as dateparser

import streamlit as st
from duckduckgo_search import DDGS
from bs4 import BeautifulSoup
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# ---------------------------
# Helper functions
# ---------------------------
analyzer = SentimentIntensityAnalyzer()

def safe_request(url, timeout=6):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; PresenceMonitor/1.0)"}
    try:
        return requests.get(url, headers=headers, timeout=timeout)
    except Exception:
        return None

def get_top_results(query, max_results=25):
    """Use DuckDuckGo Search (DDGS) to get top results."""
    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append(r)
    return results[:max_results]

rating_regexes = [
    re.compile(r'([0-5](?:\.\d)?)[/ ]? ?5'),
    re.compile(r'([0-5](?:\.\d)?)\s*out\s*of\s*5', re.I),
    re.compile(r'([0-5](?:\.\d)?)\s*stars?', re.I),
    re.compile(r'(★★★★★|★★★★☆|★★★★|★★★☆|★★★|★★☆|★★|★☆|★)', re.UNICODE),
]

star_map = {'★★★★★':5,'★★★★☆':4,'★★★★':4,'★★★☆':3,'★★★':3,'★★☆':2,'★★':2,'★☆':1,'★':1}

def extract_rating_from_text(text):
    if not text:
        return None
    for rx in rating_regexes:
        m = rx.search(text)
        if m:
            g = m.group(1)
            if g in star_map:
                return star_map[g]
            try:
                val = float(g)
                return max(0.0, min(5.0, round(val,2)))
            except:
                continue
    return None

def extract_snippets_and_date(url, ddg_snippet=None):
    row = {"url": url, "domain": urlparse(url).netloc, "title": "", "snippet": ddg_snippet or "", "rating": None, "date": None}
    r = safe_request(url)
    if not r:
        return row
    try:
        soup = BeautifulSoup(r.text, "html.parser")
        row["title"] = soup.title.string.strip() if soup.title and soup.title.string else ""
        meta = soup.find("meta", {"name":"description"}) or soup.find("meta", {"property":"og:description"})
        text = (meta.get("content") + " ") if meta and meta.get("content") else ""
        for script in soup.find_all("script", {"type":"application/ld+json"}):
            try:
                txt = script.string or ""
                if "ratingValue" in txt:
                    m = re.search(r'"ratingValue"\s*:\s*"?(?P<v>[0-5](?:\.\d)?)"?', txt)
                    if m:
                        row["rating"] = float(m.group('v'))
                if "datePublished" in txt and not row["date"]:
                    m2 = re.search(r'"datePublished"\s*:\s*"(.*?)"', txt)
                    if m2:
                        row["date"] = dateparser.parse(m2.group(1)).isoformat()
            except:
                continue
        for p in soup.find_all(["p","span","li"]):
            if p.string:
                text += p.get_text(separator=" ", strip=True) + " "
        if (lm := r.headers.get("Last-Modified")) and not row["date"]:
            try: row["date"] = dateparser.parse(lm).isoformat()
            except: pass
        if not row["rating"]:
            rating = extract_rating_from_text(text[:8000])
            if rating: row["rating"] = rating
        if not row["snippet"]:
            row["snippet"] = (text.strip()[:300] + "...") if text else ""
    except Exception:
        pass
    return row

def sentiment_score(text):
    if not text: return 0.0
    s = analyzer.polarity_scores(text)
    return s["compound"]

def compute_grade(score):
    return "A" if score>=90 else "B" if score>=80 else "C" if score>=70 else "D" if score>=60 else "F"

def calculate_presence_score(stats):
    w = {"sites":0.35,"rating":0.30,"sent":0.15,"rec":0.10,"comp":0.10}
    sites_score = min(stats["num_websites"],50)/50*100
    rating_score = (stats["avg_rating"] or 0)/5*100
    sent_score = ((stats["avg_sentiment"]+1)/2)*100
    rec_score = 0
    if stats["most_recent_date"]:
        try:
            days=(datetime.utcnow()-dateparser.parse(stats["most_recent_date"])).days
            rec_score = 100 if days<=7 else 80 if days<=30 else 50 if days<=90 else 30 if days<=365 else 10
        except: rec_score=20
    comp_score = stats.get("company_prevalence",0)*100
    total = (w["sites"]*sites_score+w["rating"]*rating_score+
             w["sent"]*sent_score+w["rec"]*rec_score+w["comp"]*comp_score)
    total=max(0,min(100,total))
    return {"score":round(total,2),"grade":compute_grade(total),
            "breakdown":{"sites_score":round(sites_score,1),
                         "rating_score":round(rating_score,1),
                         "sentiment_score":round(sent_score,1),
                         "recency_score":rec_score,
                         "company_score":round(comp_score,1)}}

# ---------------------------
# Streamlit UI
# ---------------------------
st.set_page_config(page_title="Online Presence Monitor", layout="wide")

st.markdown("""
<style>
.report-card{padding:14px;border-radius:12px;box-shadow:0 6px 20px rgba(0,0,0,0.06);background:#fff;}
.kv{font-weight:600;font-size:18px;}
.small{color:#666;font-size:13px;}
.quote{padding:10px;border-radius:8px;margin-bottom:8px;}
.pos{background:linear-gradient(90deg,#e6ffed,#f8fff9);border-left:4px solid #26a641;}
.neg{background:linear-gradient(90deg,#fff0f0,#fff7f7);border-left:4px solid #d32f2f;}
a.source-link{color:#065fd4;text-decoration:none;}
@media (max-width:700px){.kv{font-size:16px;}}
</style>
""", unsafe_allow_html=True)

st.title("🔎 Online Presence Monitor")
st.write("Enter a person's name or brand to analyze their web presence (prototype).")

with st.form("search"):
    col1,col2,col3=st.columns([4,3,1])
    name=col1.text_input("Name or brand", placeholder="e.g. Jane Doe")
    company=col2.text_input("Optional: company/employer")
    submitted=col3.form_submit_button("Analyze")

if not submitted:
    st.info("Type a name and click Analyze.")
    st.stop()
if not name.strip():
    st.error("Please enter a name.")
    st.stop()

with st.spinner("Searching the web (~20–30 s)..."):
    query=name
    results=get_top_results(query, max_results=25)
    parsed=[]; seen=set()
    for r in results:
        url=r.get("href") or r.get("url")
        if not url: continue
        snippet=r.get("body") or r.get("snippet") or ""
        info=extract_snippets_and_date(url,snippet)
        parsed.append(info)
        seen.add(info["domain"])
    num=len(parsed)
    ratings=[p["rating"] for p in parsed if p["rating"]]
    avg_rating=round(sum(ratings)/len(ratings),2) if ratings else None
    quotes=[]
    for p in parsed:
        s=p["snippet"] or ""
        sentences=re.split(r'(?<=[.!?])\s+',s)
        selected=next((sent for sent in sentences if name.lower().split()[0] in sent.lower()), s[:240])
        quotes.append({**p,"quote":selected,"sentiment":sentiment_score(selected)})
    avg_sent=round(sum(q["sentiment"] for q in quotes)/len(quotes),3) if quotes else 0
    dates=[p["date"] for p in parsed if p["date"]]
    most_recent=max(dates) if dates else None
    comp_prev=0
    if company:
        matches=sum(1 for p in parsed if safe_request(p["url"]) and company.lower() in safe_request(p["url"]).text.lower())
        comp_prev=matches/max(1,num)
    stats={"num_websites":num,"unique_domains":len(seen),"avg_rating":avg_rating,
           "avg_sentiment":avg_sent,"most_recent_date":most_recent,"company_prevalence":comp_prev}
    grade=calculate_presence_score(stats)

# ---------------------------
# Output
# ---------------------------
left,right=st.columns([2,1])
with left:
    st.markdown("<div class='report-card'>", unsafe_allow_html=True)
    st.subheader(f"Overview for: {name}")
    st.write(f"**Sites found:** {stats['num_websites']}")
    st.write(f"**Unique domains:** {stats['unique_domains']}")
    st.write(f"**Average rating:** {stats['avg_rating'] or 'N/A'} /5")
    st.markdown(f"### Overall Grade: {grade['grade']}  ({grade['score']} / 100)")
    bd=grade["breakdown"]
    st.write("#### Score breakdown")
    for k,v in bd.items(): st.write(f"- {k.replace('_',' ').title()}: {v}")
    st.markdown("</div>", unsafe_allow_html=True)
with right:
    st.markdown("<div class='report-card'>", unsafe_allow_html=True)
    st.subheader("Tips")
    st.write("• Add a company name for better context.")
    st.write("• Rerun weekly to track trends.")
    st.markdown("</div>", unsafe_allow_html=True)

tab1,tab2=st.tabs(["Quotes","Sources"])
with tab1:
    pos=[q for q in quotes if q["sentiment"]>=0.2]
    neg=[q for q in quotes if q["sentiment"]<=-0.2]
    if pos:
        st.markdown("**Positive quotes**")
        for q in pos[:10]:
            st.markdown(f"<div class='quote pos'><b>{q.get('title') or q['domain']}</b> — “{q['quote']}” <br><a class='source-link' href='{q['url']}' target='_blank'>source</a></div>", unsafe_allow_html=True)
    if neg:
        st.markdown("**Negative quotes**")
        for q in neg[:10]:
            st.markdown(f"<div class='quote neg'><b>{q.get('title') or q['domain']}</b> — “{q['quote']}” <br><a class='source-link' href='{q['url']}' target='_blank'>source</a></div>", unsafe_allow_html=True)
with tab2:
    st.subheader("All sources")
    for p in parsed:
        rating=f" — rating {p['rating']}/5" if p['rating'] else ""
        date=f" — {p['date'][:10]}" if p['date'] else ""
        st.markdown(f"- <a class='source-link' href='{p['url']}' target='_blank'>{p.get('title') or p['url']}</a> ({p['domain']}{rating}{date})", unsafe_allow_html=True)

try:
    import pandas as pd
    st.download_button("Download CSV", data=pd.DataFrame(parsed).to_csv(index=False),
                       file_name=f"presence_{name.replace(' ','_')}.csv")
except Exception:
    pass
