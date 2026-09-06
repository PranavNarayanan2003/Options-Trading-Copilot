# Real-Time Options Trading Assistant

A real-time intraday options trading assistant built in Python that processes live market data, technical indicators, market structure, news, and option-chain data to identify and monitor high-conviction CALL and PUT opportunities.

The system combines a deterministic quantitative signal engine with option-contract selection, dynamic risk management, Telegram alerts, an interactive dashboard, optional Webull quote reconciliation, and optional AI-based final verification.

> **Recommendation-only system:** The application does not automatically place broker orders.

---

## Overview

The assistant continuously monitors:

* SPY
* QQQ
* IWM
* AAPL
* MSFT
* NVDA
* GOOGL
* AMZN
* META
* TSLA

It evaluates market conditions in real time and only generates a trade recommendation when a setup satisfies its technical, market-regime, option-quality, risk, and freshness requirements.

A recommendation includes the specific option contract to consider, together with entry and risk-management information.

Example output:

```text
NVDA CALL — HIGH

BUY TO OPEN
NVDA 195 CALL
Expiration: Sep 11
DTE: 4

Reference Entry: $1.72

TP1: $2.24
Stretch Target: $2.58
Caution: $1.55
Hard Premium Stop: $1.38

Technical Invalidation: $193.84
Do Not Chase Above: $195.67
```

---

## Architecture

```text
                 LIVE MARKET DATA
                        │
             ┌──────────┴──────────┐
             │                     │
          Alpaca                 News
             │                     │
             └──────────┬──────────┘
                        ▼
               MARKET STATE ENGINE
                        │
        EMA / VWAP / RSI / MACD / ATR
        RVOL / volume / market structure
        opening range / support / resistance
        1m / 5m / 15m trend
                        │
                        ▼
               SIGNAL SCORING ENGINE
                        │
          CALL score ↔ PUT score
                        │
                        ▼
               SETUP / REGIME FILTERS
                        │
          breakout freshness
          trend alignment
          ETF-specific confirmation
          chop detection
          no-chase filtering
          macro/event risk
                        │
                        ▼
                OPTION SELECTION
                        │
          expiration / strike / DTE
          liquidity / spread / delta
          option-quality scoring
                        │
                        ▼
              WEBULL RECONCILIATION
                   (optional)
                        │
                        ▼
                 AI VERIFICATION
                   (optional)
                        │
                        ▼
                FINAL FRESHNESS CHECK
                        │
                        ▼
                     READY
                        │
             ┌──────────┴──────────┐
             ▼                     ▼
         Telegram              Dashboard
             │
             ▼
       Live Trade Monitor
```

---

## Key Features

### Real-Time Technical Analysis

The signal engine processes live one-minute market data and evaluates:

* EMA 9 / 21 / 50
* VWAP
* RSI
* MACD and momentum acceleration
* ATR
* Relative volume
* Volume acceleration
* Opening-range levels
* Recent highs and lows
* Support and resistance
* 1-minute, 5-minute and 15-minute trend alignment

---

### Multi-Setup Signal Engine

The assistant detects several intraday trading structures, including:

* Breakout ignition
* EMA/VWAP momentum
* Opening-range breakouts
* VWAP reclaim and rejection
* Trend pullbacks
* Support/resistance reversals
* Failed breakouts

Each potential CALL and PUT receives an independent evidence score before additional quality filters are applied.

---

### Market Regime Detection

The system attempts to distinguish between:

```text
TRENDING
NEUTRAL
CHOPPY
```

using higher-timeframe movement, trend efficiency and repeated VWAP rotation.

Momentum and breakout setups can therefore be suppressed when the market is behaving more like a range than a trend.

---

### ETF-Specific Filtering

SPY, QQQ and IWM remain fully tradable but require stronger confirmation than individual equities.

ETF setups require:

* higher minimum evidence scores;
* stronger directional separation;
* sufficient relative volume;
* TRENDING market regime;
* aligned 5-minute and 15-minute direction;
* fresher entries with stricter no-chase requirements.

---

### Exact Option Contract Selection

Once an underlying setup qualifies, the assistant searches available option contracts and evaluates:

* CALL or PUT direction
* Strike
* Expiration
* 0–14 DTE
* Bid / ask
* Spread
* Delta
* Volume
* Moneyness
* Contract cost
* Option-quality score

The selected contract is included directly in the trade recommendation.

---

### Webull Quote Integration

Alpaca is used for market scanning and option discovery.

Webull OpenAPI can optionally provide the latest bid/ask for the exact selected option contract before the recommendation is sent.

```text
Alpaca
→ detect setup
→ identify candidate contracts

Webull
→ verify selected contract
→ reconcile bid / ask
```

This keeps the system aligned with the broker used for manual execution.

---

### Optional AI Final Verification

AI is an optional additional review layer rather than the primary trading engine.

The deterministic system first determines whether a setup qualifies.

If AI verification is enabled, it reviews contextual factors such as:

* CALL vs PUT evidence
* higher-timeframe trend coherence
* market regime
* momentum and volume participation
* breakout freshness
* option liquidity
* macro/event risk
* relevant company news
* Webull vs Alpaca quote consistency

The AI can approve or veto an already-qualified setup but cannot generate a trade independently or override the deterministic risk rules.

The assistant can also operate completely without AI:

```env
ENABLE_AI_REVIEW=false
```

---

### Dynamic Trade Monitoring

Once a recommendation becomes active, the assistant continues monitoring:

* underlying price;
* EMA/VWAP structure;
* directional score;
* momentum;
* relative volume;
* technical invalidation;
* key support/resistance;
* exact option premium.

Possible monitoring states include:

```text
TRACKING
CAUTION
TRAILING
PROTECT_PROFIT
TARGET_HIT
EXIT
```

