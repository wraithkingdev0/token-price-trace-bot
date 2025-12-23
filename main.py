import os
import time
import requests
from collections import deque
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone

load_dotenv()

# ====== CONFIG ======
MIN_P = float(os.getenv("MIN_P", "220"))
MAX_P = float(os.getenv("MAX_P", "230"))
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "15"))

# Separate cooldowns (seconds)
RANGE_COOLDOWN_SECONDS = int(os.getenv("RANGE_COOLDOWN_SECONDS", "300"))  # 5 min
RAPID_COOLDOWN_SECONDS = int(os.getenv("RAPID_COOLDOWN_SECONDS", "120"))  # 2 min

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")  # required
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")      # required

TOKEN_NAME = (os.getenv("TOKEN_NAME") or "").strip().upper()  # e.g., TAO
CMC_API_KEY = os.getenv("CMC_API_KEY")  # optional fallback if MEXC fails

# Rapid move rule: USD delta within N minutes
RAPID_USD_DELTA = float(os.getenv("RAPID_USD_DELTA", "5"))          # e.g., 5 (USD)
RAPID_WINDOW_MINUTES = float(os.getenv("RAPID_WINDOW_MINUTES", "2")) # e.g., 2 (minutes)
RAPID_WINDOW_SEC = int(RAPID_WINDOW_MINUTES * 60)

TIMEZONE_ENV = os.getenv("TIMEZONE", "UTC").upper()

MEXC_URL = "https://api.mexc.com/api/v3/ticker/price"
CMC_URL  = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"

# Store (timestamp, price) for rapid move checks
history = deque()  # oldest at left


def get_tzinfo():
    if TIMEZONE_ENV == "UTC":
        return timezone.utc

    if TIMEZONE_ENV.startswith("GMT"):
        sign = 1 if "+" in TIMEZONE_ENV else -1
        hours = int(TIMEZONE_ENV.replace("GMT", "").replace("+", "").replace("-", ""))
        return timezone(timedelta(hours=sign * hours))

    # fallback
    return timezone.utc

TZINFO = get_tzinfo()
print(f"tz info: {TZINFO}")

def send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID env vars")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": str(TELEGRAM_CHAT_ID).strip(), "text": text}
    r = requests.post(url, data=payload, timeout=10)

    # If Telegram errors, print the description safely (no token)
    if r.status_code != 200:
        print("Telegram error:", r.status_code, r.text)

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
            params={"symbol": TOKEN_NAME, "convert": "USDT"},
            headers={"X-CMC_PRO_API_KEY": CMC_API_KEY, "Accept": "application/json"},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        return float(data["data"][TOKEN_NAME]["quote"]["USDT"]["price"])
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


def check_rapid_usd_move(now: float, price: float) -> str | None:
    """
    Returns an alert message if price moved by >= RAPID_USD_DELTA
    within the last RAPID_WINDOW_SEC seconds.
    """
    # Add current point
    history.append((now, price))

    # Remove points older than window (keep a little extra safety margin)
    while history and (now - history[0][0]) > RAPID_WINDOW_SEC:
        history.popleft()

    if len(history) < 2:
        return None

    old_ts, old_price = history[0]
    delta = price - old_price
    elapsed = int(now - old_ts)

    if delta >= RAPID_USD_DELTA:
        return (
            f"📈 Rapid RISE detected\n"
            f"{TOKEN_NAME}/USDT: +${delta:.2f} in {elapsed}s\n"
            f"From {old_price:.4f} → {price:.4f}\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
    if delta <= -RAPID_USD_DELTA:
        return (
            f"📉 Rapid FALL detected\n"
            f"{TOKEN_NAME}/USDT: -${abs(delta):.2f} in {elapsed}s\n"
            f"From {old_price:.4f} → {price:.4f}"
        )

    return None


def main():
    if not TOKEN_NAME:
        raise RuntimeError("Missing TOKEN_NAME in .env (e.g., TOKEN_NAME=TAO)")

    last_range_alert_ts = 0.0
    last_rapid_alert_ts = 0.0

    # Startup ping
    send_telegram(
        f"✅ {TOKEN_NAME}/USDT watcher started\n"
        f"- Source: MEXC primary, CMC fallback\n"
        f"- Range alert: {MIN_P}-{MAX_P}\n"
        f"- Rapid alert: ${RAPID_USD_DELTA} in {RAPID_WINDOW_MINUTES} min"
    )

    while True:
        price, src = get_price()
        now = time.time()
        ts = datetime.now(TZINFO).strftime("%Y-%m-%d %H:%M:%S")

        if price is None:
            print("Price unavailable from both MEXC and CMC.")
            time.sleep(POLL_SECONDS)
            continue

        print(f"[{ts}] [{src}] {TOKEN_NAME}/USDT = {price:.4f}")

        # 1) Range alert
        in_range = (MIN_P <= price <= MAX_P)
        if in_range and (now - last_range_alert_ts) >= RANGE_COOLDOWN_SECONDS:
            msg = (
                f"🚨 {TOKEN_NAME}/USDT in range {MIN_P}-{MAX_P}\n"
                f"Price: {price:.4f}\n"
                f"Time: {ts}\n"
                f"Source: {src}"
            )
            send_telegram(msg)
            last_range_alert_ts = now

        # 2) Rapid move alert ($ delta within N minutes)
        rapid_msg = check_rapid_usd_move(now, price)
        if rapid_msg and (now - last_rapid_alert_ts) >= RAPID_COOLDOWN_SECONDS:
            send_telegram(rapid_msg + f"\nSource: {src}")
            last_rapid_alert_ts = now

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
