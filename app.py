# app.py
import re
import requests
from urllib.parse import urlparse, quote_plus
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
# Debug Mode checkbox (visible always)
# ---------------------------
debug_mode = st.checkbox("🛠 Enable Debug Mode", value=False)

if debug_mode:
    st.caption("Debug Mode ON — extra diagnostics will appear in the Debug expander.")

# Stop early only if keys are missing (but allow debug to show)
if not API_KEY or not CSE_ID:
    st.warning("Missing GOOGLE_API_KEY or GOOGLE_CSE_ID in Streamlit secrets. Add them to run live analysis.")
    if not debug_mode:
        st.info("Enable Debug Mode to see test information or add secrets to proceed.")
    # don't stop — allow debug to run below if checked

# ---------------------------
# Helper functions
# ---------------------------
def safe_request(url, timeout=8):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; PresenceMonitor/1.0)"}
    try:
        return requests.get(url, headers=headers, timeout=timeout)
    except Exception as e:
        if debug_mode:
            st.write(f"[safe_request] Error fetching {url}: {e}")
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
                v = float(g)
                return max(0.0, min(5.0, round(v,2)))
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
        if meta and meta.get("content"):
            text += meta.get("content") + " "
        # gather visible text
        for el in soup.find_all(["p","span","li","blockquote"]):
            text += el.get_text(separator=" ", strip=True) + " "
        row["full_text"] = text.lower()
        if not row["snippet"]:
            row["snippet"] = (text.strip()[:300] + "...") if text else ""
        row["rating"] = extract_rating_from_text(text[:8000])
    except Exception as e:
        if debug_mode:
            st.write(f"[extract_snippets_and_date] parse error for {url}: {e}")
    return row

def sentiment_score(text):
    if not text: 
        return 0.0
    s = analyzer.polarity_scores(text)
    return s["compound"]

def compute_grade(score):
    if score >= 90: return "A"
    if score >= 80: return "B"
    if score >= 70: return "C"
    if score >= 60: return "D"
    return "F"

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
        except:
            rec_score = 20
    comp_score = stats.get("company_prevalence",0)*100
    total = (w["sites"]*sites_score + w["rating"]*rating_score +
             w["sent"]*sent_score + w["rec"]*rec_score + w["comp"]*comp_score)
    total = max(0, min(100, total))
    return {"score": round(total,2), "grade": compute_grade(total),
            "breakdown": {
                "sites_score": round(sites_score,1),
                "rating_score": round(rating_score,1),
                "sentiment_score": round(sent_score,1),
                "recency_score": rec_score,
                "company_score": round(comp_score,1)
            }}

# ---------------------------
# Google CSE search (paginated)
# ---------------------------
def get_top_results(query, max_results=25):
    if not API_KEY or not CSE_ID:
        return []
    results = []
    try:
        for start in range(1, max_results+1, 10):
            url = f"https://www.googleapis.com/customsearch/v1?q={quote_plus(query)}&key={API_KEY}&cx={CSE_ID}&num={min(max_results,10)}&start={start}"
            r = requests.get(url, timeout=10)
            data = r.json()
            items = data.get("items", [])
            for item in items:
                results.append({
                    "title": item.get("title"),
                    "href": item.get("link"),
                    "body": item.get("snippet")
                })
            if len(results) >= max_results:
                break
    except Exception as e:
        if debug_mode:
            st.error(f"[get_top_results] Google CSE error: {e}")
    return results[:max_results]

# ---------------------------
# Google Places Reviews (cached)
# ---------------------------
@st.cache_data(ttl=3600)
def get_google_reviews(name, city=None):
    if not API_KEY:
        return None
    try:
        query = f"{name} {city}" if city else name
        search_url = f"https://maps.googleapis.com/maps/api/place/findplacefromtext/json?input={quote_plus(query)}&inputtype=textquery&fields=place_id,name,formatted_address&key={API_KEY}"
        r = requests.get(search_url, timeout=8)
        data = r.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return None
        place = candidates[0]
        place_id = place["place_id"]
        details_url = f"https://maps.googleapis.com/maps/api/place/details/json?place_id={place_id}&fields=name,rating,user_ratings_total,reviews,url&key={API_KEY}"
        r2 = requests.get(details_url, timeout=8)
        details = r2.json().get("result", {})
        return details
    except Exception as e:
        if debug_mode:
            st.write(f"[get_google_reviews] error: {e}")
        return None

