# 💰 Crypto Price Watcher with Telegram Alerts

A Python script to monitor a cryptocurrency's price (e.g., BTC/USDT) and:

- ✅ Send Telegram alerts when price enters a defined **range**
- 🚨 Alert on **rapid price movement** (e.g., ±$0.50 within 30 seconds)
- 🌍 Supports **timezone localization** for alert timestamps
- 🔄 Fetches live data from **MEXC API**, with **CoinMarketCap fallback**

---

## 📦 Requirements

- Python 3.9+
- Internet access
- Telegram Bot + Chat ID
- (Optional) CoinMarketCap API Key

---

## 🛠️ Installation

1. **Clone the repo**
   ```bash
   git clone https://github.com/wraithkingdev0/token-price-trace-bot.git
   cd token-price-trace-bot
2. **Install dependencies**
    ```bash
    pip install -r requirements.txt
    ```

3. **Create .env file from template**

    ```bash
    cp .env.example .env
    ```

4. **Edit .env with your values (see below)**
    ```bash
    TELEGRAM_BOT_TOKEN=827...:AAG-6ib... # from @BotFather
    TELEGRAM_CHAT_ID=123456789 # from @UserInfeBot

    TOKEN_NAME=BTC
    MIN_P=80000
    MAX_P=100000
    POLL_SECONDS=10

    # Range alert cooldown
    RANGE_COOLDOWN_SECONDS=100

    # Rapid price change rule
    RAPID_USD_DELTA=0.5
    RAPID_WINDOW_MINUTES=0.5
    RAPID_COOLDOWN_SECONDS=10

    # Fallback to CoinMarketCap if MEXC fails
    CMC_API_KEY=a86....

    # Timezone (e.g. UTC, GMT+8, GMT-4)
    TIMEZONE=GMT
    ```

5. **Run the script**
    ```bash
    python main.py
    ```