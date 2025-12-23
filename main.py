import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

# ====== CONFIG ======
MIN_P = 220.0
MAX_P = 230.0
POLL_SECONDS = 15
COOLDOWN_SECONDS = 300  # 5 min

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")  # required
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")      # required
TOKEN_NAME = os.getenv("TOKEN_NAME")
CMC_API_KEY = os.getenv("CMC_API_KEY")  # optional fallback if MEXC fails

MEXC_URL = "https://api.mexc.com/api/v3/ticker/price"
CMC_URL  = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"

# ====================

def send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID env vars")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": str(TELEGRAM_CHAT_ID).strip(), "text": text}

    r = requests.post(url, data=payload, timeout=10)
    print("Telegram response:", r.status_code, r.text)  # <-- IMPORTANT
    r.raise_for_status()

def get_price_from_mexc() -> float | None:
    try:
        r = requests.get(MEXC_URL, params={"symbol": f"{TOKEN_NAME}USDT"}, timeout=10)
        r.raise_for_status()
        return float(r.json()["price"])
    except Exception:
        return None

def get_price_from_cmc() -> float | None:
    if not CMC_API_KEY:
        return None
    try:
        r = requests.get(
            CMC_URL,
            params={"symbol": f"{TOKEN_NAME}", "convert": "USDT"},
            headers={"X-CMC_PRO_API_KEY": CMC_API_KEY, "Accept": "application/json"},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        return float(data["data"][f"{TOKEN_NAME}"]["quote"]["USDT"]["price"])
    except Exception:
        return None

def get_price() -> tuple[float | None, str]:
    p = get_price_from_mexc()
    if p is not None:
        return p, "mexc"
    p = get_price_from_cmc()
    if p is not None:
        return p, "coinmarketcap"
    return None, "none"

def main():
    last_alert_ts = 0

    # Startup ping
    send_telegram(f"✅ {TOKEN_NAME}/USDT watcher started (MEXC primary, CMC fallback).")

    while True:
        price, src = get_price()
        now = time.time()

        if price is None:
            # optional: alert if both sources fail (rate-limit it)
            print("Price unavailable from both MEXC and CMC.")
            time.sleep(POLL_SECONDS)
            continue

        print(f"[{src}] {TOKEN_NAME}/USDT = {price:.4f}")

        in_range = (MIN_P <= price <= MAX_P)
        if in_range and (now - last_alert_ts) >= COOLDOWN_SECONDS:
            msg = f"🚨 {TOKEN_NAME}/USDT in range {MIN_P}-{MAX_P}\nPrice: {price:.4f}\nSource: {src}"
            send_telegram(msg)
            last_alert_ts = now

        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