# ---------------------------
# Site-specific parsing (top-5 per site) -- keep simple and robust
# ---------------------------
@st.cache_data(ttl=3600)
def parse_healthgrades(name, city=None, max_reviews=5):
    # healthgrades search results page may have dynamic JS — we will use Google CSE to find pages
    reviews = []
    if not API_KEY or not CSE_ID:
        return reviews
    query = f"{name} {city or ''} site:healthgrades.com"
    items = get_top_results(query, max_results=5)
    for it in items:
        url = it.get("href")
        r = safe_request(url)
        if not r:
            continue
        try:
            soup = BeautifulSoup(r.text, "html.parser")
            # heuristics — find review blocks and rating
            rev_blocks = soup.find_all("div", class_=re.compile("review|comment|rating"), limit=max_reviews)
            if not rev_blocks:
                # fallback: find elements that contain "patient review" text
                rev_blocks = soup.find_all(text=re.compile("review|patient", re.I))
            count = 0
            for rb in rev_blocks:
                text = rb.get_text(separator=" ", strip=True)
                rating = extract_rating_from_text(text)
                if text and count < max_reviews:
                    reviews.append({"site":"Healthgrades","text":text,"rating":rating,"url":url})
                    count += 1
        except Exception:
            continue
    return reviews

@st.cache_data(ttl=3600)
def parse_glassdoor(name, city=None, max_reviews=5):
    reviews = []
    if not API_KEY or not CSE_ID:
        return reviews
    # Glassdoor results by company: use CSE to find company pages with "Reviews"
    query = f"{name} {city or ''} site:glassdoor.com \"Reviews\""
    items = get_top_results(query, max_results=5)
    for it in items:
        url = it.get("href")
        r = safe_request(url)
        if not r:
            continue
        try:
            soup = BeautifulSoup(r.text, "html.parser")
            # Glassdoor's DOM is complex; find review snippets heuristically
            rev_blocks = soup.find_all("p", limit=max_reviews)
            count = 0
            for rb in rev_blocks:
                text = rb.get_text(separator=" ", strip=True)
                if len(text) < 30:
                    continue
                rating = extract_rating_from_text(text)
                reviews.append({"site":"Glassdoor","text":text,"rating":rating,"url":url})
                count += 1
                if count >= max_reviews:
                    break
        except Exception:
            continue
    return reviews

@st.cache_data(ttl=3600)
def parse_yelp(name, city=None, max_reviews=5):
    reviews = []
    if not API_KEY or not CSE_ID:
        return reviews
    query = f"{name} {city or ''} site:yelp.com"
    items = get_top_results(query, max_results=5)
    for it in items:
        url = it.get("href")
        r = safe_request(url)
        if not r:
            continue
        try:
            soup = BeautifulSoup(r.text, "html.parser")
            rev_blocks = soup.find_all("p", {"class": re.compile("comment|review|yelp")}, limit=max_reviews)
            if not rev_blocks:
                # fallback: any <p> with reasonable length
                rev_blocks = [p for p in soup.find_all("p") if len(p.get_text(strip=True))>50][:max_reviews]
            for rb in rev_blocks[:max_reviews]:
                text = rb.get_text(separator=" ", strip=True)
                rating = extract_rating_from_text(text)
                reviews.append({"site":"Yelp","text":text,"rating":rating,"url":url})
        except Exception:
            continue
    return reviews

@st.cache_data(ttl=3600)
def parse_ratemds(name, city=None, max_reviews=5):
    reviews = []
    if not API_KEY or not CSE_ID:
        return reviews
    query = f"{name} {city or ''} site:ratemds.com"
    items = get_top_results(query, max_results=5)
    for it in items:
        url = it.get("href")
        r = safe_request(url)
        if not r:
            continue
        try:
            soup = BeautifulSoup(r.text, "html.parser")
            rev_blocks = soup.find_all("div", class_=re.compile("review|comment"), limit=max_reviews)
            if not rev_blocks:
                rev_blocks = [p for p in soup.find_all("p") if len(p.get_text(strip=True))>30][:max_reviews]
            for rb in rev_blocks[:max_reviews]:
                text = rb.get_text(separator=" ", strip=True)
                rating = extract_rating_from_text(text)
                reviews.append({"site":"RateMDs","text":text,"rating":rating,"url":url})
        except Exception:
            continue
    return reviews

