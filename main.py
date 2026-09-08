import os
import re
import time
import logging
from typing import Optional

import requests
from bs4 import BeautifulSoup


# =========================================================
# CONFIG
# =========================================================

PRODUCT_URL = os.getenv(
    "PRODUCT_URL",
    "https://www.myntra.com/smart-watches/boat/boat-lunar-discovery-pro-smartwatch---active-black/44438582/buy"
)

TARGET_PRICE = int(os.getenv("TARGET_PRICE", "1199"))
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "90"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

REQUEST_TIMEOUT = 30


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)


# =========================================================
# SESSION
# =========================================================

session = requests.Session()

session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 15) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/140.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://www.myntra.com/",
})


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(message: str) -> bool:

    if not TELEGRAM_BOT_TOKEN:
        logging.error("TELEGRAM_BOT_TOKEN missing")
        return False

    if not TELEGRAM_CHAT_ID:
        logging.error("TELEGRAM_CHAT_ID missing")
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    try:

        response = session.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "disable_web_page_preview": False,
            },
            timeout=REQUEST_TIMEOUT
        )

        if response.ok:
            logging.info("Telegram message sent")
            return True

        logging.error(
            "Telegram error %s: %s",
            response.status_code,
            response.text[:300]
        )

    except requests.RequestException as exc:

        logging.error(
            "Telegram request error: %s",
            exc
        )

    return False


# =========================================================
# DIRECT MYNTRA REQUEST
# =========================================================

def fetch_direct():

    try:

        response = session.get(
            PRODUCT_URL,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True
        )

        logging.info(
            "Direct Myntra HTTP: %s | bytes=%s",
            response.status_code,
            len(response.content)
        )

        if response.status_code == 200 and len(response.text) > 10000:
            return response.text

        logging.warning(
            "Direct Myntra response unusable"
        )

    except requests.RequestException as exc:

        logging.warning(
            "Direct Myntra request failed: %s",
            exc
        )

    return None


# =========================================================
# FALLBACK READER
#
# If Myntra gives Render a tiny/challenge response,
# use a text-rendering fallback.
# =========================================================

def fetch_fallback():

    # Reader-style URL.
    reader_url = (
        "https://r.jina.ai/"
        + PRODUCT_URL
    )

    try:

        response = requests.get(
            reader_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "Chrome/140 Safari/537.36"
                ),
                "Accept": "text/plain,text/html,*/*",
            },
            timeout=REQUEST_TIMEOUT
        )

        logging.info(
            "Fallback HTTP: %s | bytes=%s",
            response.status_code,
            len(response.content)
        )

        if response.status_code == 200:

            text = response.text.strip()

            if len(text) > 500:
                return text

    except requests.RequestException as exc:

        logging.warning(
            "Fallback request failed: %s",
            exc
        )

    return None


# =========================================================
# PRICE EXTRACTION
# =========================================================

def extract_price(text: str) -> Optional[int]:

    if not text:
        return None

    candidates = []

    # ---------------------------------------------
    # JSON / HTML style price fields
    # ---------------------------------------------

    patterns = [

        r'"discountedPrice"\s*:\s*"?(\\?d+)"?',
        r'"discounted_price"\s*:\s*"?(\\?d+)"?',
        r'"sellingPrice"\s*:\s*"?(\\?d+)"?',
        r'"selling_price"\s*:\s*"?(\\?d+)"?',
        r'"currentPrice"\s*:\s*"?(\\?d+)"?',
        r'"price"\s*:\s*"?(\\?d+)"?',

    ]

    # Correct numeric patterns separately
    patterns = [
        r'"discountedPrice"\s*:\s*"?(\d+)"?',
        r'"discounted_price"\s*:\s*"?(\d+)"?',
        r'"sellingPrice"\s*:\s*"?(\d+)"?',
        r'"selling_price"\s*:\s*"?(\d+)"?',
        r'"currentPrice"\s*:\s*"?(\d+)"?',
    ]

    for pattern in patterns:

        for match in re.findall(
            pattern,
            text,
            flags=re.I
        ):

            try:
                value = int(match)

                if 100 <= value <= 100000:
                    candidates.append(value)

            except ValueError:
                pass


    # ---------------------------------------------
    # Myntra visible text
    # ---------------------------------------------

    visible_patterns = [

        r"Selling Price\s*(?:Rs\.?|₹)\s*([\d,]+)",

        r"Price Details.*?(?:Rs\.?|₹)\s*([\d,]+)",

        r"(?:Rs\.?|₹)\s*([\d,]+)",

    ]

    for pattern in visible_patterns:

        for match in re.findall(
            pattern,
            text,
            flags=re.I | re.S
        ):

            try:

                value = int(
                    match.replace(",", "")
                )

                if 100 <= value <= 100000:
                    candidates.append(value)

            except ValueError:
                pass


    if not candidates:
        return None


    # -------------------------------------------------
    # Important:
    # Prefer the price near "Selling Price".
    # -------------------------------------------------

    lower = text.lower()

    selling_index = lower.find(
        "selling price"
    )

    if selling_index >= 0:

        nearby = text[
            selling_index:
            selling_index + 200
        ]

        nearby_prices = re.findall(
            r"(?:₹|Rs\.?)\s*([\d,]+)",
            nearby,
            flags=re.I
        )

        for value in nearby_prices:

            try:

                value = int(
                    value.replace(",", "")
                )

                if 100 <= value <= 100000:
                    return value

            except ValueError:
                pass


    # Frequency fallback

    frequency = {}

    for value in candidates:
        frequency[value] = (
            frequency.get(value, 0) + 1
        )

    return sorted(
        frequency,
        key=lambda x: (
            -frequency[x],
            x
        )
    )[0]


