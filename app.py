# app_v0_2.py
"""
Version 0.2 - modular review sources, Yelp JSON integration, per-source debug,
removed Quick Actions. Built on top of your v0.1 baseline.
"""

import re
import json
import requests
from urllib.parse import quote_plus, urlparse
from datetime import datetime
from dateutil import parser as dateparser

import streamlit as st
from bs4 import BeautifulSoup
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
import pandas as pd
import plotly.express as px

# ---------------------------
# Page config
# ---------------------------
st.set_page_config(page_title="Online Presence Monitor — v0.2", layout="wide")
st.title("🔎 Online Presence Monitor — v0.2")

analyzer = SentimentIntensityAnalyzer()

# ---------------------------
# Secrets / Keys (single Google key)
# ---------------------------
API_KEY = st.secrets.get("GOOGLE_API_KEY")
CSE_ID = st.secrets.get("GOOGLE_CSE_ID")

# ---------------------------
# UI: Debug toggle
# ---------------------------
debug_mode = st.checkbox("🛠 Enable Debug Mode", value=False)
if debug_mode:
    st.caption("Debug Mode ON — detailed diagnostics will appear in Debug expanders.")

# ---------------------------
# Constants
# ---------------------------
MAX_RESULTS = 25
MAX_REVIEWS_PER_SOURCE = 5

# ---------------------------
# Helpers
# ---------------------------
def safe_get(url, headers=None, timeout=10):
    headers = headers or {"User-Agent": "Mozilla/5.0 (compatible; PresenceMonitor/1.0)"}
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        return r
    except Exception as e:
        if debug_mode:
            st.write(f"[safe_get] Error fetching {url}: {e}")
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
                return float(g)
            except:
                continue
    return None

def sentiment_score(text):
    if not text:
        return 0.0
    return analyzer.polarity_scores(text)["compound"]

# ---------------------------
# Google Custom Search (CSE)
# ---------------------------
@st.cache_data(ttl=600)
def get_top_results(query, max_results=25):
    results = []
    if not API_KEY or not CSE_ID:
        if debug_mode:
            st.write("[get_top_results] Missing GOOGLE_API_KEY or GOOGLE_CSE_ID.")
        return results
    try:
        for start in range(1, max_results+1, 10):
            url = f"https://www.googleapis.com/customsearch/v1?q={quote_plus(query)}&key={API_KEY}&cx={CSE_ID}&num={min(max_results,10)}&start={start}"
            r = requests.get(url, timeout=10)
            data = r.json()
            for item in data.get("items", []):
                results.append({
                    "title": item.get("title"),
                    "href": item.get("link"),
                    "snippet": item.get("snippet")
                })
            if len(results) >= max_results:
                break
    except Exception as e:
        if debug_mode:
            st.write(f"[get_top_results] error: {e}")
    return results[:max_results]

# ---------------------------
# Google Places (Maps) details
# ---------------------------
@st.cache_data(ttl=3600)
def get_google_places_details(query):
    """Find place via findplacefromtext then return details (including reviews)"""
    if not API_KEY:
        if debug_mode:
            st.write("[get_google_places_details] Missing API_KEY.")
        return None, {"debug": "missing_api_key"}
    try:
        find_url = f"https://maps.googleapis.com/maps/api/place/findplacefromtext/json?input={quote_plus(query)}&inputtype=textquery&fields=place_id,name,formatted_address&key={API_KEY}"
        r = requests.get(find_url, timeout=8)
        d = r.json()
        if debug_mode:
            st.write("[get_google_places_details] findplace response keys:", list(d.keys()))
        candidates = d.get("candidates", [])
        if not candidates:
            return None, {"debug": "no_candidates", "find_response": d}
        place = candidates[0]
        place_id = place.get("place_id")
        details_url = f"https://maps.googleapis.com/maps/api/place/details/json?place_id={place_id}&fields=name,rating,user_ratings_total,reviews,url&key={API_KEY}"
        r2 = requests.get(details_url, timeout=8)
        details = r2.json().get("result", {})
        debug_payload = {"debug": "success", "place_id": place_id, "place_name": place.get("name")}
        return details, debug_payload
    except Exception as e:
        if debug_mode:
            st.write(f"[get_google_places_details] error: {e}")
        return None, {"debug": f"exception:{e}"}

