# app.py
import streamlit as st
import requests, json, re
from bs4 import BeautifulSoup
import plotly.express as px
from urllib.parse import quote_plus

st.set_page_config(page_title="Online Presence Monitor", layout="wide")
debug_mode = st.sidebar.checkbox("Enable Debug Mode")

# -----------------------------
# CONFIG: SINGLE GOOGLE API KEY
# -----------------------------
API_KEY = st.secrets.get("GOOGLE_API_KEY")
CSE_ID = st.secrets.get("CSE_ID")

MAX_REVIEWS = 5

# -----------------------------
# UTILITIES
# -----------------------------
def get_top_results(query, max_results=10):
    urls = []
    if not API_KEY or not CSE_ID:
        if debug_mode:
            st.write("[get_top_results] Missing API_KEY/CSE_ID.")
        return urls
    search_url = f"https://www.googleapis.com/customsearch/v1?q={quote_plus(query)}&cx={CSE_ID}&key={API_KEY}&num={max_results}"
    try:
        r = requests.get(search_url)
        data = r.json()
        for item in data.get("items", []):
            urls.append({
                "title": item.get("title"),
                "href": item.get("link"),
                "snippet": item.get("snippet")
            })
    except Exception as e:
        if debug_mode:
            st.write(f"[get_top_results] Error: {e}")
    return urls

# -----------------------------
# YELP REVIEWS (JSON via CSE)
# -----------------------------
@st.cache_data(ttl=3600)
def fetch_yelp_reviews_json(query, max_reviews=MAX_REVIEWS):
    out = []
    if not API_KEY or not CSE_ID:
        return out

    cse_q = f"{query} site:yelp.com"
    if debug_mode:
        st.write(f"[fetch_yelp_reviews_json] CSE query: {cse_q}")
    candidates = get_top_results(cse_q, max_results=8)

    yelp_biz_url = None
    for c in candidates:
        href = c.get("href","")
        if "/biz/" in href:
            yelp_biz_url = href.split("?")[0]
            break
    if not yelp_biz_url:
        return out

    if debug_mode:
        st.write(f"[fetch_yelp_reviews_json] Found Yelp biz URL: {yelp_biz_url}")

    alias = yelp_biz_url.rstrip("/").split("/biz/")[-1].split("/")[0]
    feed_url = f"https://www.yelp.com/biz/{alias}/review_feed?start=0&sort_by=date_desc"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; PresenceMonitor/1.0)",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": yelp_biz_url
    }
    try:
        r = requests.get(feed_url, headers=headers, timeout=10)
        if r.status_code != 200:
            if debug_mode:
                st.write(f"[fetch_yelp_reviews_json] Yelp feed returned status {r.status_code}")
            return out
        try:
            data = r.json()
        except Exception:
            m = re.search(r"(\{.*\"reviews\":\s*\[.*\]\s*\})", r.text, re.S)
            if m:
                data = json.loads(m.group(1))
            else:
                data = {}

        reviews = data.get("reviews") or data.get("review_list") or []
        if debug_mode:
            st.write(f"[fetch_yelp_reviews_json] Reviews count: {len(reviews)}")

        for idx, rv in enumerate(reviews):
            if idx >= max_reviews:
                break
            text = rv.get("comment") or rv.get("excerpt") or rv.get("text") or ""
            rating = rv.get("rating") or rv.get("rating_score") or None
            author = rv.get("user", {}).get("markup_display_name") or rv.get("user", {}).get("display_name")
            time = rv.get("localizedDate") or rv.get("time") or rv.get("published") or rv.get("date")
            out.append({
                "site": "Yelp",
                "text": text.strip(),
                "rating": float(rating) if rating else None,
                "url": yelp_biz_url,
                "author": author,
                "time": time
            })
    except Exception as e:
        if debug_mode:
            st.write(f"[fetch_yelp_reviews_json] Error fetching Yelp JSON: {e}")

    return out[:max_reviews]

# -----------------------------
# GOOGLE PLACES REVIEWS
# -----------------------------
@st.cache_data(ttl=3600)
def fetch_google_places_reviews(query):
    if not API_KEY:
        return []
    search_url = f"https://maps.googleapis.com/maps/api/place/findplacefromtext/json?input={quote_plus(query)}&inputtype=textquery&fields=place_id,name,rating,user_ratings_total&key={API_KEY}"
    try:
        r = requests.get(search_url)
        data = r.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return []
        place_id = candidates[0]["place_id"]
        details_url = f"https://maps.googleapis.com/maps/api/place/details/json?place_id={place_id}&fields=name,rating,user_ratings_total,reviews,url&key={API_KEY}"
        r2 = requests.get(details_url)
        details = r2.json().get("result", {})
        reviews = details.get("reviews", [])[:MAX_REVIEWS]
        out = []
        for rv in reviews:
            out.append({
                "site": "Google",
                "text": rv.get("text",""),
                "rating": float(rv.get("rating",0)),
                "url": details.get("url"),
                "author": rv.get("author_name"),
                "time": rv.get("relative_time_description")
            })
        return out
    except Exception as e:
        if debug_mode:
            st.write(f"[fetch_google_places_reviews] Error: {e}")
        return []

