import os
import re
import json
import time
import hashlib
import logging
import threading
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from flask import Flask, jsonify

# ─────────────────────────────────────────────
#  CONFIG — loaded from environment variables
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN     = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID       = os.environ.get("TELEGRAM_CHAT_ID", "")
CHECK_INTERVAL_MINUTES = int(os.environ.get("CHECK_INTERVAL_MINUTES", "30"))

CRITERIA = {
    "buildings": ["rimal 4", "rimal 5", "bahar 4"],
    "min_price": 1_500_000,
    "max_price": 2_000_000,
    "bedrooms": 2,
    "sea_view_keywords": [
        "sea view", "sea facing", "full sea", "ocean view",
        "marina view", "beachfront", "sea front"
    ],
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  FLASK — keeps the service alive on Railway
# ─────────────────────────────────────────────
app = Flask(__name__)

@app.route("/")
def index():
    return jsonify({
        "status": "running",
        "agent": "UAE Property Alert Bot",
        "monitoring": "Rimal 4, Rimal 5, Bahar 4 — JBR",
        "interval_minutes": CHECK_INTERVAL_MINUTES,
        "last_check": last_check_time
    })

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

last_check_time = "Not yet"

# ─────────────────────────────────────────────
#  SEEN LISTINGS STATE
# ─────────────────────────────────────────────
seen_listings = set()

def listing_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()

# ─────────────────────────────────────────────
#  TELEGRAM
# ─────────────────────────────────────────────
def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Telegram credentials not set!")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
        }, timeout=10)
        r.raise_for_status()
        log.info("Telegram alert sent.")
    except Exception as e:
        log.error(f"Telegram error: {e}")

def format_alert(listing: dict) -> str:
    sea_tag = "🌊 SEA VIEW ✅" if listing.get("sea_view") else "🏙️ No sea view mentioned"
    price_fmt = f"AED {listing['price']:,.0f}"
    return (
        f"🏠 <b>NEW PROPERTY ALERT — JBR</b>\n\n"
        f"🏢 <b>Building:</b> {listing['building']}\n"
        f"🛏 <b>Bedrooms:</b> {listing['bedrooms']} BR\n"
        f"💰 <b>Price:</b> {price_fmt}\n"
        f"📐 <b>Size:</b> {listing.get('size', 'N/A')}\n"
        f"👁 <b>View:</b> {sea_tag}\n"
        f"📋 <b>Title:</b> {listing['title']}\n"
        f"🔗 {listing['url']}\n\n"
        f"📡 Source: {listing['source']}\n"
        f"⏰ {datetime.now().strftime('%d %b %Y, %H:%M')} UAE time"
    )

# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────
def parse_price(text: str) -> float:
    digits = re.sub(r"[^\d]", "", str(text))
    return float(digits) if digits else 0.0

def parse_beds(text: str) -> int:
    m = re.search(r"(\d+)", str(text))
    return int(m.group(1)) if m else 0

def detect_sea_view(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in CRITERIA["sea_view_keywords"])

def detect_building(text: str) -> str:
    t = text.lower()
    for b in CRITERIA["buildings"]:
        if b in t:
            return b.title()
    return ""

def matches_criteria(listing: dict) -> bool:
    combined = (listing.get("title", "") + " " + listing.get("building", "")).lower()
    if not any(b in combined for b in CRITERIA["buildings"]):
        return False
    price = listing.get("price", 0)
    if not (CRITERIA["min_price"] <= price <= CRITERIA["max_price"]):
        return False
    if listing.get("bedrooms", 0) != CRITERIA["bedrooms"]:
        return False
    return True

# ─────────────────────────────────────────────
#  SCRAPERS
# ─────────────────────────────────────────────
PF_SEARCHES = [
    ("rimal 4",  "https://www.propertyfinder.ae/en/search?c=2&t=1&fu=0&rp=y&ob=mr&l=JBR&bdr%5B%5D=2&pf=1500000&pt=2000000&kw=rimal+4"),
    ("rimal 5",  "https://www.propertyfinder.ae/en/search?c=2&t=1&fu=0&rp=y&ob=mr&l=JBR&bdr%5B%5D=2&pf=1500000&pt=2000000&kw=rimal+5"),
    ("bahar 4",  "https://www.propertyfinder.ae/en/search?c=2&t=1&fu=0&rp=y&ob=mr&l=JBR&bdr%5B%5D=2&pf=1500000&pt=2000000&kw=bahar+4"),
]

BAYUT_SEARCHES = [
    ("rimal 4", "https://www.bayut.com/for-sale/apartments/dubai/jumeirah-beach-residence/?bedrooms=2&price_min=1500000&price_max=2000000&keywords=rimal+4"),
    ("rimal 5", "https://www.bayut.com/for-sale/apartments/dubai/jumeirah-beach-residence/?bedrooms=2&price_min=1500000&price_max=2000000&keywords=rimal+5"),
    ("bahar 4", "https://www.bayut.com/for-sale/apartments/dubai/jumeirah-beach-residence/?bedrooms=2&price_min=1500000&price_max=2000000&keywords=bahar+4"),
]

