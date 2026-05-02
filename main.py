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

TELEGRAM_BOT_TOKEN = os.environ.get(“TELEGRAM_BOT_TOKEN”, “”)
TELEGRAM_CHAT_ID = os.environ.get(“TELEGRAM_CHAT_ID”, “”)
CHECK_INTERVAL_MINUTES = int(os.environ.get(“CHECK_INTERVAL_MINUTES”, “30”))

CRITERIA = {
“buildings”: [“rimal 4”, “rimal 5”, “bahar 4”],
“min_price”: 1500000,
“max_price”: 2000000,
“bedrooms”: 2,
“sea_view_keywords”: [“sea view”, “sea facing”, “full sea”, “ocean view”, “marina view”, “beachfront”],
}

HEADERS = {
“User-Agent”: “Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36”,
“Accept-Language”: “en-US,en;q=0.9”,
}

logging.basicConfig(level=logging.INFO, format=”%(asctime)s [%(levelname)s] %(message)s”)
log = logging.getLogger(**name**)

app = Flask(**name**)
seen_listings = set()
last_check_time = “Not yet”

@app.route(”/”)
def index():
return jsonify({“status”: “running”, “last_check”: last_check_time})

@app.route(”/health”)
def health():
return jsonify({“status”: “ok”})

def listing_id(url):
return hashlib.md5(url.encode()).hexdigest()

def send_telegram(message):
if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
log.error(“Telegram credentials missing!”)
return
url = f”https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage”
try:
r = requests.post(url, json={“chat_id”: TELEGRAM_CHAT_ID, “text”: message, “parse_mode”: “HTML”}, timeout=10)
r.raise_for_status()
log.info(“Telegram message sent!”)
except Exception as e:
log.error(f”Telegram error: {e}”)

def format_alert(listing):
sea_tag = “🌊 SEA VIEW” if listing.get(“sea_view”) else “🏙️ No sea view mentioned”
return (
f”🏠 <b>NEW PROPERTY ALERT — JBR</b>\n\n”
f”🏢 <b>Building:</b> {listing[‘building’]}\n”
f”🛏 <b>Bedrooms:</b> 2 BR\n”
f”💰 <b>Price:</b> AED {listing[‘price’]:,.0f}\n”
f”👁 <b>View:</b> {sea_tag}\n”
f”📋 <b>Title:</b> {listing[‘title’]}\n”
f”🔗 {listing[‘url’]}\n”
f”📡 Source: {listing[‘source’]}\n”
f”⏰ {datetime.now().strftime(’%d %b %Y, %H:%M’)}”
)

def parse_price(text):
digits = re.sub(r”[^\d]”, “”, str(text))
return float(digits) if digits else 0.0

def parse_beds(text):
m = re.search(r”(\d+)”, str(text))
return int(m.group(1)) if m else 0

def detect_sea_view(text):
return any(kw in text.lower() for kw in CRITERIA[“sea_view_keywords”])

def detect_building(text):
t = text.lower()
for b in CRITERIA[“buildings”]:
if b in t:
return b.title()
return “”

def matches_criteria(listing):
combined = (listing.get(“title”, “”) + “ “ + listing.get(“building”, “”)).lower()
if not any(b in combined for b in CRITERIA[“buildings”]):
return False
if not (CRITERIA[“min_price”] <= listing.get(“price”, 0) <= CRITERIA[“max_price”]):
return False
if listing.get(“bedrooms”, 0) != CRITERIA[“bedrooms”]:
return False
return True

