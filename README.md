# AI Options Trading Copilot V1.4.2

Cloud-ready, recommendation-only intraday options assistant.

```text
Alpaca stock/options/news
        ↓
deterministic technical + regime + ETF + risk engine
        ↓
ARMED setup appears
        ↓
non-blocking AI precheck + likely contract/Webull quote
        ↓
short-lived cached review + feature fingerprint
        ↓
TRIGGERED setup passes all deterministic gates
        ↓
exact option selection + Webull quote reconciliation
        ↓
reuse cached AI approval if materially unchanged
        OR tiny MICRO review if context changed/expired
        OR compact FULL review if no precheck existed
        ↓
post-review underlying + option freshness revalidation
        ↓
READY dashboard + Telegram + live trade monitor
```

**No broker orders are placed.** `ENABLE_BROKER_EXECUTION=false` remains the safety default and no Webull order-submission method exists.

## V1.4.2 key behavior

- Python remains the only signal generator and owns all hard entry/risk rules.
- AI begins contextual analysis while a promising breakout is still `ARMED`; no ARMED notification is sent.
- ARMED AI approvals are cached for 25 seconds by default and tied to a material feature fingerprint.
- If the trigger arrives with the same material context and likely option contract, the cached AI approval is reused with no trigger-time AI round-trip.
- If material context changed or the cache expired, only a compact `MICRO` change-review is sent to AI.
- If a setup jumps directly to `TRIGGERED` with no usable precheck, a compact `FULL` review is used.
- AI responses are deliberately tiny: `APPROVE/VETO`, fixed reason code, short reason and review-strength.
- `confidence` is **review-strength only**. It is not a win probability and is no longer a hard READY threshold.
- Default OpenAI reasoning effort is `none` for the latency baseline. All actual AI latency is recorded for later comparison with `low` if desired.
- OpenAI and Webull reuse persistent HTTP keep-alive clients.
- After approval, the bot still refreshes the underlying and exact option quote before Telegram; stale/chased trades are rejected.
- AI-vetoed deterministic trades are tracked internally as silent research counterfactuals for outcome comparison. They never appear as alerts, trackers or Telegram messages.
- Alpaca discovers/ranks contracts; Webull can provide the selected exact OCC contract's live bid/ask.
- SPY, QQQ and IWM remain tradable but pass stricter ETF-specific confirmation.
- Standalone Telegram news pushes remain MAG 7 only; macro/general news still affects internal risk and AI context.

See `CHANGELOG_V1.4.2.md`, `MIGRATION_V1.4.0_TO_V1.4.2.md`, and `AI_WEBULL_SETUP.md`.

## Current trading profile

- Universe: SPY, QQQ, IWM, AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA.
- PRIME: 09:30–10:30 ET.
- SECONDARY: 10:30–11:30 ET.
- No new setup at/after 11:30 ET.
- Long calls/puts only.
- 0–14 DTE.
- Hard option ask ceiling $2.50; <= $2 preferred.
- Maximum 6 READY recommendations/session.
- Profit-lock after 3 completed winners; stop new recommendations after 4 completed winners.
- One normal trade per ticker, exceptional re-entry only after configured reset rules.
- Technical invalidation has priority over the premium hard-risk cap.

## ETF-specific filter

SPY/QQQ/IWM must pass the normal rules plus:

- PRIME evidence >= 84;
- SECONDARY evidence >= 88;
- directional edge >= 65;
- RVOL >= 1.25x;
- `TRENDING` market regime;
- aligned 5m and 15m trend;
- <60% chase utilization.

## Low-latency AI verification

`strategy.yaml` defaults include:

```yaml
ai:
  required_for_live_ready: false
  fail_open_on_error: true
  reject_cooldown_minutes: 10
  minimum_approval_confidence: 0.00
  post_ai_max_option_move_pct: 0.08

  armed_precheck_enabled: true
  armed_precheck_min_score: 62
  armed_precheck_min_edge: 30
  armed_precheck_etf_min_score: 76
  armed_precheck_etf_min_edge: 50
  precheck_ttl_seconds: 25
  trigger_precheck_wait_ms: 350

  precheck_score_drop_for_micro: 6
  precheck_edge_drop_for_micro: 10
  precheck_rvol_drop_fraction_for_micro: 0.25
  precheck_option_ask_drift_pct_for_micro: 0.06
  counterfactual_horizon_minutes: 15
```

Recommended live `.env`:

```env
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.6-terra
OPENAI_REASONING_EFFORT=none
OPENAI_TIMEOUT_SECONDS=3
ENABLE_AI_REVIEW=true
```

