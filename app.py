import os
import re
import time
import requests
from urllib.parse import urlparse
from datetime import datetime
from dateutil import parser as dateparser

import streamlit as st
from duckduckgo_search import ddg
from bs4 import BeautifulSoup
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# ---------------------------
# Helper functions
# ---------------------------
analyzer = SentimentIntensityAnalyzer()

def safe_request(url, timeout=6):
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; PresenceMonitor/1.0; +https://example.com)"
    }
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        return r
    except Exception as e:
        return None

def get_top_results(query, max_results=25):
    """Use duckduckgo_search ddg to get top results (title, href, body snippet)."""
    results = ddg(query, max_results)
    # ddg returns list of dicts with 'title','body','href'
    return results[:max_results] if results else []

rating_regexes = [
    re.compile(r'([0-5](?:\.\d)?)[/ ]? ?5'),         # 4.5/5  or 4.5 5
    re.compile(r'([0-5](?:\.\d)?)\s*out\s*of\s*5', re.I),
    re.compile(r'([0-5](?:\.\d)?)\s*stars?', re.I),
    re.compile(r'(★★★★★|★★★★☆|★★★★|★★★☆|★★★|★★☆|★★|★☆|★)', re.UNICODE),
]

star_map = {
    '★★★★★':5,'★★★★☆':4.0,'★★★★':4.0,'★★★☆':3.0,'★★★':3.0,'★★☆':2.0,'★★':2.0,'★☆':1.0,'★':1.0
}

def extract_rating_from_text(text):
    if not text: return None
    # look for numeric patterns
    for rx in rating_regexes:
        m = rx.search(text)
        if m:
            g = m.group(1)
            # if stars characters
            if g in star_map:
                return star_map[g]
            try:
                val = float(g)
                # clamp 0-5
                if val > 5: val = 5.0
                if val < 0: val = 0.0
                return round(val, 2)
            except:
                continue
    return None

def extract_snippets_and_date(url, ddg_snippet=None):
    """
    Return dictionary with:
      - url, domain, title, snippet (text), rating (maybe), date (maybe)
    """
    row = {"url": url, "domain": urlparse(url).netloc, "title": "", "snippet": ddg_snippet or "", "rating": None, "date": None}
    r = safe_request(url)
    if not r:
        return row
    text = ""
    try:
        soup = BeautifulSoup(r.text, "html.parser")
        # title
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        row["title"] = title
        # meta description
        meta = soup.find("meta", {"name":"description"}) or soup.find("meta", {"property":"og:description"})
        if meta and meta.get("content"):
            text += meta.get("content") + " "
        # try to find review rating via schema.org json-ld
        for script in soup.find_all("script", {"type":"application/ld+json"}):
            try:
                txt = script.string
                if not txt: continue
                # quick find rating
                # naive search for "ratingValue"
                if "ratingValue" in txt:
                    m = re.search(r'"ratingValue"\s*:\s*"?(?P<v>[0-5](?:\.\d)?)"?', txt)
                    if m:
                        row["rating"] = float(m.group('v'))
                # datePublished
                if "datePublished" in txt and row["date"] is None:
                    m2 = re.search(r'"datePublished"\s*:\s*"(.*?)"', txt)
                    if m2:
                        try:
                            row["date"] = dateparser.parse(m2.group(1)).isoformat()
                        except:
                            pass
            except Exception:
                continue
        # fallback: page text
        for p in soup.find_all(["p","span","li"]):
            if p.string:
                text += p.get_text(separator=" ", strip=True) + " "
        # last-modified header
        lm = r.headers.get("Last-Modified")
        if lm and row["date"] is None:
            try:
                row["date"] = dateparser.parse(lm).isoformat()
            except:
                pass
        # search for inline date patterns
        if row["date"] is None:
            m = re.search(r'(\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s*\d{4})', r.text, re.I)
            if m:
                try:
                    row["date"] = dateparser.parse(m.group(1)).isoformat()
                except:
                    pass
        # extract rating from page content if not found
        if row["rating"] is None:
            rtxt = text[:8000]  # limit
            rating = extract_rating_from_text(rtxt)
            if rating:
                row["rating"] = rating
        # snippet fallback to first 300 chars of text
        if not row["snippet"]:
            row["snippet"] = (text.strip()[:300] + "...") if text else ""
    except Exception as e:
        # ignore parsing errors
        pass
    return row