def gather_fallback_reviews(name, city=None):
    # aggregate from multiple sites with top-5 each
    reviews = []
    reviews.extend(parse_healthgrades(name, city))
    reviews.extend(parse_ratemds(name, city))
    reviews.extend(parse_glassdoor(name, city))
    reviews.extend(parse_yelp(name, city))
    return reviews[:50]  # cap

# ---------------------------
# Smart Tips engine
# ---------------------------
def generate_tips(stats, avg_rating, review_count, sentiment_scores, inputs):
    tips = []
    # unpack inputs
    name = inputs.get("name")
    city = inputs.get("city")
    company = inputs.get("company")
    profession = inputs.get("profession")

    # Search context tips
    if not city:
        tips.append(("Search Context", "Add a city to narrow results to local listings and reviews."))
    if not (company or profession):
        tips.append(("Search Context", "Adding a company or profession helps disambiguate common names."))

    # Reputation tips
    if avg_rating:
        if avg_rating >= 4.5:
            tips.append(("Reputation", "Excellent average rating — encourage more recent reviews to stay current."))
        elif 3.0 <= avg_rating < 4.5:
            tips.append(("Reputation", "Mixed/okay ratings — respond to constructive feedback and highlight 5-star testimonials."))
        else:
            tips.append(("Reputation", "Low average rating — prioritize response to reviews and corrective actions."))
    else:
        tips.append(("Reputation", "No average rating detected — consider requesting reviews from satisfied clients."))

    # Volume
    if review_count < 5:
        tips.append(("Volume", "Few reviews found — increase review requests from satisfied customers to build trust."))
    elif review_count > 50:
        tips.append(("Volume", "Solid review volume — maintain monitoring and respond to negative reviews promptly."))

    # Sentiment
    pos = sum(1 for s in sentiment_scores if s > 0.2)
    neg = sum(1 for s in sentiment_scores if s < -0.2)
    total = max(1, len(sentiment_scores))
    pos_ratio = pos / total
    if pos_ratio > 0.75:
        tips.append(("Sentiment", "Strongly positive sentiment — surface positive quotes on your site or marketing."))
    elif pos_ratio < 0.4:
        tips.append(("Sentiment", "Negative sentiment detected — analyze themes and address root causes."))

    # Presence/recency
    if stats.get("num_websites", 0) < 10:
        tips.append(("Presence", "Low web presence — create content, list on directories, and encourage social mentions."))
    if stats.get("most_recent_date"):
        try:
            days = (datetime.utcnow() - dateparser.parse(stats["most_recent_date"])).days
            if days > 365:
                tips.append(("Recency", "Most mentions are over a year old — publish fresh content and request current reviews."))
            elif days > 90:
                tips.append(("Recency", "Consider generating a few timely mentions to improve recency (blog, press, social)."))
        except:
            pass

    # Company-specific
    comp_prev = stats.get("company_prevalence", 0)
    if company and comp_prev < 0.1:
        tips.append(("Company", f"The company '{company}' is not prevalent on found pages — add company context or official pages."))

    # Deduplicate tips by message
    seen = set()
    filtered = []
    for cat, msg in tips:
        if msg not in seen:
            filtered.append((cat, msg))
            seen.add(msg)
    return filtered

# ---------------------------
# Form & inputs (now functional)
# ---------------------------
with st.form("search"):
    col1, col2, col3, col4 = st.columns([3,2,2,2])
    name = col1.text_input("Name or brand", placeholder="e.g. Dr. Jane Doe")
    company = col2.text_input("Company / Employer (optional)")
    city = col3.text_input("City / Location (optional)")
    profession = col4.text_input("Profession / Category (optional)")
    submitted = st.form_submit_button("Run Analysis")

if not submitted:
    st.info("Fill in fields and click Run Analysis.")
    st.stop()

# Build a strong query using all inputs
query_parts = [name]
if company:
    query_parts.append(company)
if profession:
    query_parts.append(profession)
if city:
    query_parts.append(city)
query = " ".join([p for p in query_parts if p]).strip()
if debug_mode:
    st.write("Query that will be used for searches:", query)