Technical structure takes priority over mechanically waiting for a fixed percentage stop.

---

### Risk Management

The system includes:

* technical invalidation levels;
* approximately -10% premium caution zone;
* approximately -20% premium hard-risk cap;
* primary profit targets;
* stretch targets for stronger setups;
* no-chase levels;
* position-monitoring updates.

Recommendations are classified by conviction:

```text
VALID
HIGH
STRONG
EXCEPTIONAL
```

---

### Selective Session Management

The assistant is designed to prioritize quality over trade frequency.

Current session:

```text
09:30–10:30 ET   PRIME
10:30–11:30 ET   SECONDARY
11:30 onward      NO NEW TRADES
```

Additional controls include:

* maximum six READY trades per session;
* profit-lock mode after three completed winners;
* stop new recommendations after four completed winners;
* one normal trade per ticker;
* stricter requirements for repeat entries;
* per-symbol cooldowns.

---

### News and Macro Awareness

The application integrates:

* Alpaca market news;
* Federal Reserve feeds;
* BLS economic-release schedules;
* optional external economic-calendar data.

News does not create CALL or PUT direction.

Instead, major events can increase the evidence required before a trade is accepted.

Standalone Telegram news notifications are limited to major headlines concerning:

```text
AAPL
MSFT
NVDA
GOOGL
AMZN
META
TSLA
```

---

### Telegram Alerts

Telegram is used for actionable trade notifications and material position-management updates.

Multiple users can subscribe using:

```text
/start
```

or:

```text
/subscribe
```

The system does not send trade notifications for:

* ARMED setups;
* rejected candidates;
* stale entries;
* extended breakouts;
* research-only setups.

Only actual `READY` recommendations are pushed.

---

### Interactive Dashboard

The FastAPI web application provides a real-time view of:

* monitored symbols;
* indicator state;
* CALL / PUT evidence scores;
* market regime;
* breakout status;
* current recommendations;
* selected option contracts;
* trade monitoring;
* news and macro risk.

Local dashboard:

```text
http://127.0.0.1:8000
```

---

## Technology Stack

### Backend

* Python
* FastAPI
* AsyncIO
* WebSockets
* REST APIs

### Data & Integrations

* Alpaca Market Data API
* Webull OpenAPI
* Telegram Bot API
* OpenAI API (optional)
* Federal Reserve / BLS feeds

### Data & Persistence

* Pandas
* NumPy
* SQLite

### Infrastructure

* Docker
* Docker Compose
* Linux / cloud deployment

---

## Local Setup

### 1. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/real-time-options-trading-assistant.git
cd real-time-options-trading-assistant
```

### 2. Create a virtual environment

Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 3. Create the environment file

```powershell
Copy-Item .env.example .env
```

Add your API credentials to `.env`.

Never commit `.env` to GitHub.

---

## Minimal Configuration

```env
DATA_MODE=live

ALPACA_API_KEY=
ALPACA_API_SECRET=

ALPACA_STOCK_FEED=iex
ALPACA_OPTION_FEED=indicative

ENABLE_TELEGRAM=true
TELEGRAM_BOT_TOKEN=

ENABLE_NEWS=true

ENABLE_AI_REVIEW=false
AI_REQUIRED_FOR_READY=false
AI_FAIL_OPEN_ON_ERROR=true

ENABLE_WEBULL_QUOTES=false
ENABLE_WEBULL_SYNC=false

ENABLE_BROKER_EXECUTION=false
```

---

## Run Diagnostics

```powershell
.\.venv\Scripts\python.exe scripts\check_setup.py
```

---

## Run Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

---

## Start the Live Assistant

```powershell
.\.venv\Scripts\python.exe scripts\run_live.py
```

Open:

```text
http://127.0.0.1:8000
```

---

## Research & Performance Logging

The assistant stores detailed observations for later strategy evaluation, including:

* evidence scores;
* setup type;
* market regime;
* directional edge;
* RVOL;
* entry freshness;
* option quality;
* final outcome;
* maximum favorable excursion;
* maximum adverse excursion;
* AI approval/veto information when enabled.

Export research data with:

```powershell
.\.venv\Scripts\python.exe scripts\export_research_data.py
```

Example outputs:

```text
signal_observations.csv
trade_outcomes.csv
ai_veto_counterfactuals.csv
ai_effectiveness_summary.csv
```

This makes it possible to evaluate whether individual filters or the optional AI layer are actually improving performance rather than relying only on anecdotal results.

---

## Cloud Deployment

The project includes:

```text
Dockerfile
docker-compose.yml
```

and is designed to run as a single persistent cloud instance.

The service can be deployed to a Linux VM so that:

```text
Laptop off
VS Code closed
        ↓
cloud instance remains active
        ↓
market scanner runs
        ↓
Telegram alerts continue
```

SQLite data is stored in a persistent Docker volume and the container uses automatic restart behavior.

---

## Security

The repository intentionally excludes:

```text
.env
API credentials
Telegram tokens
Webull credentials
OpenAI credentials
runtime SQLite databases
Python virtual environments
```

Keep API credentials in `.env` or secure server-side environment variables.

---

## Limitations

* The system provides trading research and recommendations, not guaranteed outcomes.
* Evidence scores are not probabilities of winning.
* AI review strength is not a probability of winning.
* Market conditions can change between an alert and manual execution.
* Free/indicative option feeds may differ from executable broker prices.
* Webull market-data availability depends on OpenAPI permissions and subscriptions.
* The current system is designed for manual order execution.
* Automatic broker order placement is intentionally disabled.

---

## Disclaimer

This project is intended for educational, research and personal analytical use.

It does not constitute financial or investment advice, and historical or simulated performance does not guarantee future results.

Options trading involves significant risk and can result in the loss of the entire premium invested.