def sentiment_score(text):
    if not text: return 0.0
    s = analyzer.polarity_scores(text)
    return s["compound"]  # -1..1

def compute_grade(score):
    # score 0..100 -> A-F
    if score >= 90: return "A"
    if score >= 80: return "B"
    if score >= 70: return "C"
    if score >= 60: return "D"
    return "F"

def calculate_presence_score(stats):
    # Weighted sum:
    # websites_count weight 0.35 (normalized)
    # avg_rating weight 0.30 (normalized to 0-100 using 5->100)
    # sentiment weight 0.15 (compound -1..1 -> 0..100)
    # recency weight 0.10 (0..100)
    # company_prevalence weight 0.10
    w_sites = 0.35
    w_rating = 0.30
    w_sent = 0.15
    w_recency = 0.10
    w_comp = 0.10

    # normalize sites: expecting maybe 0..50; map to 0..100 with diminishing returns
    n_sites = min(stats["num_websites"], 50)
    sites_score = (n_sites / 50) * 100

    avg_rating = stats["avg_rating"] or 0.0
    rating_score = (avg_rating / 5.0) * 100

    sent = stats["avg_sentiment"]  # -1..1
    sent_score = ((sent + 1) / 2.0) * 100

    # recency: if last_seen within 7 days => 100, within 30 days => 80, 90 days => 50, else low
    recency_score = 0
    if stats["most_recent_date"]:
        try:
            dt = dateparser.parse(stats["most_recent_date"])
            delta = (datetime.utcnow() - dt).days
            if delta <= 7:
                recency_score = 100
            elif delta <= 30:
                recency_score = 80
            elif delta <= 90:
                recency_score = 50
            elif delta <= 365:
                recency_score = 30
            else:
                recency_score = 10
        except:
            recency_score = 20
    # company prevalence currently provided as a fraction 0..1
    comp_score = stats.get("company_prevalence", 0) * 100

    total = (w_sites * sites_score) + (w_rating * rating_score) + (w_sent * sent_score) + (w_recency * recency_score) + (w_comp * comp_score)
    total = max(0, min(100, total))
    grade = compute_grade(total)
    return {"score": round(total,2), "grade": grade, "breakdown":{
        "sites_score": round(sites_score,1),
        "rating_score": round(rating_score,1),
        "sentiment_score": round(sent_score,1),
        "recency_score": recency_score,
        "company_score": round(comp_score,1)
    }}

# ---------------------------
# Streamlit UI
# ---------------------------
st.set_page_config(page_title="Online Presence Monitor", layout="wide", initial_sidebar_state="auto")

# minimal responsive CSS
st.markdown("""
<style>
/* mobile friendly widths */
.report-card { padding:14px; border-radius:12px; box-shadow: 0 6px 20px rgba(0,0,0,0.06); background: #fff; }
.kv { font-weight:600; font-size:18px; }
.small { color:#666; font-size:13px; }
.overview { display:flex; gap:14px; flex-wrap:wrap; }
.quote { padding:10px; border-radius:8px; margin-bottom:8px; }
.pos { background: linear-gradient(90deg, #e6ffed, #f8fff9); border-left:4px solid #26a641; }
.neg { background: linear-gradient(90deg, #fff0f0, #fff7f7); border-left:4px solid #d32f2f; }
a.source-link { color:#065fd4; text-decoration: none; }
@media (max-width: 700px) {
  .kv { font-size:16px; }
}
</style>
""", unsafe_allow_html=True)

st.title("🔎 Online Presence Monitor")
st.write("Enter a person's name or brand and review a quick summary of their web presence (prototype).")