# ---------------------------
# Main analysis (wrapped in spinner)
# ---------------------------
with st.spinner("Running analysis — this may take a few seconds..."):
    # 1) Google CSE results
    cse_results = get_top_results(query, max_results=25)
    parsed = []
    domains = set()
    for r in cse_results:
        url = r.get("href")
        snippet = r.get("body") or ""
        info = extract_snippets_and_date(url, snippet)
        parsed.append(info)
        domains.add(info["domain"])

    num_sites = len(parsed)
    ratings = [p["rating"] for p in parsed if p.get("rating") is not None]
    avg_rating = round(sum(ratings)/len(ratings),2) if ratings else None

    # quotes & sentiment
    quotes = []
    for p in parsed:
        s = p.get("snippet", "") or ""
        sentences = re.split(r'(?<=[.!?])\s+', s)
        selected = next((sent for sent in sentences if name.split()[0].lower() in sent.lower()), s[:240])
        sentiment_val = sentiment_score(selected)
        quotes.append({**p, "quote": selected, "sentiment": sentiment_val})

    avg_sentiment = round(sum(q["sentiment"] for q in quotes)/len(quotes),3) if quotes else 0.0
    dates = [p["date"] for p in parsed if p.get("date")]
    most_recent = max(dates) if dates else None

    # optimized company prevalence: use cached full_text
    comp_prev = 0.0
    if company and parsed:
        matches = sum(1 for p in parsed if company.lower() in (p.get("full_text") or ""))
        comp_prev = matches / max(1, num_sites)

    stats = {
        "num_websites": num_sites,
        "unique_domains": len(domains),
        "avg_rating": avg_rating,
        "avg_sentiment": avg_sentiment,
        "most_recent_date": most_recent,
        "company_prevalence": comp_prev
    }

    grade = calculate_presence_score(stats)

    # Google Places reviews (primary)
    g_reviews = get_google_reviews(query, city)

    # fallback scraped reviews from specific sites
    fallback_reviews = gather_fallback_reviews(query, city)
    # normalize sentiment list for tips
    sentiment_values = [q["sentiment"] for q in quotes]

# ---------------------------
# Output: Overview & Radar Chart
# ---------------------------
left, right = st.columns([2,1])
with left:
    st.subheader(f"Overview — {name}")
    st.write(f"**Query used:** {query}")
    st.write(f"**Sites found:** {stats['num_websites']}")
    st.write(f"**Unique domains:** {stats['unique_domains']}")
    st.write(f"**Average rating (detected):** {stats['avg_rating'] or 'N/A'} / 5")
    st.write(f"**Avg sentiment (compound):** {stats['avg_sentiment']}")
    st.markdown(f"### Overall Grade: {grade['grade']}  ({grade['score']} / 100)")

    # radar chart
    bd = grade["breakdown"]
    radar_df = pd.DataFrame({
        "Category": ["Sites","Rating","Sentiment","Recency","Company"],
        "Score": [bd["sites_score"], bd["rating_score"], bd["sentiment_score"], bd["recency_score"], bd["company_score"]]
    })
    fig = px.line_polar(radar_df, r='Score', theta='Category', line_close=True, title="Presence Breakdown", range_r=[0,100])
    st.plotly_chart(fig, use_container_width=True)

    # Tips expander (collapsible)
    tips = generate_tips(stats, stats.get("avg_rating"), len(fallback_reviews) + (len(g_reviews.get("reviews",[])) if g_reviews else 0) if True else 0, sentiment_values, {"name":name,"company":company,"city":city,"profession":profession})
    with st.expander("💡 Tips & Recommendations", expanded=False):
        if not tips:
            st.write("No specific tips generated.")
        else:
            for cat, msg in tips:
                if cat == "Reputation":
                    st.error(f"**{cat}:** {msg}")
                elif cat in ("Sentiment", "Presence", "Volume"):
                    st.warning(f"**{cat}:** {msg}")
                else:
                    st.info(f"**{cat}:** {msg}")

with right:
    st.subheader("Quick Actions")
    st.write("• Try adding a city or profession to narrow searches.")
    st.write("• For continuous monitoring, run periodically and store historical data.")
    if debug_mode:
        st.write("Debug mode enabled — raw data available in Debug section below.")

# ---------------------------
# Tabs: Quotes / Sources / Google Reviews / Other Sites Reviews
# ---------------------------
tab1, tab2, tab3, tab4 = st.tabs(["Quotes","Sources","Google Reviews","Other Sites Reviews"])