# ---------------------------
# Yelp JSON fetch (preferred) + HTML fallback (light)
# ---------------------------
@st.cache_data(ttl=3600)
def fetch_yelp_reviews_json(query, max_reviews=MAX_REVIEWS_PER_SOURCE):
    """
    1) Use Google CSE to find a Yelp /biz/ link for the query.
    2) Request Yelp's review_feed JSON endpoint and parse reviews.
    3) If fails, return empty list (HTML fallback is possible if you want).
    Returns: list of reviews dict and debug info.
    """
    out = []
    debug = {"status": "init", "cse_query": f"{query} site:yelp.com", "found_biz_url": None, "feed_status": None, "parsed": 0}
    if not API_KEY or not CSE_ID:
        debug["status"] = "missing_google_keys"
        if debug_mode:
            st.write("[fetch_yelp_reviews_json] Missing API keys.")
        return out, debug

    # 1) find via CSE
    candidates = get_top_results(f"{query} site:yelp.com", max_results=8)
    biz_url = None
    for c in candidates:
        href = c.get("href","")
        if "/biz/" in href:
            biz_url = href.split("?")[0]
            break
    debug["found_biz_url"] = biz_url
    if not biz_url:
        debug["status"] = "no_biz_url"
        if debug_mode:
            st.write("[fetch_yelp_reviews_json] No /biz/ URL found via CSE.")
        return out, debug

    # 2) derive alias and fetch feed
    try:
        alias = biz_url.rstrip("/").split("/biz/")[-1].split("/")[0]
    except Exception as e:
        debug["status"] = "alias_error"
        debug["alias_error"] = str(e)
        return out, debug

    feed_url = f"https://www.yelp.com/biz/{alias}/review_feed?start=0&sort_by=date_desc"
    debug["feed_url"] = feed_url
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; PresenceMonitor/1.0)",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": biz_url
    }
    try:
        r = requests.get(feed_url, headers=headers, timeout=10)
        debug["feed_status"] = r.status_code
        if r.status_code != 200:
            debug["status"] = "feed_non_200"
            if debug_mode:
                st.write(f"[fetch_yelp_reviews_json] feed returned {r.status_code} for {feed_url}")
            return out, debug
        # parse JSON or JSON blob
        data = None
        try:
            data = r.json()
        except Exception:
            # attempt to extract JSON object with "reviews" array from HTML
            m = re.search(r"(\{.*\"reviews\":\s*\[.*\]\s*\})", r.text, re.S)
            if m:
                try:
                    data = json.loads(m.group(1))
                except Exception:
                    data = None
        if not data:
            debug["status"] = "no_json_payload"
            return out, debug
        reviews = data.get("reviews") or data.get("review_list") or []
        debug["status"] = "json_parsed"
        debug["raw_reviews_count"] = len(reviews)
        # parse top reviews
        count = 0
        for rv in reviews:
            if count >= max_reviews:
                break
            text = rv.get("comment") or rv.get("excerpt") or rv.get("text") or ""
            rating = rv.get("rating") or rv.get("rating_score") or None
            author = None
            try:
                if rv.get("user"):
                    author = rv["user"].get("markup_display_name") or rv["user"].get("display_name")
            except:
                author = None
            time = rv.get("localizedDate") or rv.get("time") or rv.get("published") or rv.get("date")
            out.append({
                "site": "Yelp",
                "rating": float(rating) if rating else None,
                "text": text.strip(),
                "url": biz_url,
                "author": author,
                "time": time
            })
            count += 1
        debug["parsed"] = len(out)
    except Exception as e:
        debug["status"] = "exception"
        debug["exception"] = str(e)
        if debug_mode:
            st.write(f"[fetch_yelp_reviews_json] exception: {e}")
        return out, debug

    return out, debug

# ---------------------------
# Healthgrades (simple HTML-based)
# ---------------------------
@st.cache_data(ttl=3600)
def fetch_healthgrades_reviews(query, max_reviews=MAX_REVIEWS_PER_SOURCE):
    out = []
    debug = {"status":"init","source_url":None,"parsed":0}
    try:
        # Use CSE to find Healthgrades pages
        candidates = get_top_results(f"{query} site:healthgrades.com", max_results=5)
        if not candidates:
            debug["status"] = "no_candidates"
            return out, debug
        url = candidates[0].get("href")
        debug["source_url"] = url
        r = safe_get(url)
        if not r:
            debug["status"] = "fetch_failed"
            return out, debug
        soup = BeautifulSoup(r.text, "html.parser")
        revs = soup.find_all("div", class_=re.compile("review|patient|comment"), limit=max_reviews)
        parsed = 0
        for rb in revs:
            text = rb.get_text(separator=" ", strip=True)
            rating = extract_rating_from_text(text)
            out.append({"site":"Healthgrades","rating":rating,"text":text,"url":url,"author":"","time":""})
            parsed += 1
        debug["status"] = "parsed" if parsed>0 else "no_reviews_found"
        debug["parsed"] = parsed
    except Exception as e:
        debug["status"] = "exception"
        debug["exception"] = str(e)
        if debug_mode:
            st.write(f"[fetch_healthgrades_reviews] ex: {e}")
    return out, debug