with st.form("search"):
    col1, col2, col3 = st.columns([4,3,1])
    with col1:
        name = st.text_input("Name or brand to analyze", placeholder="e.g. Jane Doe or ACME Co.")
    with col2:
        company = st.text_input("Optional: company / employer (helps prevalence)", placeholder="Optional")
    with col3:
        submitted = st.form_submit_button("Analyze")

if not submitted:
    st.info("Type a name (and optionally a company) and click Analyze.")
    st.stop()

if not name.strip():
    st.error("Please enter a name to search for.")
    st.stop()

with st.spinner("Searching the web and analyzing results (this can take ~10–30s depending on pages)..."):
    query = f"{name}"
    # optionally include company in search to prefer direct references
    results = get_top_results(query, max_results=25)
    parsed = []
    seen_domains = set()
    for r in results:
        url = r.get("href") or r.get("url")
        snippet = r.get("body") or r.get("snippet") or ""
        if not url: continue
        info = extract_snippets_and_date(url, ddg_snippet=snippet)
        parsed.append(info)
        seen_domains.add(info["domain"])

    # some basic stats
    num_websites = len(parsed)
    ratings = [p["rating"] for p in parsed if p.get("rating") is not None]
    avg_rating = round(sum(ratings)/len(ratings),2) if ratings else None

    # sentiment and quote extraction: choose top 2 sentences from snippet / page text with name mentions
    quotes = []
    for p in parsed:
        s = p["snippet"] or ""
        # find sentences containing name or short snippet around name
        # split sentences
        sentences = re.split(r'(?<=[.!?])\s+', s)
        selected = None
        for sent in sentences:
            if len(sent) < 8: continue
            if name.lower().split()[0] in sent.lower() or len(sent) > 80:
                selected = sent
                break
        if not selected:
            selected = s[:240]
        sent_score = sentiment_score(selected)
        quotes.append({**p, "quote": selected, "sentiment": sent_score})

    # compute sentiment aggregates
    if quotes:
        avg_sentiment = round(sum(q["sentiment"] for q in quotes)/len(quotes),3)
    else:
        avg_sentiment = 0.0

    # most recent date seen
    dates = [p["date"] for p in parsed if p.get("date")]
    most_recent = max(dates) if dates else None

    # company prevalence: naive count of occurrences of the company string on pages
    comp_prevalence = 0.0
    if company:
        matches = 0
        for p in parsed:
            r = safe_request(p["url"])
            if r and company.lower() in r.text.lower():
                matches += 1
        comp_prevalence = matches / max(1, num_websites)

    stats = {
        "num_websites": num_websites,
        "unique_domains": len(seen_domains),
        "avg_rating": avg_rating,
        "avg_sentiment": avg_sentiment,
        "most_recent_date": most_recent,
        "company_prevalence": comp_prevalence
    }

    grade_data = calculate_presence_score(stats)

# ---------------------------
# Output: Overview & Tabs
# ---------------------------
left, right = st.columns([2,1])