# -----------------------------
# HEALTHGRADES / GLASSDOOR (HTML)
# -----------------------------
@st.cache_data(ttl=3600)
def fetch_healthgrades_reviews(query):
    out = []
    search_url = f"https://www.healthgrades.com/search?query={quote_plus(query)}"
    try:
        r = requests.get(search_url, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        review_blocks = soup.select(".review-card")[:MAX_REVIEWS]
        for blk in review_blocks:
            text = blk.select_one(".review-text")
            rating = blk.select_one(".rating-stars")
            author = blk.select_one(".reviewer-name")
            out.append({
                "site": "Healthgrades",
                "text": text.get_text(strip=True) if text else "",
                "rating": float(rating.get("data-rating")) if rating else None,
                "url": search_url,
                "author": author.get_text(strip=True) if author else "",
                "time": ""
            })
    except:
        pass
    return out

@st.cache_data(ttl=3600)
def fetch_glassdoor_reviews(query):
    out = []
    search_url = f"https://www.glassdoor.com/Reviews/{quote_plus(query)}-reviews-SRCH_KE0,50.htm"
    try:
        r = requests.get(search_url, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        review_blocks = soup.select(".empReview")[:MAX_REVIEWS]
        for blk in review_blocks:
            text = blk.select_one(".mt-0")
            rating = blk.select_one(".gdStars")
            out.append({
                "site": "Glassdoor",
                "text": text.get_text(strip=True) if text else "",
                "rating": float(rating.get("title").split()[0]) if rating else None,
                "url": search_url,
                "author": "",
                "time": ""
            })
    except:
        pass
    return out

# -----------------------------
# RADAR CHART
# -----------------------------
def plot_radar_chart(data_dict):
    df = {"Metric": list(data_dict.keys()), "Score": list(data_dict.values())}
    theme = "plotly_dark" if st.get_option("theme.base")=="dark" else "plotly_white"
    fig = px.line_polar(df, r="Score", theta="Metric", line_close=True)
    fig.update_traces(fill="toself", line_color="#636EFA")
    fig.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0,10])), showlegend=False, template=theme)
    return fig

# -----------------------------
# MAIN APP
# -----------------------------
st.title("🕵️ Online Presence Monitor")

query_input = st.text_input(
    "Enter name, profession/category, and/or city (e.g., 'Dr. Jane Smith cardiologist Chicago')",
    ""
)

if query_input:
    canonical_query = query_input.strip()
    st.subheader(f"Results for: {canonical_query}")

    yelp_reviews = fetch_yelp_reviews_json(canonical_query)
    google_reviews = fetch_google_places_reviews(canonical_query)
    hg_reviews = fetch_healthgrades_reviews(canonical_query)
    gd_reviews = fetch_glassdoor_reviews(canonical_query)

    all_reviews = yelp_reviews + google_reviews + hg_reviews + gd_reviews

    if all_reviews:
        st.write(f"Found {len(all_reviews)} reviews across multiple sites:")
        for rv in all_reviews[:10]:
            st.markdown(f"- **{rv.get('site')}** ({rv.get('rating')}) by {rv.get('author')}: {rv.get('text')[:200]}... [link]({rv.get('url')})")
    else:
        st.info("No reviews found for this query.")

    radar_scores = {
        "Visibility": min(len(all_reviews)/5,10),
        "Sentiment": min(sum([r.get("rating") or 0 for r in all_reviews])/len(all_reviews),10) if all_reviews else 0,
        "Freshness": 8,
        "Corporate": 7
    }
    st.plotly_chart(plot_radar_chart(radar_scores), use_container_width=True)

    with st.expander("💡 Brand Improvement Tips"):
        tips = []
        if radar_scores["Visibility"] < 5:
            tips.append("Increase your online mentions and citations.")
        if radar_scores["Sentiment"] < 6:
            tips.append("Respond to reviews and improve customer satisfaction.")
        if radar_scores["Freshness"] < 5:
            tips.append("Ensure recent activity is posted online regularly.")
        if radar_scores["Corporate"] < 5:
            tips.append("Boost company presence via LinkedIn, Glassdoor, etc.")
        if tips:
            for t in tips:
                st.markdown(f"- {t}")
        else:
            st.markdown("All metrics look good!")

    if debug_mode:
        with st.expander("🐞 Debug Information"):
            st.write("Canonical query:", canonical_query)
            st.write("All reviews fetched:", all_reviews)

    with st.expander("🌐 Sources / Validation Links"):
        urls = [rv.get("url") for rv in all_reviews if rv.get("url")]
        for u in urls:
            st.markdown(f"- [Link]({u})")