# ---------------------------
# Glassdoor (simple HTML-based)
# ---------------------------
@st.cache_data(ttl=3600)
def fetch_glassdoor_reviews(query, max_reviews=MAX_REVIEWS_PER_SOURCE):
    out = []
    debug = {"status":"init","source_url":None,"parsed":0}
    try:
        candidates = get_top_results(f"{query} site:glassdoor.com \"Reviews\"", max_results=5)
        if not candidates:
            debug["status"] = "no_candidates"
            return out, debug
        url = candidates[0].get("href")
        debug["source_url"] = url
        r = safe_get(url)
        if not r:
            debug["status"] = "fetch_failed"
            return out, debug
        soup = BeautifulSoup(r.text, "html.parser")
        revs = soup.find_all("p", limit=max_reviews)
        parsed = 0
        for rb in revs:
            text = rb.get_text(separator=" ", strip=True)
            if len(text) < 30:
                continue
            rating = extract_rating_from_text(text)
            out.append({"site":"Glassdoor","rating":rating,"text":text,"url":url,"author":"","time":""})
            parsed += 1
        debug["status"] = "parsed" if parsed>0 else "no_reviews_found"
        debug["parsed"] = parsed
    except Exception as e:
        debug["status"] = "exception"
        debug["exception"] = str(e)
        if debug_mode:
            st.write(f"[fetch_glassdoor_reviews] ex: {e}")
    return out, debug

# ---------------------------
# REVIEW SOURCES registry (modular)
# ---------------------------
REVIEW_SOURCES = {
    "google_places": {
        "fn": lambda q: ([], {"status": "not_called"}),
        "tab_name": "Google Reviews",
        "enabled": True
    },
    "yelp": {
        "fn": fetch_yelp_reviews_json,
        "tab_name": "Yelp Reviews",
        "enabled": True
    },
    "healthgrades": {
        "fn": fetch_healthgrades_reviews,
        "tab_name": "Healthgrades (fallback)",
        "enabled": True
    },
    "glassdoor": {
        "fn": fetch_glassdoor_reviews,
        "tab_name": "Glassdoor (fallback)",
        "enabled": True
    }
}

# We wire google_places separately because it uses a different function signature
REVIEW_SOURCES["google_places"]["fn"] = lambda q: (fetch_google_places_reviews(q), {"status":"google_places_called"})

# ---------------------------
# Presence scoring & radar helpers
# ---------------------------
def calculate_presence_score(num_websites, avg_rating, avg_sentiment, most_recent_date, company_prevalence=0.0):
    # weights same as before
    w = {"sites":0.35,"rating":0.30,"sent":0.15,"rec":0.10,"comp":0.10}
    sites_score = min(num_websites,50)/50*100
    rating_score = (avg_rating or 0)/5*100
    sent_score = ((avg_sentiment or 0)+1)/2*100
    rec_score = 0
    if most_recent_date:
        try:
            days = (datetime.utcnow() - dateparser.parse(most_recent_date)).days
            rec_score = 100 if days<=7 else 80 if days<=30 else 50 if days<=90 else 30 if days<=365 else 10
        except:
            rec_score = 20
    comp_score = company_prevalence*100
    total = (w["sites"]*sites_score + w["rating"]*rating_score + w["sent"]*sent_score +
             w["rec"]*rec_score + w["comp"]*comp_score)
    total = max(0, min(100, total))
    # grade
    if total >= 90: grade = "A"
    elif total >= 80: grade = "B"
    elif total >= 70: grade = "C"
    elif total >= 60: grade = "D"
    else: grade = "F"
    breakdown = {"Sites": round(sites_score,1), "Rating": round(rating_score,1),
                 "Sentiment": round(sent_score,1), "Recency": rec_score, "Company": round(comp_score,1)}
    return {"score": round(total,2), "grade": grade, "breakdown": breakdown}

def plot_radar(breakdown):
    df = pd.DataFrame({
        "Category": list(breakdown.keys()),
        "Score": list(breakdown.values())
    })
    try:
        theme_base = st.get_option("theme.base")
    except:
        theme_base = "light"
    template = "plotly_dark" if theme_base == "dark" else "plotly"
    fig = px.line_polar(df, r='Score', theta='Category', line_close=True, template=template)
    fig.update_traces(fill='toself')
    fig.update_layout(title="Presence Breakdown", polar=dict(radialaxis=dict(range=[0,100])))
    return fig

