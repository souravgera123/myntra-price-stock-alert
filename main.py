import os
import re
import time
import json
import logging
from typing import Optional

import requests
from bs4 import BeautifulSoup


# =========================
# CONFIG
# =========================

PRODUCT_URL = os.getenv(
    "PRODUCT_URL",
    "https://www.myntra.com/smart-watches/boat/boat-lunar-discovery-pro-smartwatch---active-black/44438582/buy"
)

TARGET_PRICE = int(os.getenv("TARGET_PRICE", "1199"))
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "90"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

REQUEST_TIMEOUT = 25

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

session = requests.Session()

session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 15) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,image/webp,"
        "*/*;q=0.8"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://www.myntra.com/",
})


# =========================
# TELEGRAM
# =========================

def telegram_send(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.error("Telegram credentials missing.")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": False,
    }

    try:
        response = session.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        if response.ok:
            return True

        logging.error(
            "Telegram error: %s %s",
            response.status_code,
            response.text[:300]
        )

    except requests.RequestException as exc:
        logging.error("Telegram request failed: %s", exc)

    return False


# =========================
# PRICE PARSING
# =========================

def clean_price(value) -> Optional[int]:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return int(value)

    text = str(value)

    numbers = re.findall(r"\d[\d,]*", text)

    if not numbers:
        return None

    try:
        return int(numbers[0].replace(",", ""))
    except ValueError:
        return None


def extract_prices(html: str):
    prices = []

    # JSON-LD
    soup = BeautifulSoup(html, "html.parser")

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text())

            objects = data if isinstance(data, list) else [data]

            for obj in objects:
                if not isinstance(obj, dict):
                    continue

                offers = obj.get("offers")

                if isinstance(offers, dict):
                    p = clean_price(offers.get("price"))
                    if p:
                        prices.append(p)

                elif isinstance(offers, list):
                    for offer in offers:
                        if isinstance(offer, dict):
                            p = clean_price(offer.get("price"))
                            if p:
                                prices.append(p)

        except Exception:
            pass

    # Common Myntra price patterns
    patterns = [
        r'"discountedPrice"\s*:\s*(\d+)',
        r'"discountedPrice"\s*:\s*"(\d+)"',
        r'"sellingPrice"\s*:\s*(\d+)',
        r'"sellingPrice"\s*:\s*"(\d+)"',
        r'"discounted_price"\s*:\s*(\d+)',
        r'"price"\s*:\s*(\d+)',
        r'"price"\s*:\s*"(\d+)"',
    ]

    for pattern in patterns:
        for match in re.findall(pattern, html, flags=re.I):
            try:
                prices.append(int(match))
            except ValueError:
                pass

    # Visible rupee prices
    for match in re.findall(
        r"(?:₹|Rs\.?\s*)\s*([\d,]+)",
        html,
        flags=re.I
    ):
        try:
            prices.append(int(match.replace(",", "")))
        except ValueError:
            pass

    # Remove obviously impossible values
    prices = [
        p for p in prices
        if 1 <= p <= 10_00_000
    ]

    if not prices:
        return None

    # Prefer a price near the actual product price.
    # For this product, ₹8,999 is expected currently.
    # Taking the smallest candidate can accidentally pick coupon/offer values,
    # so use frequency first.
    frequency = {}

    for p in prices:
        frequency[p] = frequency.get(p, 0) + 1

    most_common = sorted(
        frequency.items(),
        key=lambda x: (-x[1], x[0])
    )

    return most_common[0][0]


# =========================
# STOCK PARSING
# =========================

def extract_stock(html: str):
    soup = BeautifulSoup(html, "html.parser")

    html_lower = html.lower()

    # Strong out-of-stock indicators
    out_patterns = [
        "out of stock",
        "sold out",
        "currently unavailable",
        "notify me",
    ]

    # Strong available indicators
    available_patterns = [
        "add to bag",
        "add to cart",
        "buy now",
    ]

    out_found = any(
        pattern in html_lower
        for pattern in out_patterns
    )

    available_found = any(
        pattern in html_lower
        for pattern in available_patterns
    )

    # Extract visible size names.
    sizes = []

    for text in soup.stripped_strings:
        clean = text.strip()

        if not clean:
            continue

        if len(clean) > 30:
            continue

        if clean.lower() in {
            "select size",
            "size",
            "add to bag",
            "wishlist",
            "buy now",
        }:
            continue

        # Common single-size watch listing
        if clean.lower() in {
            "onesize",
            "one size",
            "free size",
        }:
            sizes.append(clean)

    sizes = list(dict.fromkeys(sizes))

    if available_found and not out_found:
        return True, sizes

    if out_found and not available_found:
        return False, sizes

    # Product page accessible but stock state uncertain.
    return None, sizes


