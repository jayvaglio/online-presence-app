import re
import requests
from urllib.parse import urlparse
from datetime import datetime
from dateutil import parser as dateparser
import streamlit as st
from bs4 import BeautifulSoup
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
import pandas as pd
import plotly.express as px

# ---------------------------
# Config
# ---------------------------
st.set_page_config(page_title="Online Presence Monitor", layout="wide")
st.title("🔎 Online Presence Monitor")
st.write("Analyze a person's or brand's online presence across multiple sources.")

analyzer = SentimentIntensityAnalyzer()

# ---------------------------
# Secrets
# ---------------------------
API_KEY = st.secrets.get("GOOGLE_API_KEY")
CSE_ID = st.secrets.get("GOOGLE_CSE_ID")

# ---------------------------
# Debug Mode
# ---------------------------
debug_mode = st.checkbox("🛠 Enable Debug Mode", value=True)

if debug_mode:
    st.header("Debug Mode")
    st.write("API_KEY present:", bool(API_KEY))
    st.write("CSE_ID present:", bool(CSE_ID))
    if not API_KEY or not CSE_ID:
        st.warning("⚠️ Google API Key or CSE ID missing! Add them to Streamlit secrets.")

if not API_KEY or not CSE_ID:
    st.info("Enter valid API_KEY and CSE_ID in Streamlit secrets to enable analysis.")
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
        for script in soup.find_all(["p","span","li"]):
            if script.string:
                text += script.get_text(separator=" ", strip=True) + " "
        row["full_text"] = text.lower()
        if not row["snippet"]:
            row["snippet"] = (text.strip()[:300] + "...") if text else ""
        row["rating"] = extract_rating_from_text(text[:8000])
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
# Google Custom Search
# ---------------------------
def get_top_results(query, max_results=25):
    results = []
    try:
        for start in range(1, max_results+1, 10):
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
# Google Places Reviews
# ---------------------------
@st.cache_data(ttl=3600)
def get_google_reviews(name, city=None):
    """Fetch Google Places reviews if available."""
    try:
        query = f"{name} {city}" if city else name
        search_url = f"https://maps.googleapis.com/maps/api/place/findplacefromtext/json?input={query}&inputtype=textquery&fields=place_id,name,formatted_address&key={API_KEY}"
        r = requests.get(search_url)
        data = r.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return None
        place = candidates[0]
        place_id = place["place_id"]
        details_url = f"https://maps.googleapis.com/maps/api/place/details/json?place_id={place_id}&fields=name,rating,user_ratings_total,reviews,url&key={API_KEY}"
        r2 = requests.get(details_url)
        details = r2.json().get("result", {})
        return details
    except Exception as e:
        if debug_mode:
            st.error(f"Google Places API error: {e}")
        return None

# ---------------------------
# Direct HTML Parsing for Review Sites
# ---------------------------
@st.cache_data(ttl=3600)
def parse_site_reviews(name, city=None):
    """Scrape Healthgrades, Glassdoor, Yelp, RateMDs (top 5 each)"""
    reviews = []

    # Define site search patterns
    site_patterns = {
        "Healthgrades": f"https://www.healthgrades.com/search?what={name}&where={city or ''}",
        "Glassdoor": f"https://www.glassdoor.com/Reviews/{name.replace(' ','-')}-Reviews.htm",
        # Add Yelp, RateMDs, etc.
    }

    for site, url in site_patterns.items():
        r = safe_request(url)
        if not r:
            continue
        try:
            soup = BeautifulSoup(r.text, "html.parser")
            # Simple heuristic: find first 5 reviews
            rev_blocks = soup.find_all("div", class_=re.compile("review|rating"), limit=5)
            for rb in rev_blocks[:5]:
                text = rb.get_text(separator=" ", strip=True)
                rating = extract_rating_from_text(text)
                reviews.append({
                    "site": site,
                    "text": text,
                    "rating": rating,
                    "url": url
                })
        except Exception:
            continue
    return reviews