# ---------------------------
# UI: Query input (single input - user friendly)
# ---------------------------
with st.form("search"):
    query_raw = st.text_input("Search Query (name / profession / city)", placeholder="e.g. Monstera's Books bookstore Overland Park")
    submitted = st.form_submit_button("Run Analysis")

if not submitted:
    st.info("Enter a search query and click Run Analysis. Enable Debug Mode for diagnostics.")
    st.stop()

canonical_query = query_raw.strip()
if debug_mode:
    st.write("Canonical query:", canonical_query)

# ---------------------------
# Run CSE (top results) to get sites/mentions
# ---------------------------
cse_results = get_top_results(canonical_query, max_results=MAX_RESULTS) if (API_KEY and CSE_ID) else []
parsed_sites = []
domains = set()
for item in cse_results:
    href = item.get("href")
    title = item.get("title")
    snippet = item.get("snippet")
    entry = {"url": href, "title": title, "snippet": snippet, "domain": urlparse(href).netloc if href else "", "rating": None, "date": None, "full_text": ""}
    # light fetch to extract rating/snippet if possible
    try:
        r = safe_get(href)
        if r and r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            entry["title"] = entry["title"] or (soup.title.string.strip() if soup.title else "")
            meta = soup.find("meta", {"name":"description"}) or soup.find("meta", {"property":"og:description"})
            txt = ""
            if meta and meta.get("content"):
                txt += meta.get("content") + " "
            for el in soup.find_all(["p","span","li","blockquote"]):
                txt += el.get_text(separator=" ", strip=True) + " "
            entry["full_text"] = txt.lower()
            entry["snippet"] = entry["snippet"] or (txt.strip()[:300] + "...") if txt else entry["snippet"]
            entry["rating"] = extract_rating_from_text(txt[:8000])
    except Exception as e:
        if debug_mode:
            st.write(f"[site parse] error for {href}: {e}")
    parsed_sites.append(entry)
    if entry.get("domain"):
        domains.add(entry["domain"])

num_websites = len(parsed_sites)
unique_domains = len(domains)

# ---------------------------
# Gather reviews from modular sources
# ---------------------------
all_reviews = []
source_debug = {}

# Google Places (special handling)
places_details, places_debug = get_google_places_details(canonical_query) if API_KEY else (None, {"debug":"no_api_key"})
if places_details:
    # convert to reviews list (top MAX_REVIEWS_PER_SOURCE)
    gp_reviews = []
    for r in (places_details.get("reviews") or [])[:MAX_REVIEWS_PER_SOURCE]:
        gp_reviews.append({
            "site":"Google",
            "rating": r.get("rating"),
            "text": r.get("text"),
            "url": places_details.get("url"),
            "author": r.get("author_name"),
            "time": r.get("relative_time_description")
        })
    all_reviews.extend(gp_reviews)
source_debug["google_places"] = places_debug

# iterate other registry sources (yelp, healthgrades, glassdoor)
for key, meta in REVIEW_SOURCES.items():
    if key == "google_places":
        continue
    if not meta.get("enabled", True):
        source_debug[key] = {"status":"disabled"}
        continue
    try:
        fn = meta["fn"]
        reviews, dbg = fn(canonical_query)
        # ensure list shape
        reviews = reviews or []
        if isinstance(reviews, tuple) and len(reviews)==2:
            # some functions return (list, debug)
            reviews, dbg = reviews
        all_reviews.extend(reviews)
        source_debug[key] = dbg if dbg else {"status":"no_debug"}
    except Exception as e:
        source_debug[key] = {"status":"exception", "exception": str(e)}
        if debug_mode:
            st.write(f"[source loop] {key} exception: {e}")

# ---------------------------
# Aggregate stats
# ---------------------------
ratings = [r["rating"] for r in all_reviews if r.get("rating") is not None]
avg_rating = round(sum(ratings)/len(ratings),2) if ratings else None
sentiments = [sentiment_score((r.get("text") or "")[:400]) for r in all_reviews]
avg_sentiment = round(sum(sentiments)/len(sentiments),3) if sentiments else 0.0
dates = [r.get("time") for r in all_reviews if r.get("time")]
most_recent = None
# attempt to parse times to get a recency heuristic (best-effort)
for d in dates:
    try:
        # if relative_time_description (e.g., "2 months ago") skip; look for ISO-like
        dt = None
        try:
            dt = dateparser.parse(d)
        except:
            continue
        if dt:
            if not most_recent or dt > most_recent:
                most_recent = dt.isoformat()
    except:
        continue

# company prevalence placeholder
company_prevalence = 0.0