with left:
    st.markdown("<div class='report-card'>", unsafe_allow_html=True)
    st.subheader(f"Overview for: {name}")
    st.write(f"**Query:** {query}")
    st.write("---")
    c1, c2, c3 = st.columns(3)
    c1.markdown(f"<div class='kv'>{stats['num_websites']}</div><div class='small'>Sites found</div>", unsafe_allow_html=True)
    c2.markdown(f"<div class='kv'>{stats['unique_domains']}</div><div class='small'>Unique domains</div>", unsafe_allow_html=True)
    avg_rating_display = f"{stats['avg_rating']} / 5" if stats['avg_rating'] else "N/A"
    c3.markdown(f"<div class='kv'>{avg_rating_display}</div><div class='small'>Avg rating (detected)</div>", unsafe_allow_html=True)

    st.write("")
    st.markdown(f"**A–F Score:** <span style='font-weight:700; font-size:22px'>{grade_data['grade']}</span> — <span style='color:#333'>{grade_data['score']} / 100</span>", unsafe_allow_html=True)
    st.write("**Score breakdown**")
    bd = grade_data["breakdown"]
    st.write(f"- Sites score: {bd['sites_score']} /100")
    st.write(f"- Rating score: {bd['rating_score']} /100")
    st.write(f"- Sentiment score: {bd['sentiment_score']} /100")
    st.write(f"- Recency score: {bd['recency_score']} /100")
    st.write(f"- Company prevalence score: {bd['company_score']} /100")

    st.write("---")
    st.markdown("<div class='small'>Notes: Ratings and quotes are extracted heuristically. For higher accuracy, connect a paid search/review API and implement site-specific parsers.</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    # map quick visual of recent presence
    st.markdown("<div class='report-card' style='margin-top:12px'>", unsafe_allow_html=True)
    st.subheader("Quick recent snapshot")
    if stats["most_recent_date"]:
        try:
            dt = dateparser.parse(stats["most_recent_date"])
            days_ago = (datetime.utcnow() - dt).days
            st.write(f"Most recent found mention: **{dt.date().isoformat()}** ({days_ago} days ago)")
        except:
            st.write(f"Most recent found mention: **{stats['most_recent_date']}**")
    else:
        st.write("No publication dates were detected on the top pages.")
    st.markdown("</div>", unsafe_allow_html=True)

with right:
    st.markdown("<div class='report-card'>", unsafe_allow_html=True)
    st.subheader("Quick actions")
    st.write("- Improve accuracy: add company or supply your own list of sources.")
    st.write("- For continuous monitoring: run this periodically or deploy with a scheduler + database.")
    st.markdown("</div>", unsafe_allow_html=True)

# Tabs: Quotes and Sources
tab1, tab2 = st.tabs(["Quotes", "All Sources"])

with tab1:
    st.subheader("Positive & Negative Quotes (extracted)")
    if not quotes:
        st.info("No quote snippets were extracted from the top results.")
    else:
        # sort quotes by sentiment
        pos = sorted([q for q in quotes if q["sentiment"] >= 0.2], key=lambda x: -x["sentiment"])
        neg = sorted([q for q in quotes if q["sentiment"] <= -0.2], key=lambda x: x["sentiment"])
        neutral = [q for q in quotes if -0.2 < q["sentiment"] < 0.2]

        if pos:
            st.markdown("**Positive**")
            for q in pos[:10]:
                st.markdown(f"<div class='quote pos'><strong>{q.get('title') or q['domain']}</strong> — \"{q['quote']}\" <br><a class='source-link' href='{q['url']}' target='_blank'>source</a> — sentiment {q['sentiment']}</div>", unsafe_allow_html=True)
        if neg:
            st.markdown("**Negative**")
            for q in neg[:10]:
                st.markdown(f"<div class='quote neg'><strong>{q.get('title') or q['domain']}</strong> — \"{q['quote']}\" <br><a class='source-link' href='{q['url']}' target='_blank'>source</a> — sentiment {q['sentiment']}</div>", unsafe_allow_html=True)
        if neutral:
            st.markdown("**Neutral / unclear**")
            for q in neutral[:8]:
                st.markdown(f"<div class='quote'><strong>{q.get('title') or q['domain']}</strong> — \"{q['quote']}\" <br><a class='source-link' href='{q['url']}' target='_blank'>source</a> — sentiment {q['sentiment']}</div>", unsafe_allow_html=True)

with tab2:
    st.subheader("Sources (top results)")
    st.write("Validate: open any link in a new tab and inspect the page.")
    if not parsed:
        st.info("No sources available.")
    else:
        for p in parsed:
            rating = f" — rating: {p['rating']}/5" if p.get("rating") else ""
            date = f" — date: {p['date'].split('T')[0]}" if p.get("date") else ""
            st.markdown(f"- <a class='source-link' href='{p['url']}' target='_blank'>{p.get('title') or p['url']}</a> <span class='small'>{p['domain']}{rating}{date}</span>", unsafe_allow_html=True)

# Footer - quick download of results as CSV
try:
    import pandas as pd
    df = pd.DataFrame(parsed)
    st.download_button("Download raw results (.csv)", data=df.to_csv(index=False), file_name=f"presence_{name.replace(' ','_')}.csv", mime="text/csv")
except Exception:
    pass