# =========================================================
# STOCK EXTRACTION
# =========================================================

def extract_stock(text: str):

    if not text:
        return None, []


    lower = text.lower()


    # -------------------------------------------------
    # Explicit out-of-stock
    # -------------------------------------------------

    out_words = [
        "out of stock",
        "sold out",
        "currently unavailable",
    ]

    for word in out_words:

        if word in lower:

            return False, []


    # -------------------------------------------------
    # Explicit available signals
    # -------------------------------------------------

    available_words = [
        "add to bag",
        "add to cart",
        "buy now",
    ]

    available = any(
        word in lower
        for word in available_words
    )


    # -------------------------------------------------
    # Sizes
    # -------------------------------------------------

    sizes = []

    size_patterns = [
        r"\bOnesize\b",
        r"\bOne Size\b",
        r"\bFree Size\b",
    ]

    for pattern in size_patterns:

        matches = re.findall(
            pattern,
            text,
            flags=re.I
        )

        for match in matches:

            if match not in sizes:
                sizes.append(match)


    # -------------------------------------------------
    # Final stock result
    # -------------------------------------------------

    if available:

        return True, sizes

    return None, sizes


# =========================================================
# CHECK PRODUCT
# =========================================================

def check_product():

    # -------------------------------------------------
    # 1. Direct request
    # -------------------------------------------------

    text = fetch_direct()


    # -------------------------------------------------
    # 2. Fallback if direct response is bad
    # -------------------------------------------------

    if text is None:

        logging.info(
            "Using fallback reader..."
        )

        text = fetch_fallback()


    if not text:

        logging.error(
            "No usable Myntra response"
        )

        return None


    price = extract_price(text)

    stock, sizes = extract_stock(text)


    logging.info(
        "Parsed -> Price=%s | Stock=%s | Sizes=%s",
        price,
        stock,
        sizes
    )


    return {
        "price": price,
        "stock": stock,
        "sizes": sizes,
    }


# =========================================================
# ALERT STATE
# =========================================================

price_alert_sent = False
stock_alert_sent = False


# =========================================================
# PROCESS ALERT
# =========================================================

def process(data):

    global price_alert_sent
    global stock_alert_sent


    if not data:

        return


    price = data["price"]
    stock = data["stock"]
    sizes = data["sizes"]


    # =====================================================
    # PRICE ALERT
    # =====================================================

    if price is not None:

        logging.info(
            "Current price: ₹%s | Target: ₹%s",
            price,
            TARGET_PRICE
        )


        # Price went above target again
        # => allow future alert
        if price > TARGET_PRICE:

            price_alert_sent = False


        # Target reached
        if (
            price <= TARGET_PRICE
            and not price_alert_sent
        ):

            message = (
                "🔥 MYNTRA PRICE ALERT\n\n"
                "⌚ boAt Lunar Discovery Pro\n"
                f"💰 Current Price: ₹{price:,}\n"
                f"🎯 Target Price: ₹{TARGET_PRICE:,}\n"
                "\n"
                f"🔗 {PRODUCT_URL}"
            )


            if send_telegram(message):

                price_alert_sent = True

                logging.info(
                    "PRICE ALERT SENT"
                )


    # =====================================================
    # STOCK ALERT
    # =====================================================

    if stock is True:

        if not stock_alert_sent:

            size_text = (
                ", ".join(sizes)
                if sizes
                else "Available"
            )


            price_text = (
                f"₹{price:,}"
                if price is not None
                else "Unknown"
            )


            message = (
                "🟢 MYNTRA STOCK ALERT\n\n"
                "⌚ boAt Lunar Discovery Pro\n"
                "📦 Stock: AVAILABLE\n"
                f"👕 Size: {size_text}\n"
                f"💰 Price: {price_text}\n"
                "\n"
                f"🔗 {PRODUCT_URL}"
            )


            if send_telegram(message):

                stock_alert_sent = True

                logging.info(
                    "STOCK ALERT SENT"
                )


    elif stock is False:

        # Product became unavailable.
        # Future availability should alert again.
        stock_alert_sent = False


# =========================================================
# MAIN LOOP
# =========================================================

def main():

    logging.info(
        "===================================="
    )

    logging.info(
        "Myntra Price + Stock Alert Started"
    )

    logging.info(
        "Product ID: 44438582"
    )

    logging.info(
        "Target Price: ₹%s",
        TARGET_PRICE
    )

    logging.info(
        "Check Interval: %s seconds",
        CHECK_INTERVAL
    )

    logging.info(
        "===================================="
    )


    # -------------------------------------------------
    # Startup Telegram message
    # -------------------------------------------------

    send_telegram(
        "✅ Myntra Alert Bot Started\n\n"
        "⌚ boAt Lunar Discovery Pro\n"
        f"🎯 Target: ₹{TARGET_PRICE:,}\n"
        f"⏱️ Checking every {CHECK_INTERVAL} seconds"
    )


    # -------------------------------------------------
    # Continuous checking
    # -------------------------------------------------

    while True:

        try:

            data = check_product()

            process(data)

        except Exception as exc:

            logging.exception(
                "Unexpected loop error: %s",
                exc
            )


        logging.info(
            "Next check in %s seconds...",
            CHECK_INTERVAL
        )


        time.sleep(
            CHECK_INTERVAL
        )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    main()
