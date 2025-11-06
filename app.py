# app.py

import streamlit as st
from bs4 import BeautifulSoup
import requests
import re
import plotly.express as px
import pandas as pd
from datetime import datetime

# -------------------------------
# CONFIGURATION
# -------------------------------

st.set_page_config(
    page_title="Online Presence Monitor",
    layout="wide",
)

debug_mode = False

# -------------------------------
# UTILITIES
# -------------------------------

def safe_request(url):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            return resp
    except Exception as e:
        if debug_mode:
            st.write(f"[safe_request] Error fetching {url}: {e}")
    return None

@st.cache_data(ttl=3600)
def get_top_results(query, max_results=5):
    """Fetch top Google CSE results"""
    results = []
    API_KEY = st.secrets.get("GOOGLE_API_KEY")
    CSE_ID = st.secrets.get("GOOGLE_CSE_ID")
    if not API_KEY or not CSE_ID:
        if debug_mode:
            st.write("Missing Google API Key or CSE ID")
        return results

    url = f"https://www.googleapis.com/customsearch/v1?q={query}&cx={CSE_ID}&key={API_KEY}&num={max_results}"
    try:
        resp = requests.get(url)
        data = resp.json()
        for item in data.get("items", []):
            results.append({
                "title": item.get("title"),
                "href": item.get("link"),
                "snippet": item.get("snippet")
            })
    except Exception as e:
        if debug_mode:
            st.write(f"[get_top_results] Error: {e}")
    return results

# -------------------------------
# SITE-SPECIFIC PARSERS
# -------------------------------

@st.cache_data(ttl=3600)
def parse_yelp(query, max_reviews=5):
    reviews = []
    items = get_top_results(f"{query} site:yelp.com", max_results=5)

    for it in items:
        url = it.get("href")
        if "/biz/" not in url:
            continue
        r = safe_request(url)
        if not r:
            continue
        try:
            soup = BeautifulSoup(r.text, "html.parser")
            review_blocks = soup.select('div[data-review-id]')
            if not review_blocks:
                review_blocks = soup.select('div[class*="review__"]')

            if debug_mode:
                st.write(f"[Yelp] URL: {url} - Found {len(review_blocks)} review blocks")

            count = 0
            for rb in review_blocks:
                if count >= max_reviews:
                    break
                text_el = rb.find("p")
                text = text_el.get_text(strip=True) if text_el else ""
                if not text:
                    continue
                rating_val = None
                rating_el = rb.find("div", {"role": "img"})
                if rating_el and rating_el.get("aria-label"):
                    m = re.search(r"([0-5](?:\.\d)?) star rating", rating_el["aria-label"])
                    if m:
                        rating_val = float(m.group(1))
                reviews.append({
                    "site": "Yelp",
                    "text": text,
                    "rating": rating_val,
                    "url": url
                })
                count += 1

            if debug_mode and reviews:
                st.write(f"[Yelp] Sample review: {reviews[0]['text'][:200]}")

        except Exception as e:
            if debug_mode:
                st.write(f"[Yelp] Parsing error: {e}")
            continue

    return reviews[:max_reviews]

# Placeholder functions for Healthgrades, Glassdoor
def parse_healthgrades(query):
    return []

def parse_glassdoor(query):
    return []

# -------------------------------
# RATING AND TIPS LOGIC
# -------------------------------

def compute_overall_score(stats):
    """
    Compute an A-F score based on presence, ratings, recency, etc.
    """
    score = 0
    # Websites found
    score += min(stats.get("sites_found",0)/10,1)*2
    # Average rating
    score += (stats.get("avg_rating",3)/5)*2
    # Positive vs negative
    if stats.get("positive_reviews",0)+stats.get("negative_reviews",0) > 0:
        score += (stats.get("positive_reviews")/(stats.get("positive_reviews")+stats.get("negative_reviews")))*2
    # recency and company prevalence skipped for now
    # convert to letter grade
    if score >= 7:
        return "A"
    elif score >=6:
        return "B"
    elif score >=5:
        return "C"
    elif score >=4:
        return "D"
    else:
        return "F"