SEARCHES = [
(“PropertyFinder”, “rimal 4”, “https://www.propertyfinder.ae/en/search?c=2&t=1&l=JBR&bdr%5B%5D=2&pf=1500000&pt=2000000&kw=rimal+4”),
(“PropertyFinder”, “rimal 5”, “https://www.propertyfinder.ae/en/search?c=2&t=1&l=JBR&bdr%5B%5D=2&pf=1500000&pt=2000000&kw=rimal+5”),
(“PropertyFinder”, “bahar 4”, “https://www.propertyfinder.ae/en/search?c=2&t=1&l=JBR&bdr%5B%5D=2&pf=1500000&pt=2000000&kw=bahar+4”),
(“Bayut”, “rimal 4”, “https://www.bayut.com/for-sale/apartments/dubai/jumeirah-beach-residence/?bedrooms=2&price_min=1500000&price_max=2000000&keywords=rimal+4”),
(“Bayut”, “rimal 5”, “https://www.bayut.com/for-sale/apartments/dubai/jumeirah-beach-residence/?bedrooms=2&price_min=1500000&price_max=2000000&keywords=rimal+5”),
(“Bayut”, “bahar 4”, “https://www.bayut.com/for-sale/apartments/dubai/jumeirah-beach-residence/?bedrooms=2&price_min=1500000&price_max=2000000&keywords=bahar+4”),
]

def scrape_all():
results = []
for source, keyword, url in SEARCHES:
try:
log.info(f”[{source}] Checking: {keyword}”)
r = requests.get(url, headers=HEADERS, timeout=20)
r.raise_for_status()
soup = BeautifulSoup(r.text, “html.parser”)
cards = soup.select(“article”) or soup.select(”[class*=‘card’]”)
log.info(f”[{source}] Found {len(cards)} cards”)
for card in cards:
try:
title_el = card.select_one(“h2, h3, [class*=‘title’]”)
title = title_el.get_text(strip=True) if title_el else keyword.title()
price_el = card.select_one(”[class*=‘price’]”)
price = parse_price(price_el.get_text(strip=True) if price_el else “0”)
bed_el = card.select_one(”[class*=‘bed’], [aria-label*=‘bed’]”)
beds = parse_beds(bed_el.get_text(strip=True) if bed_el else “2”)
link_el = card.select_one(“a[href]”)
href = link_el[“href”] if link_el else “”
base = “https://www.propertyfinder.ae” if source == “PropertyFinder” else “https://www.bayut.com”
full_url = href if href.startswith(“http”) else f”{base}{href}”
card_text = card.get_text(” “, strip=True)
building = detect_building(title + “ “ + card_text) or keyword.title()
results.append({
“source”: source,
“title”: title,
“price”: price,
“bedrooms”: beds or 2,
“url”: full_url,
“sea_view”: detect_sea_view(card_text),
“building”: building,
})
except Exception as e:
log.debug(f”Card error: {e}”)
time.sleep(4)
except Exception as e:
log.warning(f”[{source}] Failed: {e}”)
return results

def agent_loop():
global last_check_time
log.info(“Agent starting…”)
time.sleep(5)
send_telegram(
“🤖 <b>Property Agent is LIVE!</b>\n\n”
“Watching JBR for:\n”
“🏢 Rimal 4 | Rimal 5 | Bahar 4\n”
“🛏 2 Bedrooms\n”
“💰 AED 1.5M – 2M\n”
“🌊 Sea view preferred\n\n”
f”Checking every {CHECK_INTERVAL_MINUTES} mins 🔍”
)
while True:
try:
last_check_time = datetime.now().strftime(”%d %b %Y, %H:%M”)
log.info(f”Running check at {last_check_time}”)
listings = scrape_all()
new_count = 0
for listing in listings:
if not matches_criteria(listing):
continue
lid = listing_id(listing[“url”])
if lid in seen_listings:
continue
seen_listings.add(lid)
new_count += 1
log.info(f”NEW MATCH: {listing[‘title’]} AED {listing[‘price’]:,.0f}”)
send_telegram(format_alert(listing))
time.sleep(2)
log.info(f”Done. {new_count} new matches. Sleeping {CHECK_INTERVAL_MINUTES}m.”)
except Exception as e:
log.error(f”Loop error: {e}”)
time.sleep(CHECK_INTERVAL_MINUTES * 60)

if **name** == “**main**”:
t = threading.Thread(target=agent_loop, daemon=True)
t.start()
port = int(os.environ.get(“PORT”, 8080))
app.run(host=“0.0.0.0”, port=port, debug=False, use_reloader=False)