# ---------------------------
# User input form
# ---------------------------
with st.form("search"):
    col1,col2,col3,col4 = st.columns([3,2,2,2])
    name = col1.text_input("Name or brand", placeholder="e.g. Jane Doe")
    company = col2.text_input("Company / Employer (optional)")
    city = col3.text_input("City / Location (optional)")
    category = col4.text_input("Profession / Category (optional)")
    submitted = st.form_submit_button("Run Analysis")

if not submitted:
    st.info("Type a name and click Run Analysis.")
    st.stop()

# ---------------------------
# Fetch and analyze
# ---------------------------
with st.spinner("Fetching search results..."):
    # Google CSE
    query_terms = [name, company, city, category]
    query = " ".join([t for t in query_terms if t])
    results = get_top_results(query, max_results=25)
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

    comp_prev = 0
    if company:
        matches = sum(1 for p in parsed if company.lower() in p["full_text"])
        comp_prev = matches / max(1, num)

    stats = {
        "num_websites": num,
        "unique_domains": len(seen),
        "avg_rating": avg_rating,
        "avg_sentiment": avg_sent,
        "most_recent_date": most_recent,
        "company_prevalence": comp_prev
    }

    grade = calculate_presence_score(stats)

    # Google Places reviews
    g_reviews = get_google_reviews(name, city)

    # Direct site reviews
    fallback_reviews = parse_site_reviews(name, city)

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

    # Radar chart
    radar_df = pd.DataFrame([grade['breakdown']])
    radar_df = radar_df.melt(var_name="Category", value_name="Score")
    fig = px.line_polar(radar_df, r='Score', theta='Category', line_close=True, title="Presence Breakdown", range_r=[0,100])
    st.plotly_chart(fig, use_container_width=True)

    # Tips
    st.subheader("Tips & Recommendations")
    if avg_rating and avg_rating < 3.5:
        st.write("• Average rating is low — consider addressing negative feedback and improving service quality.")
    if stats['num_websites'] < 10:
        st.write("• Limited online presence — consider publishing content, social media engagement, or press mentions.")
    if avg_sent < -0.2:
        st.write("• Negative sentiment detected — proactive PR or response to reviews may help.")
    if avg_rating and avg_rating > 4.0 and num < 10:
        st.write("• High ratings but few mentions — encourage satisfied clients/customers to leave reviews.")

with right:
    st.subheader("Tips for Further Exploration")
    st.write("• Try including city or profession to improve result accuracy.")
    st.write("• Refresh weekly to track trends.")

# Tabs
tab1, tab2, tab3, tab4 = st.tabs(["Quotes","Sources","Google Reviews","Other Sites Reviews"])

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

with tab3:
    st.subheader("Google Maps Reviews")
    if not g_reviews or not g_reviews.get("reviews"):
        st.info("No Google Reviews found for this person or brand.")
    else:
        avg_r = g_reviews.get("rating")
        total_r = g_reviews.get("user_ratings_total")
        maps_url = g_reviews.get("url")
        st.write(f"⭐ **Average Rating:** {avg_r} ({total_r} reviews)")
        if maps_url:
            st.markdown(f"[View on Google Maps]({maps_url})")
        revs = g_reviews.get("reviews", [])
        for r in revs[:5]:
            author = r.get("author_name", "Anonymous")
            text = r.get("text", "")
            relative_time = r.get("relative_time_description","")
            st.markdown(f"“{text}” — {author} ({relative_time})")
        if debug_mode:
            st.write("Raw Google Reviews API response:", g_reviews)

with tab4:
    st.subheader("Top Reviews from Other Sites")
    if not fallback_reviews:
        st.info("No reviews found from other sites.")
    else:
        for r in fallback_reviews:
            st.markdown(f"**{r['site']}** ⭐ {r['rating']}/5 — “{r['text']}” [source]({r['url']})")

# CSV Download
try:
    st.download_button("Download CSV", data=pd.DataFrame(parsed).to_csv(index=False),
                       file_name=f"presence_{name.replace(' ','_')}.csv")
except Exception:
    pass