# =========================
# PRODUCT CHECK
# =========================

def check_product():
    try:
        response = session.get(
            PRODUCT_URL,
            timeout=REQUEST_TIMEOUT
        )

        logging.info(
            "Myntra HTTP status: %s",
            response.status_code
        )

        if response.status_code != 200:
            return None

        html = response.text

        if len(html) < 10_000:
            logging.warning(
                "Myntra response is unusually small."
            )

        price = extract_prices(html)
        in_stock, sizes = extract_stock(html)

        return {
            "price": price,
            "in_stock": in_stock,
            "sizes": sizes,
        }

    except requests.RequestException as exc:
        logging.error("Myntra request failed: %s", exc)
        return None

    except Exception as exc:
        logging.exception(
            "Unexpected parsing error: %s",
            exc
        )
        return None


# =========================
# ALERT STATE
# =========================

last_price = None
last_stock = None

price_alert_sent = False
stock_alert_sent = False


def process_product(data):
    global last_price
    global last_stock
    global price_alert_sent
    global stock_alert_sent

    if not data:
        logging.warning(
            "No usable product data. Will retry."
        )
        return

    price = data.get("price")
    in_stock = data.get("in_stock")
    sizes = data.get("sizes") or []

    logging.info(
        "Price=%s | Stock=%s | Sizes=%s",
        price,
        in_stock,
        sizes
    )

    # ---------------------------------
    # PRICE ALERT
    # ---------------------------------

    if price is not None:

        # Reset after price goes above target.
        if price > TARGET_PRICE:
            price_alert_sent = False

        if price <= TARGET_PRICE and not price_alert_sent:

            message = (
                "🔥 MYNTRA PRICE ALERT\n\n"
                "boAt Lunar Discovery Pro Smartwatch\n"
                f"💰 Current Price: ₹{price:,}\n"
                f"🎯 Target Price: ₹{TARGET_PRICE:,}\n"
            )

            if telegram_send(
                message + f"\n🔗 {PRODUCT_URL}"
            ):
                price_alert_sent = True
                logging.info(
                    "Price alert sent."
                )

    # ---------------------------------
    # STOCK ALERT
    # ---------------------------------

    if in_stock is True:

        if not stock_alert_sent:

            size_text = (
                ", ".join(sizes)
                if sizes
                else "Available"
            )

            message = (
                "🟢 MYNTRA STOCK ALERT\n\n"
                "boAt Lunar Discovery Pro Smartwatch\n"
                f"📦 Stock: AVAILABLE\n"
                f"👕 Size: {size_text}\n"
            )

            if price is not None:
                message += f"💰 Price: ₹{price:,}\n"

            message += f"\n🔗 {PRODUCT_URL}"

            if telegram_send(message):
                stock_alert_sent = True
                logging.info(
                    "Stock alert sent."
                )

    elif in_stock is False:

        # Product went out of stock.
        # Allow a future stock alert.
        stock_alert_sent = False

    last_price = price
    last_stock = in_stock


# =========================
# MAIN LOOP
# =========================

def main():

    logging.info("===================================")
    logging.info("Myntra Price + Stock Alert Started")
    logging.info("Product ID: 44438582")
    logging.info("Target Price: ₹%s", TARGET_PRICE)
    logging.info("Interval: %s seconds", CHECK_INTERVAL)
    logging.info("===================================")

    # Optional startup Telegram message
    telegram_send(
        "✅ Myntra Alert Bot Started\n\n"
        "📦 Product: boAt Lunar Discovery Pro\n"
        f"🎯 Target Price: ₹{TARGET_PRICE:,}\n"
        f"⏱️ Check Interval: {CHECK_INTERVAL} seconds"
    )

    while True:

        try:
            data = check_product()
            process_product(data)

        except Exception as exc:
            logging.exception(
                "Main loop error: %s",
                exc
            )

        logging.info(
            "Next check in %s seconds...",
            CHECK_INTERVAL
        )

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
