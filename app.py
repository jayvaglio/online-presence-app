import os
import re
import requests
from urllib.parse import urlparse
from datetime import datetime
from dateutil import parser as dateparser

import streamlit as st
from bs4 import BeautifulSoup
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# ---------------------------
# Config
# ---------------------------
st.set_page_config(page_title="Online Presence Monitor", layout="wide")
st.title("🔎 Online Presence Monitor")
st.write("Enter a person's name or brand to analyze their web presence.")

analyzer = SentimentIntensityAnalyzer()

# ---------------------------
# Secrets & Debug
# ---------------------------
API_KEY = st.secrets.get("GOOGLE_API_KEY")
CSE_ID = st.secrets.get("GOOGLE_CSE_ID")

debug_mode = st.checkbox("🛠 Enable Debug Mode", value=False)

if debug_mode:
    st.header("Debug Mode")
    st.subheader("Secrets Check")
    st.write("API_KEY present:", bool(API_KEY))
    st.write("CSE_ID present:", bool(CSE_ID))
    if not API_KEY or not CSE_ID:
        st.error("⚠️ Google API Key or CSE ID missing. Add them to Streamlit secrets.")
    else:
        # Test a live API request
        test_query = "Test Query"
        url = f"https://www.googleapis.com/customsearch/v1?q={test_query}&key={API_KEY}&cx={CSE_ID}&num=1"
        try:
            r = requests.get(url, timeout=10)
            st.write("API response code:", r.status_code)
            data = r.json()
            st.write("API response keys:", list(data.keys()))
            if "items" in data and len(data["items"]) > 0:
                st.success("✅ API returned results")
                st.write("First result:", data["items"][0].get("title"), "-", data["items"][0].get("link"))
            else:
                st.warning("⚠️ No results returned. Check your CSE settings (must search the entire web).")
        except Exception as e:
            st.error(f"Google API request failed: {e}")

if not API_KEY or not CSE_ID:
    st.stop()

# ---------------------------
# Helper functions
# ---------------------------
def safe_request(url, timeout=6):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; PresenceMonitor/1.0)"}
    try:
        return requests.get(url, headers=headers, timeout=timeout)
    except Exception:
        return None

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

def extract_snippets_and_date(url, snippet=None):
    row = {"url": url, "domain": urlparse(url).netloc, "title": "", "snippet": snippet or "", 
           "rating": None, "date": None, "full_text": ""}
    r = safe_request(url)
    if not r:
        return row
    try:
        text = ""
        soup = BeautifulSoup(r.text, "html.parser")
        row["title"] = soup.title.string.strip() if soup.title and soup.title.string else ""
        meta = soup.find("meta", {"name":"description"}) or soup.find("meta", {"property":"og:description"})
        text += (meta.get("content") + " ") if meta and meta.get("content") else ""
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
        row["full_text"] = text.lower()  # store full lowercase text for company prevalence
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
# Google API search
# ---------------------------
def get_top_results(query, max_results=25):
    results = []
    try:
        for start in range(1, max_results+1, 10):  # pagination
            url = f"https://www.googleapis.com/customsearch/v1?q={query}&key={API_KEY}&cx={CSE_ID}&num={min(max_results,10)}&start={start}"
            r = requests.get(url)
            data = r.json()
            for item in data.get("items", []):
                results.append({
                    "title": item.get("title"),
                    "href": item.get("link"),
                    "body": item.get("snippet")
                })
            if len(results) >= max_results:
                break
    except Exception as e:
        st.error(f"Google Search API failed: {e}")
    return results[:max_results]

# ---------------------------
# User input form
# ---------------------------
with st.form("search"):
    col1,col2,col3 = st.columns([4,3,1])
    name = col1.text_input("Name or brand", placeholder="e.g. Jane Doe")
    company = col2.text_input("Optional: company/employer")
    submitted = col3.form_submit_button("Analyze")

if not submitted:
    st.info("Type a name and click Analyze.")
    st.stop()
if not name.strip():
    st.error("Please enter a name.")
    st.stop()

# ---------------------------
# Fetch and analyze
# ---------------------------
with st.spinner("Fetching search results..."):
    results = get_top_results(name, max_results=25)
    parsed = []
    seen = set()
    for r in results:
        url = r.get("href")
        snippet = r.get("body") or ""
        info = extract_snippets_and_date(url, snippet)
        parsed.append(info)
        seen.add(info["domain"])

    num = len(parsed)
    ratings = [p["rating"] for p in parsed if p["rating"]]
    avg_rating = round(sum(ratings)/len(ratings),2) if ratings else None

    quotes = []
    for p in parsed:
        s = p["snippet"] or ""
        sentences = re.split(r'(?<=[.!?])\s+', s)
        selected = next((sent for sent in sentences if name.lower().split()[0] in sent.lower()), s[:240])
        quotes.append({**p, "quote": selected, "sentiment": sentiment_score(selected)})

    avg_sent = round(sum(q["sentiment"] for q in quotes)/len(quotes),3) if quotes else 0
    dates = [p["date"] for p in parsed if p["date"]]
    most_recent = max(dates) if dates else None

    # Optimized company prevalence
    comp_prev = 0
    if company:
        matches = sum(1 for p in parsed if company.lower() in p["full_text"])
        comp_prev = matches / max(1, num)
        if debug_mode:
            st.write(f"Pages containing '{company}': {matches} / {num}")

    stats = {
        "num_websites": num,
        "unique_domains": len(seen),
        "avg_rating": avg_rating,
        "avg_sentiment": avg_sent,
        "most_recent_date": most_recent,
        "company_prevalence": comp_prev
    }

    grade = calculate_presence_score(stats)

# ---------------------------
# Output UI
# ---------------------------
left,right = st.columns([2,1])
with left:
    st.subheader(f"Overview for: {name}")
    st.write(f"**Sites found:** {stats['num_websites']}")
    st.write(f"**Unique domains:** {stats['unique_domains']}")
    st.write(f"**Average rating:** {stats['avg_rating'] or 'N/A'} /5")
    st.markdown(f"### Overall Grade: {grade['grade']}  ({grade['score']} / 100)")

with right:
    st.subheader("Tips")
    st.write("• Add a company name for better context.")
    st.write("• Rerun weekly to track trends.")

tab1, tab2 = st.tabs(["Quotes","Sources"])
with tab1:
    pos = [q for q in quotes if q["sentiment"] >= 0.2]
    neg = [q for q in quotes if q["sentiment"] <= -0.2]
    if pos:
        st.markdown("**Positive quotes**")
        for q in pos[:10]:
            st.markdown(f"**{q.get('title') or q['domain']}** — “{q['quote']}” [source]({q['url']})")
    if neg:
        st.markdown("**Negative quotes**")
        for q in neg[:10]:
            st.markdown(f"**{q.get('title') or q['domain']}** — “{q['quote']}” [source]({q['url']})")

with tab2:
    st.subheader("All sources")
    for p in parsed:
        rating = f" — rating {p['rating']}/5" if p['rating'] else ""
        date = f" — {p['date'][:10]}" if p['date'] else ""
        st.markdown(f"- [{p.get('title') or p['url']}]({p['url']}) ({p['domain']}{rating}{date})")

try:
    import pandas as pd
    st.download_button("Download CSV", data=pd.DataFrame(parsed).to_csv(index=False),
                       file_name=f"presence_{name.replace(' ','_')}.csv")
except Exception:
    pass