The AI receives a compact feature vector rather than the full raw technical snapshot. It focuses on:

- directional score/edge and setup quality;
- 1m/5m/15m coherence;
- regime and recent VWAP rotation;
- EMA/VWAP structure and momentum direction;
- RVOL/volume participation;
- entry freshness/chase context;
- likely/exact option liquidity and quote source;
- relevant macro/news/event context.

Fixed reason codes include `CLEAN_ALIGNMENT`, `HIGHER_TIMEFRAME_CONFLICT`, `CHOP_RISK`, `LATE_ENTRY`, `WEAK_PARTICIPATION`, `EVENT_RISK`, `OPTION_QUALITY`, `QUOTE_CONFLICT`, `MOMENTUM_DIVERGENCE`, and `STRUCTURE_CONFLICT`.

After approval, the bot refreshes the underlying and exact option quote. It rejects the candidate if the trigger was lost, the no-chase/invalidation boundary was crossed, option quality deteriorated, or the option ask moved >8% during the final review/freshness window.

## Webull read-only option quotes

```env
ENABLE_WEBULL_QUOTES=true
WEBULL_QUOTES_REQUIRED_FOR_READY=false
WEBULL_APP_KEY=...
WEBULL_APP_SECRET=...
WEBULL_API_ENDPOINT=api.webull.com
WEBULL_ACCESS_TOKEN=
WEBULL_OPTION_SNAPSHOT_PATH=/market-data/options/snapshots/list
WEBULL_MAX_QUOTE_AGE_SECONDS=20
```

Use `WEBULL_QUOTES_REQUIRED_FOR_READY=true` only after `scripts/check_setup.py` confirms your Webull OpenAPI options quote entitlement works.

Optional owner-position synchronization:

```env
ENABLE_WEBULL_SYNC=true
WEBULL_ACCOUNT_ID=...
WEBULL_POLL_SECONDS=15
```

See `WEBULL_SETUP.md`.

## Local Windows setup

```powershell
cd "C:\path\to\ai-options-trading-copilot-v1.4.2"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Configure `.env`, then run:

```powershell
.\.venv\Scripts\python.exe scripts\check_setup.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\run_live.py
```

Open `http://127.0.0.1:8000` locally. `/health` should report `version=1.4.2`, `mode=live`, `execution_enabled=false`, plus AI/Webull status fields.

## Telegram

One bot token supports multiple subscribers. Users send `/start` or `/subscribe`; chat IDs are stored in SQLite automatically.

Trade notifications are sent only for actual READY recommendations. Rejected/ARMED/EXTENDED/AI-vetoed/stale candidates are not pushed.

Standalone high-impact news pushes are restricted to AAPL, MSFT, NVDA, GOOGL/GOOG, AMZN, META and TSLA.

## Research logging and AI effectiveness

Signal observations record AI review mode, reason code, actual AI latency, token usage, precheck age, feature fingerprint, material changes, and option/underlying movement during the final verification window.

AI-vetoed candidates are tracked internally for 15 minutes by default using the selected exact contract. This research path is silent and is not exposed as a trade recommendation.

Export:

```powershell
.\.venv\Scripts\python.exe scripts\export_research_data.py
```

The export includes:

```text
signal_observations.csv
trade_outcomes.csv
ai_veto_counterfactuals.csv
ai_effectiveness_summary.csv
```

`ai_effectiveness_summary.csv` compares real AI-approved outcomes with AI-vetoed counterfactual outcomes and labels a cohort `PRELIMINARY` until at least 50 completed samples exist.

## Free cloud deployment

For an always-on deployment without paying for the VM, see `FREE_CLOUD_DEPLOYMENT.md`.

The Docker Compose service:

- persists SQLite under `/data`;
- uses `restart: unless-stopped`;
- binds dashboard port 8000 to server localhost only;
- is intended to run as one live replica.

Once the cloud instance is live, stop the local live process.

## Safety / limitations

- This is a research/recommendation system, not a guarantee of profit.
- Evidence score and AI review-strength are not calibrated probabilities of winning.
- AI can make mistakes; its role is conservative contextual verification, not autonomous trading.
- The 25-second cache is a latency optimization, not permission to ignore post-review freshness checks.
- Alpaca Basic IEX is partial-market stock data and Alpaca indicative options are not execution-grade OPRA quotes.
- Webull OpenAPI market-data permissions/subscriptions are separate from ordinary Webull app quote packages.
- Even a broker API quote can move before manual order entry.
- `ENABLE_BROKER_EXECUTION=false` must remain false for the current manual-execution architecture.