def scrape_site(searches, base_url, source_name, card_selectors, field_map):
    results = []
    for keyword, url in searches:
        try:
            log.info(f"[{source_name}] Checking: {keyword}")
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            cards = []
            for sel in card_selectors:
                cards = soup.select(sel)
                if cards:
                    break

            log.info(f"[{source_name}] Found {len(cards)} cards for '{keyword}'")

            for card in cards:
                try:
                    def get_text(selectors):
                        for s in selectors:
                            el = card.select_one(s)
                            if el:
                                return el.get_text(strip=True)
                        return ""

                    title = get_text(field_map["title"])
                    price_text = get_text(field_map["price"])
                    bed_text = get_text(field_map["bed"])
                    size_text = get_text(field_map.get("size", []))

                    link_el = card.select_one("a[href]")
                    href = link_el["href"] if link_el else ""
                    full_url = href if href.startswith("http") else f"{base_url}{href}"

                    card_text = card.get_text(" ", strip=True)
                    building = detect_building(title + " " + card_text)
                    if not building:
                        building = keyword.title()

                    listing = {
                        "source": source_name,
                        "title": title or f"{keyword.title()} listing",
                        "price": parse_price(price_text),
                        "bedrooms": parse_beds(bed_text) or CRITERIA["bedrooms"],
                        "url": full_url,
                        "sea_view": detect_sea_view(card_text),
                        "building": building,
                        "size": size_text,
                    }
                    results.append(listing)
                except Exception as e:
                    log.debug(f"Card parse error: {e}")

            time.sleep(4)
        except Exception as e:
            log.warning(f"[{source_name}] Failed for '{keyword}': {e}")

    return results

def scrape_propertyfinder():
    return scrape_site(
        PF_SEARCHES,
        "https://www.propertyfinder.ae",
        "PropertyFinder",
        card_selectors=[
            "article[data-testid='property-card']",
            "[class*='property-card']",
            "article",
            "[class*='card']",
        ],
        field_map={
            "title": ["h2", "h3", "[class*='title']", "[data-testid='property-name']"],
            "price": ["[data-testid='property-price']", "[class*='price']", "strong"],
            "bed":   ["[aria-label*='bedroom']", "[data-testid*='bed']", "[class*='bed']"],
            "size":  ["[data-testid*='area']", "[class*='area']", "[class*='size']"],
        }
    )

def scrape_bayut():
    return scrape_site(
        BAYUT_SEARCHES,
        "https://www.bayut.com",
        "Bayut",
        card_selectors=[
            "article[class*='property']",
            "[class*='listing-card']",
            "article",
        ],
        field_map={
            "title": ["h2", "h3", "[class*='title']"],
            "price": ["[class*='price']", "strong", "span[aria-label*='price']"],
            "bed":   ["[class*='bed']", "[aria-label*='bed']", "[class*='room']"],
            "size":  ["[class*='area']", "[class*='size']", "[aria-label*='area']"],
        }
    )

# ─────────────────────────────────────────────
#  MAIN AGENT LOOP
# ─────────────────────────────────────────────
def agent_loop():
    global last_check_time
    log.info("Agent loop started.")

    time.sleep(5)  # let Flask start first

    send_telegram(
        "🤖 <b>Property Agent is LIVE!</b>\n\n"
        "I'm now watching JBR for:\n"
        "🏢 Rimal 4 | Rimal 5 | Bahar 4\n"
        "🛏 2 Bedrooms\n"
        "💰 AED 1.5M – 2M\n"
        "🌊 Sea view preferred\n\n"
        f"📡 Checking PropertyFinder + Bayut every {CHECK_INTERVAL_MINUTES} mins.\n"
        "I'll alert you the moment something new appears! 🏠"
    )

    while True:
        try:
            last_check_time = datetime.now().strftime("%d %b %Y, %H:%M")
            log.info(f"--- Check at {last_check_time} ---")

            all_listings = scrape_propertyfinder() + scrape_bayut()
            new_count = 0

            for listing in all_listings:
                if not matches_criteria(listing):
                    continue
                lid = listing_id(listing["url"])
                if lid in seen_listings:
                    continue

                seen_listings.add(lid)
                new_count += 1
                log.info(f"NEW MATCH: {listing['title']} — AED {listing['price']:,.0f} ({listing['source']})")
                send_telegram(format_alert(listing))
                time.sleep(2)

            log.info(f"Check done. {new_count} new matches. Sleeping {CHECK_INTERVAL_MINUTES}m.")

        except Exception as e:
            log.error(f"Agent loop error: {e}")

        time.sleep(CHECK_INTERVAL_MINUTES * 60)


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    # Start agent in background thread
    t = threading.Thread(target=agent_loop, daemon=True)
    t.start()

    # Start Flask web server (required by Railway/Render)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
