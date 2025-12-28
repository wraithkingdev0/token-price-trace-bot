import os
import time
import requests
from collections import deque
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

# ====== CONFIG ======
MIN_P = float(os.getenv("MIN_P", "220"))
MAX_P = float(os.getenv("MAX_P", "230"))
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "15"))

# Cooldowns (seconds)
RANGE_COOLDOWN_SECONDS = int(os.getenv("RANGE_COOLDOWN_SECONDS", "300"))
RAPID_COOLDOWN_SECONDS = int(os.getenv("RAPID_COOLDOWN_SECONDS", "120"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")  # required
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")      # required

TOKEN_NAME = (os.getenv("TOKEN_NAME") or "").strip().upper()  # e.g., TAO
CMC_API_KEY = os.getenv("CMC_API_KEY")  # optional fallback if MEXC fails

# Rapid move rule: USD delta within N minutes
RAPID_USD_DELTA = float(os.getenv("RAPID_USD_DELTA", "5"))            # e.g. 5 (USD)
RAPID_WINDOW_MINUTES = float(os.getenv("RAPID_WINDOW_MINUTES", "2"))  # e.g. 2 (minutes)
RAPID_WINDOW_SEC = int(RAPID_WINDOW_MINUTES * 60)

# Timezone from env: UTC / GMT-4 / GMT+8
TIMEZONE_ENV = (os.getenv("TIMEZONE", "UTC") or "UTC").strip().upper()

MEXC_URL = "https://api.mexc.com/api/v3/ticker/price"
CMC_URL  = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"

# Store (timestamp, price) - timestamps are epoch seconds
history = deque()  # oldest at left


# ---------- Timezone helpers ----------
def _tzinfo_from_env(tz_env: str):
    if tz_env == "UTC":
        return timezone.utc

    if tz_env.startswith("GMT"):
        # Support GMT+H and GMT-H
        # Examples: GMT+8, GMT-4
        rest = tz_env.replace("GMT", "")
        if not rest:
            return timezone.utc

        sign = 1
        if rest.startswith("+"):
            sign = 1
            rest = rest[1:]
        elif rest.startswith("-"):
            sign = -1
            rest = rest[1:]
        else:
            # "GMT4" (rare) treat as +4
            sign = 1

        try:
            hours = int(rest)
            return timezone(timedelta(hours=sign * hours))
        except ValueError:
            return timezone.utc

    return timezone.utc


TZINFO = _tzinfo_from_env(TIMEZONE_ENV)


def fmt_ts(epoch_seconds: float) -> str:
    return datetime.fromtimestamp(epoch_seconds, TZINFO).strftime("%Y-%m-%d %H:%M:%S")


# ---------- Telegram ----------
def send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID env vars")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": str(TELEGRAM_CHAT_ID).strip(), "text": text}
    r = requests.post(url, data=payload, timeout=10)

    # Safe debug if something breaks
    if r.status_code != 200:
        print("Telegram error:", r.status_code, r.text)

    r.raise_for_status()


def notify(msg: str, also_telegram: bool = True) -> None:
    """Print to terminal and optionally send to Telegram."""
    print(msg)
    if also_telegram:
        send_telegram(msg)


# ---------- Price fetch ----------
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


# ---------- Rapid move check (multi-lag within window) ----------
def _nearest_price_at_or_before(target_ts: float) -> tuple[float, float] | None:
    """
    Find the most recent history point with ts <= target_ts.
    Returns (ts, price) or None if not found.
    Since history is small, linear scan from the right is fine.
    """
    for ts, price in reversed(history):
        if ts <= target_ts:
            return ts, price
    return None


def check_rapid_usd_move_multi(now: float, price: float) -> tuple[str, float, float] | None:
    """
    Checks rapid move against multiple checkpoints:
    now - POLL_SECONDS, now - 2*POLL_SECONDS, ... up to RAPID_WINDOW_SEC.
    Triggers if any checkpoint delta crosses threshold.

    Returns (message, best_delta, best_elapsed_seconds) or None.
    """
    # Add current point
    history.append((now, price))

    # Prune old points (keep a bit more than window just in case)
    prune_before = now - (RAPID_WINDOW_SEC + POLL_SECONDS * 2)
    while history and history[0][0] < prune_before:
        history.popleft()

    # Need at least 2 points
    if len(history) < 2:
        return None

    # If window smaller than polling, still check at least 1 step
    step = max(POLL_SECONDS, 1)
    max_k = max(1, RAPID_WINDOW_SEC // step)

    best = None  # (abs_delta, signed_delta, elapsed, old_price, old_ts)
    for k in range(1, max_k + 1):
        target_ts = now - (k * step)
        found = _nearest_price_at_or_before(target_ts)
        if not found:
            continue
        old_ts, old_price = found
        delta = price - old_price
        abs_delta = abs(delta)
        elapsed = int(now - old_ts)

        if abs_delta >= RAPID_USD_DELTA:
            # pick the strongest move (largest abs delta)
            if best is None or abs_delta > best[0]:
                best = (abs_delta, delta, elapsed, old_price, old_ts)

    if not best:
        return None

    abs_delta, delta, elapsed, old_price, old_ts = best
    direction = "RISE" if delta > 0 else "FALL"
    emoji = "📈" if delta > 0 else "📉"

    msg = (
        f"{emoji} Rapid {direction} detected\n"
        f"From {old_price:.4f} → {price:.4f}\n"
        f"{'+' if delta > 0 else '-'}${abs_delta:.2f} in {elapsed}s\n"
        f"Time: {fmt_ts(now)} ({TIMEZONE_ENV})"
    )
    return msg, delta, elapsed


def main():
    if not TOKEN_NAME:
        raise RuntimeError("Missing TOKEN_NAME in .env (e.g., TOKEN_NAME=TAO)")

    last_range_alert_ts = 0.0
    last_rapid_alert_ts = 0.0
    last_rapid_dir = None  # "up" or "down" (helps reduce spam)

    startup = (
        f"✅ {TOKEN_NAME}/USDT watcher started\n"
        f"- Source: MEXC primary, CMC fallback\n"
        f"- Range alert: {MIN_P}-{MAX_P}\n"
        f"- Rapid alert: ${RAPID_USD_DELTA} in {RAPID_WINDOW_MINUTES} min\n"
        f"- Poll: {POLL_SECONDS}s\n"
        f"- TZ: {TIMEZONE_ENV}"
    )
    notify(startup, also_telegram=True)

    while True:
        price, src = get_price()
        now = time.time()
        ts_str = fmt_ts(now)

        if price is None:
            print(f"[{ts_str}] Price unavailable from both MEXC and CMC.")
            time.sleep(POLL_SECONDS)
            continue

        # Always print tick line with timestamp
        print(f"[{ts_str}] [{src}] {TOKEN_NAME}/USDT = {price:.4f}")

        # 1) Range alert
        in_range = (MIN_P <= price <= MAX_P)
        if in_range and (now - last_range_alert_ts) >= RANGE_COOLDOWN_SECONDS:
            msg = (
                f"Price: {price:.4f}\n"
                f"Time: {ts_str} ({TIMEZONE_ENV})\n"
                f"Source: {src}"
            )
            notify(msg, also_telegram=True)
            last_range_alert_ts = now

        # 2) Rapid move alert (multi-lag)
        rapid = check_rapid_usd_move_multi(now, price)
        if rapid:
            rapid_msg, delta, elapsed = rapid
            direction = "up" if delta > 0 else "down"

            # Cooldown + optional direction spam control
            if (now - last_rapid_alert_ts) >= RAPID_COOLDOWN_SECONDS:
                notify(rapid_msg + f"\nSource: {src}", also_telegram=True)
                last_rapid_alert_ts = now
                last_rapid_dir = direction
            else:
                # Still show it in terminal if you want visibility (no telegram spam)
                # Uncomment next line if you want terminal-only rapid alerts during cooldown:
                # print("[rapid suppressed by cooldown]", rapid_msg.replace("\n", " | "))
                pass

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