presence = calculate_presence_score(num_websites, avg_rating, avg_sentiment, most_recent, company_prevalence)

# ---------------------------
# Output UI (Overview)
# ---------------------------
left, right = st.columns([2,1])
with left:
    st.subheader(f"Overview — {canonical_query}")
    st.write(f"**Sites found:** {num_websites}")
    st.write(f"**Unique domains:** {unique_domains}")
    st.write(f"**Average rating (aggregated):** {avg_rating or 'N/A'}")
    st.write(f"**Average sentiment (compound):** {avg_sentiment}")
    st.markdown(f"### Overall Grade: {presence['grade']}  ({presence['score']} / 100)")

    # Radar chart
    fig = plot_radar(presence["breakdown"])
    st.plotly_chart(fig, use_container_width=True)

with right:
    st.subheader("Summary")
    st.write("Results aggregated from modular review sources. Enable Debug Mode to see per-source diagnostics.")

# ---------------------------
# Tabs: Quotes, Sources, Google Reviews, Yelp Reviews
# ---------------------------
tab_quotes, tab_sources, tab_google, tab_yelp = st.tabs(["Quotes","Sources","Google Reviews","Yelp Reviews"])

with tab_quotes:
    st.subheader("Extracted quotes & sentiment")
    pos = [r for r in all_reviews if sentiment_score((r.get("text") or "")[:300]) >= 0.2]
    neg = [r for r in all_reviews if sentiment_score((r.get("text") or "")[:300]) <= -0.2]
    if pos:
        st.markdown("**Positive**")
        for p in pos[:10]:
            st.markdown(f"**{p.get('site')}** — “{(p.get('text') or '')[:300]}” — ⭐ {p.get('rating')}")
    if neg:
        st.markdown("**Negative**")
        for n in neg[:10]:
            st.markdown(f"**{n.get('site')}** — “{(n.get('text') or '')[:300]}” — ⭐ {n.get('rating')}")
    if not pos and not neg:
        st.info("No sentiment-rich quotes extracted.")

with tab_sources:
    st.subheader("All sources used / validation links")
    if parsed_sites:
        for p in parsed_sites:
            st.markdown(f"- [{p.get('title') or p.get('url')}]({p.get('url')}) — {p.get('domain')}")
    else:
        st.info("No top search results were parsed.")

with tab_google:
    st.subheader("Google Maps / Places Reviews")
    if places_details:
        st.write(f"**Place:** {places_details.get('name')}")
        st.write(f"**Avg rating:** {places_details.get('rating')} ({places_details.get('user_ratings_total')} total)")
        if places_details.get("url"):
            st.markdown(f"[View on Google Maps]({places_details.get('url')})")
        for r in (places_details.get("reviews") or [])[:MAX_REVIEWS_PER_SOURCE]:
            st.markdown(f"“{r.get('text','')[:400]}” — {r.get('author_name','Anonymous')} ({r.get('relative_time_description','')})")
    else:
        st.info("No Google Maps/Places listing or reviews found for this query.")
    if debug_mode:
        st.write("Google Places debug:", source_debug.get("google_places"))

with tab_yelp:
    st.subheader("Yelp Reviews (JSON feed)")
    yelp_reviews = [r for r in all_reviews if r.get("site")=="Yelp"]
    if yelp_reviews:
        for r in yelp_reviews[:MAX_REVIEWS_PER_SOURCE]:
            st.markdown(f"**Yelp** — ⭐ {r.get('rating') or 'N/A'} — “{(r.get('text') or '')[:400]}” — {r.get('author') or 'Anonymous'}")
            if r.get("url"):
                st.markdown(f"[Source]({r.get('url')})")
    else:
        st.info("No Yelp reviews found via JSON feed for this query.")
    # Show Yelp debug info
    if debug_mode:
        st.write("Yelp debug:", source_debug.get("yelp"))

# ---------------------------
# Debug (global)
# ---------------------------
if debug_mode:
    with st.expander("🧩 Debug Details (all sources) — expanded", expanded=False):
        st.write("Canonical query:", canonical_query)
        st.write("Parsed top search results count:", num_websites)
        st.write("Google CSE sample (first 5):", cse_results[:5])
        st.write("Per-source debug info:")
        for k,v in source_debug.items():
            st.write(f"- {k}:", v)
        st.write("Aggregated reviews sample (first 10):", all_reviews[:10])

# ---------------------------
# CSV Download
# ---------------------------
try:
    df = pd.DataFrame(all_reviews)
    if not df.empty:
        st.download_button("Download aggregated reviews (.csv)", data=df.to_csv(index=False), file_name="presence_reviews.csv", mime="text/csv")
except Exception:
    pass
