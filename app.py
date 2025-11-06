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
# Page config
# ---------------------------
st.set_page_config(page_title="Online Presence Monitor", layout="wide")
st.title("🔎 Online Presence Monitor")
st.write("Enter a search query (name, profession, city). Example: 'Dr. Jane Smith cardiologist Chicago'")

# ---------------------------
# Globals & secrets
# ---------------------------
analyzer = SentimentIntensityAnalyzer()
API_KEY = st.secrets.get("GOOGLE_API_KEY")
CSE_ID = st.secrets.get("GOOGLE_CSE_ID")

# ---------------------------
# Debug toggle (only shows debug outputs when True)
# ---------------------------
debug_mode = st.checkbox("🛠 Enable Debug Mode", value=False)
if debug_mode:
    st.caption("Debug mode enabled — extra diagnostics will appear in the Debug expander and inline logs.")

# ---------------------------
# Utilities
# ---------------------------
def safe_request(url, timeout=8):
    """HTTP GET with a UA and basic error handling."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; PresenceMonitor/1.0)"}
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        return r
    except Exception as e:
        if debug_mode:
            st.write(f"[safe_request] Error for {url}: {e}")
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
                return max(0.0, min(5.0, float(g)))
            except:
                continue
    return None

def sentiment_score(text):
    if not text:
        return 0.0
    s = analyzer.polarity_scores(text)
    return s["compound"]

# ---------------------------
# Simple query parser (heuristic)
# ---------------------------
def parse_query(raw_query):
    """
    Heuristic:
    - If user includes commas: treat last comma-separated part as city.
    - Otherwise, if there are tokens and last token is capitalized or looks like a place, treat as city.
    - If tokens include profession keywords (doctor, dr, cardiologist, lawyer...), attempt to extract profession.
    This is heuristic and not perfect — debug mode will show results for tuning.
    """
    name = raw_query
    profession = ""
    city = ""

    if not raw_query or not raw_query.strip():
        return {"name": "", "profession": "", "city": ""}

    q = raw_query.strip()
    # split by comma first
    parts = [p.strip() for p in q.split(",") if p.strip()]
    if len(parts) >= 2:
        # last part most likely city/location
        city = parts[-1]
        remainder = " ".join(parts[:-1])
    else:
        remainder = q

    # attempt to find profession keywords from a small list
    prof_keywords = ["doctor","dr","physician","lawyer","attorney","clinic","dentist",
                     "therapist","nurse","restaurant","bookstore","hotel","realtor",
                     "cardiologist","pediatrician","surgeon","barber","salon","store","shop"]
    tokens = remainder.split()
    prof_tokens = [t for t in tokens if re.sub(r'[^\w]', '', t.lower()) in prof_keywords]
    if prof_tokens:
        # take first found token as profession (heuristic)
        profession = " ".join(prof_tokens)
        # name is remainder without profession tokens
        name_tokens = [t for t in tokens if t not in prof_tokens]
        name = " ".join(name_tokens)
    else:
        # try common 'Dr.' style
        m = re.match(r'^(Dr\.?|Doctor)\b', remainder, re.I)
        if m:
            # leave name as-is, set profession to doctor
            profession = "doctor"
            name = remainder
        else:
            name = remainder

    # final cleanup
    name = name.strip()
    profession = profession.strip()
    city = city.strip()
    if debug_mode:
        st.write({"raw_query": raw_query, "name": name, "profession": profession, "city": city})
    return {"name": name, "profession": profession, "city": city}

# ---------------------------
# Google Custom Search (CSE)
# ---------------------------
@st.cache_data(ttl=3600)
def get_top_results(query, max_results=25):
    """Paginated CSE fetch (returns dicts with title/href/snippet)."""
    results = []
    if not API_KEY or not CSE_ID:
        if debug_mode:
            st.write("[get_top_results] Missing API_KEY or CSE_ID")
        return results
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
            st.error(f"[get_top_results] error: {e}")
    return results[:max_results]

# ---------------------------
# Google Places (Maps) Reviews
# ---------------------------
@st.cache_data(ttl=3600)
def get_google_places_details(query):
    """Use findplacefromtext then place details to get rating, reviews, url."""
    if not API_KEY:
        if debug_mode:
            st.write("[get_google_places_details] missing API_KEY")
        return None
    try:
        find_url = f"https://maps.googleapis.com/maps/api/place/findplacefromtext/json?input={quote_plus(query)}&inputtype=textquery&fields=place_id,name,formatted_address&key={API_KEY}"
        r = requests.get(find_url, timeout=8)
        data = r.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return None
        place = candidates[0]
        place_id = place.get("place_id")
        details_url = f"https://maps.googleapis.com/maps/api/place/details/json?place_id={place_id}&fields=name,rating,user_ratings_total,reviews,url&key={API_KEY}"
        r2 = requests.get(details_url, timeout=8)
        details = r2.json().get("result", {})
        return details
    except Exception as e:
        if debug_mode:
            st.write(f"[get_google_places_details] error: {e}")
        return None

# ---------------------------
# Yelp parser (dedicated)
# ---------------------------
@st.cache_data(ttl=3600)
def parse_yelp(query, city=None, max_reviews=5):
    """
    Use CSE to locate Yelp business pages (prefer /biz/ links) and parse review blocks.
    Returns a list of dicts: {site, rating, text, url}
    """
    reviews = []
    # Compose a query targeted at Yelp
    cse_query = f"{query} {city or ''} site:yelp.com"
    if debug_mode:
        st.write(f"[parse_yelp] cse_query: {cse_query}")
    items = get_top_results(cse_query, max_results=8)
    visited = set()
    for it in items:
        url = it.get("href")
        if not url or url in visited:
            continue
        visited.add(url)
        # ensure it's a biz page
        if "/biz/" not in url:
            # sometimes CSE returns search or map links; skip them
            continue
        r = safe_request(url)
        if not r:
            continue
        try:
            soup = BeautifulSoup(r.text, "html.parser")
            # Yelp review blocks: try a few selectors (Yelp may obfuscate classes)
            review_blocks = soup.select('div[data-review-id]') or soup.select('div[class*="review__"]') or soup.select('li[class*="review"]')
            if debug_mode:
                st.write(f"[parse_yelp] fetched {url} — blocks found: {len(review_blocks)}")
            count = 0
            for rb in review_blocks:
                if count >= max_reviews:
                    break
                # text
                text_el = rb.find("p")
                text = text_el.get_text(separator=" ", strip=True) if text_el else ""
                if not text or len(text) < 10:
                    # fallback: find span with review text
                    ss = rb.find_all("span")
                    for s in ss:
                        t = s.get_text(strip=True)
                        if len(t) > 40:
                            text = t
                            break
                if not text:
                    continue
                # rating: Yelp often stores rating aria-label on a div role=img
                rating_val = None
                rating_el = rb.find("div", {"role": "img"})
                if rating_el and rating_el.get("aria-label"):
                    m = re.search(r'([0-5](?:\.\d)?) star rating', rating_el["aria-label"])
                    if m:
                        try:
                            rating_val = float(m.group(1))
                        except:
                            rating_val = None
                # date (try)
                date = None
                try:
                    # some review blocks have time elements
                    time_el = rb.find("time")
                    if time_el and time_el.get("datetime"):
                        date = time_el.get("datetime")
                except:
                    date = None
                reviews.append({"site":"Yelp","rating":rating_val,"text":text,"url":url,"date":date})
                count += 1
            if debug_mode and count:
                st.write(f"[parse_yelp] sample: {reviews[-1]['text'][:180]}")
        except Exception as e:
            if debug_mode:
                st.write(f"[parse_yelp] parse error for {url}: {e}")
            continue
    return reviews[:max_reviews]

# ---------------------------
# Placeholders for site parsers (expand later)
# ---------------------------
@st.cache_data(ttl=3600)
def parse_healthgrades(query, city=None, max_reviews=5):
    # Use CSE to find likely Healthgrades pages and parse similarly to Yelp
    reviews = []
    cse_query = f"{query} {city or ''} site:healthgrades.com"
    items = get_top_results(cse_query, max_results=5)
    for it in items:
        url = it.get("href")
        r = safe_request(url)
        if not r:
            continue
        try:
            soup = BeautifulSoup(r.text, "html.parser")
            # try heuristics for review blocks
            rev_blocks = soup.find_all("div", class_=re.compile("review|patient|comment"), limit=max_reviews)
            for rb in rev_blocks:
                text = rb.get_text(separator=" ", strip=True)
                rating = extract_rating_from_text(text)
                reviews.append({"site":"Healthgrades","rating":rating,"text":text,"url":url})
        except Exception:
            continue
    return reviews[:max_reviews]

@st.cache_data(ttl=3600)
def parse_glassdoor(query, city=None, max_reviews=5):
    reviews = []
    cse_query = f"{query} {city or ''} site:glassdoor.com \"Reviews\""
    items = get_top_results(cse_query, max_results=5)
    for it in items:
        url = it.get("href")
        r = safe_request(url)
        if not r:
            continue
        try:
            soup = BeautifulSoup(r.text, "html.parser")
            # heuristics: Glassdoor review paragraphs often inside <p>
            rev_blocks = soup.find_all("p", limit=max_reviews)
            for rb in rev_blocks:
                text = rb.get_text(separator=" ", strip=True)
                if len(text) < 30:
                    continue
                rating = extract_rating_from_text(text)
                reviews.append({"site":"Glassdoor","rating":rating,"text":text,"url":url})
        except Exception:
            continue
    return reviews[:max_reviews]

# ---------------------------
# Aggregated fallback reviews
# ---------------------------
def gather_fallback_reviews(query, city=None):
    # call each site parser
    r = []
    r.extend(parse_yelp(query, city))
    r.extend(parse_healthgrades(query, city))
    r.extend(parse_glassdoor(query, city))
    return r

# ---------------------------
# Smart Tips engine
# ---------------------------
def generate_tips(stats, avg_rating, review_count, sentiment_scores, inputs):
    tips = []
    name = inputs.get("name")
    city = inputs.get("city")
    profession = inputs.get("profession")
    company = inputs.get("company")

    # Context tips
    if not city:
        tips.append(("Search Context", "Add a city (or include it in your search query) to narrow results locally."))
    if not profession and not company:
        tips.append(("Search Context", "Include a profession or company to better disambiguate common names."))

    # Rating tips
    if avg_rating:
        if avg_rating >= 4.5:
            tips.append(("Reputation", "Strong average rating — encourage recent reviewers to stay current."))
        elif avg_rating >= 3.0:
            tips.append(("Reputation", "Average rating is mixed — respond to critical feedback and highlight positive testimonials."))
        else:
            tips.append(("Reputation", "Low average rating — investigate recurring complaints and respond publicly where appropriate."))
    else:
        tips.append(("Reputation", "No average rating detected — encourage satisfied clients to leave reviews on key platforms."))

    # Volume
    if review_count < 5:
        tips.append(("Volume", "Few reviews found — implement a process to request reviews from happy customers."))
    elif review_count > 50:
        tips.append(("Volume", "Healthy review volume — maintain monitoring and respond to negative feedback promptly."))

    # Sentiment composition
    pos = sum(1 for s in sentiment_scores if s > 0.2)
    neg = sum(1 for s in sentiment_scores if s < -0.2)
    total = max(1, len(sentiment_scores))
    pos_ratio = pos/total
    if pos_ratio > 0.75:
        tips.append(("Sentiment", "Predominantly positive sentiment — promote great quotes in marketing materials."))
    elif pos_ratio < 0.4:
        tips.append(("Sentiment", "Negative sentiment present — prioritize resolving critical issues and public responses."))

    # Presence
    if stats.get("num_websites",0) < 8:
        tips.append(("Presence", "Limited online footprint — consider directory listings, local citations, and social posts."))

    # Recency
    most_recent = stats.get("most_recent_date")
    if most_recent:
        try:
            days = (datetime.utcnow() - dateparser.parse(most_recent)).days
            if days > 365:
                tips.append(("Recency", "Most mentions are over a year old — fresh content and recent reviews help SEO."))
            elif days > 90:
                tips.append(("Recency", "Consider generating new mentions (press, blog, social)."))
        except:
            pass

    # company prevalence
    if company and stats.get("company_prevalence",0) < 0.1:
        tips.append(("Company", f"The company '{company}' isn't common in found results — add your official website and directory listings."))

    # dedupe
    seen = set()
    filtered = []
    for cat, msg in tips:
        if msg not in seen:
            filtered.append((cat, msg))
            seen.add(msg)
    return filtered

# ---------------------------
# Presence scoring & radar
# ---------------------------
def calculate_presence_score(stats):
    # reuse earlier breakdown logic but safe
    try:
        sites_score = min(stats.get("num_websites",0),50) / 50 * 100
        rating_score = (stats.get("avg_rating") or 0) / 5 * 100
        sent_score = ((stats.get("avg_sentiment",0) + 1) / 2) * 100
        # recency
        rec_score = 0
        if stats.get("most_recent_date"):
            days = (datetime.utcnow() - dateparser.parse(stats["most_recent_date"])).days
            rec_score = 100 if days <= 7 else 80 if days <= 30 else 50 if days <= 90 else 30 if days <= 365 else 10
        comp_score = stats.get("company_prevalence",0) * 100
        w = {"sites":0.35,"rating":0.30,"sent":0.15,"rec":0.10,"comp":0.10}
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
    except Exception as e:
        if debug_mode:
            st.write(f"[calculate_presence_score] error: {e}")
        return {"score":0,"grade":"F","breakdown":{"Sites":0,"Rating":0,"Sentiment":0,"Recency":0,"Company":0}}

# ---------------------------
# UI: single smart query input
# ---------------------------
with st.form("search"):
    query_raw = st.text_input("Search Query", placeholder="e.g. Dr. Jane Smith cardiologist Chicago", label_visibility="visible")
    submitted = st.form_submit_button("Run Analysis")

if not submitted:
    st.info("Enter a search query and click Run Analysis. Use Debug Mode to see internals.")
    st.stop()

# parse query into pieces for compatibility
parts = parse_query(query_raw)
name = parts.get("name")
profession = parts.get("profession")
city = parts.get("city")
company = ""  # left available if you want in future

# Build canonical query used for CSE and site parsers
canonical_query = " ".join([p for p in [name, profession, city] if p]).strip()
if debug_mode:
    st.write(f"Canonical query: {canonical_query}")

# ---------------------------
# Main analysis pipeline
# ---------------------------
with st.spinner("Running analysis — fetching search results and reviews..."):
    # 1) Google CSE search
    cse_results = get_top_results(canonical_query, max_results=25) if (API_KEY and CSE_ID) else []
    parsed = []
    domains = set()
    for item in cse_results:
        url = item.get("href")
        snippet = item.get("body")
        info = {"url": url, "domain": urlparse(url).netloc, "title": item.get("title"), "snippet": snippet, "rating": None, "date": None, "full_text": ""}
        # fetch text / rating heuristics (non-blocking best-effort)
        try:
            r = safe_request(url)
            if r:
                soup = BeautifulSoup(r.text, "html.parser")
                info["title"] = info["title"] or (soup.title.string.strip() if soup.title and soup.title.string else "")
                meta = soup.find("meta", {"name":"description"}) or soup.find("meta", {"property":"og:description"})
                text = ""
                if meta and meta.get("content"):
                    text += meta.get("content") + " "
                for el in soup.find_all(["p","span","li","blockquote"]):
                    text += el.get_text(separator=" ", strip=True) + " "
                info["full_text"] = text.lower()
                info["snippet"] = info["snippet"] or (text.strip()[:300] + "...") if text else info["snippet"]
                info["rating"] = extract_rating_from_text(text[:8000])
            parsed.append(info)
            domains.add(info["domain"])
        except Exception as e:
            if debug_mode:
                st.write(f"[main parse] error parsing {url}: {e}")
            parsed.append(info)
            domains.add(info["domain"])

    num_sites = len(parsed)
    ratings = [p["rating"] for p in parsed if p.get("rating") is not None]
    avg_rating = round(sum(ratings)/len(ratings),2) if ratings else None

    # quotes & sentiment
    quotes = []
    for p in parsed:
        s = p.get("snippet") or ""
        sentences = re.split(r'(?<=[.!?])\s+', s)
        selected = next((snt for snt in sentences if name.split()[0].lower() in snt.lower()), s[:240])
        sent_val = sentiment_score(selected)
        quotes.append({**p, "quote": selected, "sentiment": sent_val})

    avg_sentiment = round(sum(q["sentiment"] for q in quotes)/len(quotes),3) if quotes else 0.0
    dates = [p.get("date") for p in parsed if p.get("date")]
    most_recent = max(dates) if dates else None

    # company prevalence
    comp_prev = 0.0
    if company and parsed:
        matches = sum(1 for p in parsed if company.lower() in (p.get("full_text") or ""))
        comp_prev = matches / max(1, num_sites)

    stats = {"num_websites": num_sites, "unique_domains": len(domains), "avg_rating": avg_rating,
             "avg_sentiment": avg_sentiment, "most_recent_date": most_recent, "company_prevalence": comp_prev}

    grade = calculate_presence_score(stats)

    # Google Places reviews (maps)
    places = get_google_places_details(canonical_query) if API_KEY else None

    # fallback scraped reviews
    fallback_reviews = gather_fallback_reviews(canonical_query, city)

# ---------------------------
# Output display
# ---------------------------
left, right = st.columns([2,1])
with left:
    st.subheader(f"Overview — {name or query_raw}")
    st.write(f"**Canonical query:** {canonical_query}")
    st.write(f"**Sites found:** {stats['num_websites']}")
    st.write(f"**Unique domains:** {stats['unique_domains']}")
    st.write(f"**Average rating (detected):** {stats['avg_rating'] or 'N/A'} / 5")
    st.write(f"**Average sentiment (compound):** {stats['avg_sentiment']}")
    st.markdown(f"### Overall Grade: {grade['grade']}  ({grade['score']} / 100)")

    # radar chart (theme-aware)
    bd = grade["breakdown"]
    radar_df = pd.DataFrame({
        "Category": list(bd.keys()),
        "Score": list(bd.values())
    })
    # detect streamlit theme base
    try:
        theme_base = st.get_option("theme.base")
    except:
        theme_base = "light"
    template = "plotly_dark" if theme_base == "dark" else "plotly"
    fig = px.line_polar(radar_df, r='Score', theta='Category', line_close=True, template=template,
                        title="Presence Breakdown", range_r=[0,100])
    fig.update_traces(fill='toself')
    st.plotly_chart(fig, use_container_width=True)

    # Tips (collapsible)
    tips = generate_tips(stats, stats.get("avg_rating"), len(fallback_reviews) + (len(places.get("reviews",[])) if places else 0), [q["sentiment"] for q in quotes], {"name":name,"company":company,"city":city,"profession":profession})
    with st.expander("💡 Tips & Recommendations", expanded=False):
        if not tips:
            st.write("No specific tips generated.")
        else:
            for cat, msg in tips:
                if cat == "Reputation":
                    st.error(f"**{cat}:** {msg}")
                elif cat in ("Sentiment","Presence","Volume","Recency"):
                    st.warning(f"**{cat}:** {msg}")
                else:
                    st.info(f"**{cat}:** {msg}")

with right:
    st.subheader("Quick Actions")
    st.write("• Try adding more context (profession, city) to the query for better precision.")
    st.write("• Enable Debug Mode to inspect raw API responses and parsed pages.")

# Tabs
tab1, tab2, tab3, tab4 = st.tabs(["Quotes","Sources","Google Reviews","Other Sites Reviews"])

with tab1:
    st.subheader("Extracted Quotes (from top results)")
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
    st.subheader("All Sources (Top Google results)")
    if not parsed:
        st.info("No sources found for this query.")
    else:
        for p in parsed:
            rating_txt = f" — rating: {p.get('rating')}/5" if p.get('rating') else ""
            st.markdown(f"- [{p.get('title') or p['url']}]({p['url']}) ({p['domain']}{rating_txt})")

with tab3:
    st.subheader("Google Maps / Places Reviews")
    if not places:
        st.info("No Google Maps listing or no reviews found via Places API for this query.")
    else:
        avg_r = places.get("rating")
        total_r = places.get("user_ratings_total")
        maps_url = places.get("url")
        st.write(f"⭐ **Average Rating (Google Maps):** {avg_r} ({total_r} reviews)")
        if maps_url:
            st.markdown(f"[View on Google Maps]({maps_url})")
        revs = places.get("reviews", []) or []
        for r in revs[:5]:
            author = r.get("author_name", "Anonymous")
            text = r.get("text", "")
            relative_time = r.get("relative_time_description","")
            st.markdown(f"“{text}” — {author} ({relative_time})")
        if debug_mode:
            st.subheader("Raw Google Places response")
            st.write(places)

with tab4:
    st.subheader("Top Reviews from Other Sites (fallback)")
    fallback = fallback_reviews or []
    if not fallback:
        st.info("No reviews scraped from fallback sites for this query.")
    else:
        for r in fallback[:50]:
            site = r.get("site","Other")
            rating = r.get("rating") or "N/A"
            text = r.get("text","").strip()
            url = r.get("url")
            st.markdown(f"**{site}** — ⭐ {rating} — “{text[:300]}” [source]({url})")

# Debug expander with internals
if debug_mode:
    with st.expander("🧩 Debug Details (internals)", expanded=True):
        st.write("Inputs:", {"raw_query": query_raw, "name":name, "profession":profession, "city":city})
        st.write("Canonical Query:", canonical_query)
        st.write("CSE results count:", len(cse_results))
        st.write("Parsed pages sample (first):", parsed[0] if parsed else "No parsed pages")
        st.write("Places API result (if any):", places)
        st.write("Fallback reviews sample:", fallback[:5])
        st.write("Quotes sample:", quotes[:5])

# CSV download
try:
    df = pd.DataFrame(parsed)
    st.download_button("Download parsed sources CSV", data=df.to_csv(index=False), file_name=f"presence_{name.replace(' ','_')}.csv", mime="text/csv")
except Exception:
    pass