with tab1:
    st.subheader("Positive & Negative Quotes (extracted)")
    pos = sorted([q for q in quotes if q["sentiment"] >= 0.2], key=lambda x:-x["sentiment"])
    neg = sorted([q for q in quotes if q["sentiment"] <= -0.2], key=lambda x:x["sentiment"])
    neutral = [q for q in quotes if -0.2 < q["sentiment"] < 0.2]
    if pos:
        st.markdown("**Positive**")
        for q in pos[:10]:
            st.markdown(f"**{q.get('title') or q['domain']}** — “{q['quote']}”  [source]({q['url']}) — sentiment {q['sentiment']}")
    if neg:
        st.markdown("**Negative**")
        for q in neg[:10]:
            st.markdown(f"**{q.get('title') or q['domain']}** — “{q['quote']}”  [source]({q['url']}) — sentiment {q['sentiment']}")
    if neutral:
        st.markdown("**Neutral / unclear**")
        for q in neutral[:8]:
            st.markdown(f"**{q.get('title') or q['domain']}** — “{q['quote']}”  [source]({q['url']}) — sentiment {q['sentiment']}")

with tab2:
    st.subheader("All Sources (top results)")
    if not parsed:
        st.info("No sources found.")
    else:
        for p in parsed:
            rating_txt = f" — rating: {p.get('rating')}/5" if p.get('rating') else ""
            date_txt = f" — date: {p.get('date')[:10]}" if p.get('date') else ""
            st.markdown(f"- [{p.get('title') or p['url']}]({p['url']}) <span style='color:#666'>{p['domain']}{rating_txt}{date_txt}</span>", unsafe_allow_html=True)

with tab3:
    st.subheader("Google Maps Reviews")
    if not g_reviews:
        st.info("No Google Reviews found for this query/place.")
    else:
        avg_r = g_reviews.get("rating")
        total_r = g_reviews.get("user_ratings_total")
        maps_url = g_reviews.get("url")
        st.write(f"⭐ **Average Rating:** {avg_r} ({total_r} reviews)")
        if maps_url:
            st.markdown(f"[View on Google Maps]({maps_url})")
        revs = g_reviews.get("reviews", []) or []
        for r in revs[:5]:
            author = r.get("author_name", "Anonymous")
            text = r.get("text", "")
            relative_time = r.get("relative_time_description","")
            st.markdown(f"“{text}” — {author} ({relative_time})")
        if debug_mode:
            st.subheader("Raw Google Places response")
            st.write(g_reviews)

with tab4:
    st.subheader("Top Reviews from Other Sites (fallback)")
    if not fallback_reviews:
        st.info("No reviews scraped from Healthgrades/Glassdoor/Yelp/RateMDs for this query.")
    else:
        for r in fallback_reviews[:50]:
            site = r.get("site","Other")
            rating = r.get("rating") or "N/A"
            text = r.get("text","").strip()
            url = r.get("url")
            st.markdown(f"**{site}** — ⭐ {rating} — “{text[:300]}” [source]({url})")

# ---------------------------
# Debug expander (only when debug_mode True)
# ---------------------------
if debug_mode:
    with st.expander("🧩 Debug Details (showing internals)", expanded=True):
        st.write("**Inputs**:", {"name":name,"company":company,"city":city,"profession":profession})
        st.write("**Query**:", query)
        st.write("**Sites parsed**:", num_sites)
        st.write("**Avg rating detected**:", avg_rating)
        st.write("**Avg sentiment**:", avg_sentiment)
        st.write("**Company prevalence**:", comp_prev)
        st.write("**Google Reviews found?**", bool(g_reviews))
        st.write("**Fallback reviews count**:", len(fallback_reviews))
        # sample raw parsed
        st.write("Sample parsed result (first):", parsed[0] if parsed else "No parsed pages")
        st.write("Sample quotes (first 5):")
        for q in quotes[:5]:
            st.write(q)

# ---------------------------
# CSV Download
# ---------------------------
try:
    st.download_button("Download raw results (.csv)", data=pd.DataFrame(parsed).to_csv(index=False), file_name=f"presence_{name.replace(' ','_')}.csv", mime="text/csv")
except Exception:
    pass