def generate_tips(stats):
    tips = []
    if stats.get("sites_found",0) < 3:
        tips.append("Try adding more content or profiles online to increase visibility.")
    if stats.get("avg_rating",0) < 4:
        tips.append("Encourage satisfied customers to leave reviews to improve average rating.")
    if stats.get("positive_reviews",0) < stats.get("negative_reviews",0):
        tips.append("Address negative feedback promptly to improve reputation.")
    if stats.get("sites_found",0) > 5 and stats.get("avg_rating",0) >=4:
        tips.append("Great online presence! Keep maintaining active engagement.")
    return tips

# -------------------------------
# MAIN APP
# -------------------------------

st.title("🕵️ Online Presence Monitor")

debug_mode = st.checkbox("Enable Debug Mode")

query = st.text_input(
    "Search Query",
    placeholder="Enter name, profession, and/or city (e.g., Dr. Jane Smith cardiologist Chicago)"
)

if query:
    st.subheader("📊 Overarching Statistics")
    # fetch sites
    google_results = get_top_results(query, max_results=25)
    yelp_reviews = parse_yelp(query, max_reviews=5)
    healthgrades_reviews = parse_healthgrades(query)
    glassdoor_reviews = parse_glassdoor(query)

    all_reviews = yelp_reviews + healthgrades_reviews + glassdoor_reviews
    sites_found = len(google_results)
    ratings = [r["rating"] for r in all_reviews if r.get("rating") is not None]
    avg_rating = round(sum(ratings)/len(ratings),2) if ratings else 0
    positive_reviews = sum(1 for r in all_reviews if r.get("rating") and r["rating"]>=4)
    negative_reviews = sum(1 for r in all_reviews if r.get("rating") and r["rating"]<4)

    stats = {
        "sites_found": sites_found,
        "avg_rating": avg_rating,
        "positive_reviews": positive_reviews,
        "negative_reviews": negative_reviews
    }

    st.metric("Total Websites Found", sites_found)
    st.metric("Average Rating", avg_rating)
    st.metric("Overall Score", compute_overall_score(stats))

    # Radar Chart
    radar_df = pd.DataFrame({
        "Metric": ["Presence", "Avg Rating", "Positive Reviews", "Negative Reviews"],
        "Score": [
            min(sites_found/10,1)*10,
            avg_rating/5*10,
            positive_reviews/(positive_reviews+negative_reviews)*10 if (positive_reviews+negative_reviews)>0 else 0,
            negative_reviews/(positive_reviews+negative_reviews)*10 if (positive_reviews+negative_reviews)>0 else 0
        ]
    })
    radar_template = "plotly_dark" if st.get_option("theme.base")=="dark" else "plotly"
    fig = px.line_polar(radar_df, r="Score", theta="Metric", line_close=True,
                        template=radar_template, markers=True)
    st.plotly_chart(fig,use_container_width=True)

    # Tips Section
    with st.expander("💡 Tips & Recommendations"):
        tips = generate_tips(stats)
        for tip in tips:
            st.write(f"- {tip}")

    # Reviews Section
    st.subheader("📝 Top Reviews")
    for r in all_reviews:
        st.markdown(f"**{r['site']}** - Rating: {r.get('rating','N/A')}  ")
        st.markdown(f"{r['text']}  ")
        st.markdown(f"[View Source]({r['url']})")
        st.markdown("---")

    # Debug Expander
    if debug_mode:
        with st.expander("🐞 Debug Info"):
            st.write("Google Results:", google_results)
            st.write("Yelp Reviews:", yelp_reviews)
            st.write("Healthgrades Reviews:", healthgrades_reviews)
            st.write("Glassdoor Reviews:", glassdoor_reviews)
