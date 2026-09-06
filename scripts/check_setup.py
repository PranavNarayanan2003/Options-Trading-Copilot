from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_settings
from app.execution.webull_readonly import WebullReadOnlyClient
from app.ai.reviewer import AIReviewer


async def check_alpaca(s):
    if not s.alpaca_api_key or not s.alpaca_api_secret:
        print("[ALPACA] SKIP - add API credentials")
        return False, None
    headers = {"APCA-API-KEY-ID": s.alpaca_api_key, "APCA-API-SECRET-KEY": s.alpaca_api_secret}
    ok = True
    test_contract = None
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            r = await client.get("https://data.alpaca.markets/v2/stocks/SPY/trades/latest", headers=headers, params={"feed": s.alpaca_stock_feed})
            r.raise_for_status(); trade = r.json().get("trade", {})
            print(f"[ALPACA LIVE STOCK] OK - last SPY {s.alpaca_stock_feed.upper()} trade: ${trade.get('p')} at {trade.get('t')}")
        except Exception as exc:
            print(f"[ALPACA LIVE STOCK] FAIL - {exc}"); ok = False
        try:
            end = datetime.now(timezone.utc) - timedelta(minutes=16); start = end - timedelta(days=7)
            r = await client.get("https://data.alpaca.markets/v2/stocks/SPY/bars", headers=headers, params={"timeframe":"1Day","start":start.isoformat(),"end":end.isoformat(),"limit":10,"feed":s.alpaca_stock_feed,"adjustment":"raw"})
            r.raise_for_status(); bars = r.json().get("bars", [])
            if not bars: raise RuntimeError("request returned no historical bars")
            print(f"[ALPACA HISTORICAL] OK - {len(bars)} SPY daily bars; last close ${bars[-1].get('c')} at {bars[-1].get('t')}")
        except Exception as exc:
            print(f"[ALPACA HISTORICAL] FAIL - {exc}"); ok = False
        try:
            today = datetime.now(timezone.utc).date()
            r = await client.get("https://data.alpaca.markets/v1beta1/options/snapshots/SPY", headers=headers, params={"feed":s.alpaca_option_feed,"type":"call","expiration_date_gte":today.isoformat(),"expiration_date_lte":(today+timedelta(days=14)).isoformat(),"limit":5})
            r.raise_for_status(); snaps = r.json().get("snapshots", {})
            print(f"[ALPACA OPTIONS] OK - received {len(snaps)} {s.alpaca_option_feed} SPY option snapshots")
            if snaps:
                test_contract = next(iter(snaps))
                r2 = await client.get("https://data.alpaca.markets/v1beta1/options/snapshots", headers=headers, params={"symbols":test_contract,"feed":s.alpaca_option_feed,"limit":10})
                r2.raise_for_status()
                if test_contract not in r2.json().get("snapshots", {}): raise RuntimeError("exact-contract snapshot missing")
                print(f"[ALPACA TRACKING] OK - exact-contract snapshot available for {test_contract}")
        except Exception as exc:
            print(f"[ALPACA OPTIONS] FAIL - {exc}"); ok = False
    return ok, test_contract


async def check_openai(s):
    required = s.data_mode == "live" and bool(s.ai_required_for_ready)
    if not s.enable_ai_review:
        mode = "strict" if required else "deterministic"
        print(f"[OPENAI FINAL REVIEW] SKIP - ENABLE_AI_REVIEW=false; mode={mode}. Deterministic READY trades remain enabled unless strict AI mode is explicitly requested.")
        return not required
    if not s.openai_api_key:
        if required or not s.ai_fail_open_on_error:
            print("[OPENAI FINAL REVIEW] FAIL - OPENAI_API_KEY is missing and current AI policy blocks on unavailability")
            return False
        print("[OPENAI FINAL REVIEW] NOT READY - OPENAI_API_KEY is missing; optional-AI mode will fall back to deterministic READY trades")
        return False

    # Verify the actual structured Responses API path used by live trades, not just
    # GET /models. This catches quota/billing, timeout, schema and response-format issues.
    reviewer = AIReviewer(s.openai_api_key, s.openai_model, s.openai_reasoning_effort, s.openai_timeout_seconds)
    vector = {
        "symbol": "SPY", "is_etf": True, "direction": "CALL",
        "score": 90, "opposite_score": 20, "edge": 70,
        "score_components": {"trend": 24, "structure": 22, "volume": 13, "momentum": 14, "levels": 9, "market_context": 7},
        "setup": "BREAKOUT_IGNITION", "setup_score": 92,
        "breakout_phase": "ARMED", "chase_utilization": 0.20,
        "regime": "TRENDING", "trends": {"1m": "bullish", "5m": "bullish", "15m": "bullish"},
        "trend_efficiency_5m": 0.65, "recent_vwap_crosses": 1,
        "ema_alignment": "bullish", "fast_ema_slope_atr": 0.08,
        "price_vs_vwap_atr": 0.20, "ema_fast_medium_spread_atr": 0.12,
        "rsi": 60, "macd_hist_sign": "positive", "macd_acceleration_sign": "positive",
        "rvol": 1.6, "volume_acceleration": 1.3, "candle_body_ratio": 0.65, "close_location": 0.78,
        "risk_mode": "normal", "symbol_risk_reason": "", "market_risk_reason": "",
        "profit_lock_active": False, "wins_today": 0, "alerts_today": 0,
        "news": [], "upcoming_events": [], "option": None,
    }
    try:
        await reviewer.start()
        review = await reviewer.precheck(vector)
        if review.verdict not in {"APPROVE", "VETO"}:
            raise RuntimeError(f"structured review returned {review.verdict}: {review.summary} ({', '.join(review.risk_flags)})")
        print(f"[OPENAI STRUCTURED REVIEW] OK - {s.openai_model} returned {review.verdict} in {review.latency_ms}ms ({review.reason_code})")
        print(f"[OPENAI POLICY] {'STRICT' if required else 'OPTIONAL'} - " + ("AI availability is required for READY" if required else "explicit VETO blocks; API/timeout errors fall back to deterministic READY"))
        return True
    except Exception as exc:
        if required or not s.ai_fail_open_on_error:
            print(f"[OPENAI STRUCTURED REVIEW] FAIL - {exc}; current policy would block READY trades")
        else:
            print(f"[OPENAI STRUCTURED REVIEW] NOT READY - {exc}; optional-AI policy will NOT block deterministic READY trades")
        return False
    finally:
        await reviewer.close()


async def check_telegram(s):
    if not s.enable_telegram:
        print("[TELEGRAM] SKIP - ENABLE_TELEGRAM=false"); return False
    if not s.telegram_bot_token:
        print("[TELEGRAM] FAIL - token missing"); return False
    try:
        base = f"https://api.telegram.org/bot{s.telegram_bot_token}"
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{base}/getMe"); r.raise_for_status(); bot = r.json().get("result", {})
            print(f"[TELEGRAM BOT] OK - @{bot.get('username')}; users self-subscribe with /start")
            if s.telegram_bootstrap_chat_id:
                r = await client.post(f"{base}/sendMessage", json={"chat_id":s.telegram_bootstrap_chat_id,"text":"✅ AI Options Trading Copilot V1.4.2 setup test: Telegram is working."})
                r.raise_for_status(); print("[TELEGRAM BOOTSTRAP SEND] OK")
            else:
                print("[TELEGRAM BOOTSTRAP SEND] SKIP - no fixed chat ID required")
        return True
    except Exception as exc:
        print(f"[TELEGRAM] FAIL - {exc}"); return False


async def check_news(s):
    if not s.enable_news:
        print("[NEWS] SKIP - ENABLE_NEWS=false"); return False
    ok = True
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent":"AI-Options-Copilot/1.4"}) as client:
            fed = await client.get("https://www.federalreserve.gov/feeds/press_monetary.xml"); fed.raise_for_status()
            bls = await client.get("https://www.bls.gov/schedule/news_release/bls.ics"); bls.raise_for_status()
        print("[OFFICIAL MACRO SOURCES] OK - Federal Reserve RSS + BLS schedule reachable")
    except Exception as exc:
        print(f"[OFFICIAL MACRO SOURCES] FAIL - {exc}"); ok = False
    if s.alpaca_api_key and s.alpaca_api_secret:
        print("[ALPACA NEWS] CONFIGURED - WebSocket authenticates when live server starts")
    if s.trading_economics_api_key:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get("https://api.tradingeconomics.com/calendar/country/united%20states", params={"c":s.trading_economics_api_key,"importance":3,"f":"json"}); r.raise_for_status()
            print("[ECONOMIC/EARNINGS CALENDAR] OK - Trading Economics key accepted")
        except Exception as exc:
            print(f"[ECONOMIC/EARNINGS CALENDAR] FAIL - {exc}"); ok = False
    else:
        print("[ECONOMIC/EARNINGS CALENDAR] OPTIONAL - official Fed/BLS + Alpaca news still operate without it")
    print("[TELEGRAM NEWS POLICY] MAG 7 company pushes only; other macro/news remains internal risk context")
    return ok


async def check_webull(s, test_contract: str | None):
    client = WebullReadOnlyClient(s.webull_app_key, s.webull_app_secret, s.webull_account_id, s.webull_api_endpoint, s.webull_access_token, s.webull_option_snapshot_path)
    quotes_ok = True
    positions_ok = True

    if s.webull_quotes_enabled:
        if not client.market_data_configured:
            print("[WEBULL OPTION QUOTES] FAIL - App Key, App Secret and API endpoint are required")
            quotes_ok = False
        elif not test_contract:
            print("[WEBULL OPTION QUOTES] NOT VERIFIED - no current Alpaca test contract was available")
            quotes_ok = not s.webull_quotes_required_for_ready
        else:
            try:
                quote = await client.get_option_snapshot(test_contract)
                if not quote: raise RuntimeError("snapshot returned no usable bid/ask; confirm Webull OpenAPI OPRA entitlement")
                print(f"[WEBULL OPTION QUOTES] OK - {test_contract} bid/ask ${quote.bid:.2f}/${quote.ask:.2f}")
            except Exception as exc:
                print(f"[WEBULL OPTION QUOTES] FAIL - {exc}")
                quotes_ok = False
    else:
        print("[WEBULL OPTION QUOTES] SKIP - ENABLE_WEBULL_QUOTES=false")

    if s.webull_sync_enabled:
        if not client.positions_configured:
            print("[WEBULL POSITIONS] FAIL - Account ID is required in addition to App Key/Secret/endpoint")
            positions_ok = False
        else:
            try:
                positions = await client.get_positions()
                print(f"[WEBULL POSITIONS] OK - retrieved {len(positions)} open positions")
            except Exception as exc:
                print(f"[WEBULL POSITIONS] FAIL - {exc}")
                positions_ok = False
    else:
        print("[WEBULL POSITIONS] SKIP - ENABLE_WEBULL_SYNC=false")

    return quotes_ok and positions_ok


async def main():
    s = load_settings()
    print("AI Options Trading Copilot V1.4.2 - setup diagnostics")
    print(f"Python: {sys.version.split()[0]}")
    print(f"DATA_MODE: {s.data_mode}")
    print(f"Stock feed: {s.alpaca_stock_feed}")
    print(f"Option feed: {s.alpaca_option_feed}")
    print(f"Telegram enabled: {s.enable_telegram}")
    policy = "strict" if (s.enable_ai_review and s.ai_required_for_ready) else "optional" if s.enable_ai_review else "disabled/deterministic"
    print(f"AI final review enabled: {s.enable_ai_review} ({s.openai_model}, reasoning={s.openai_reasoning_effort}, timeout={s.openai_timeout_seconds:g}s, policy={policy})")
    print(f"Webull option quotes enabled: {s.webull_quotes_enabled}")
    print(f"Automatic news enabled: {s.enable_news}")
    print("-" * 76)
    alpaca_ok, test_contract = await check_alpaca(s)
    ai_ok = await check_openai(s)
    telegram_ok = await check_telegram(s)
    news_ok = await check_news(s)
    webull_ok = await check_webull(s, test_contract)
    print("-" * 76)
    print(f"Alpaca checks: {'PASS' if alpaca_ok else 'NOT READY'}")
    if not s.enable_ai_review and not s.ai_required_for_ready:
        ai_summary = "DISABLED (DETERMINISTIC MODE ACTIVE)"
    elif ai_ok:
        ai_summary = "PASS"
    elif not s.ai_required_for_ready and s.ai_fail_open_on_error:
        ai_summary = "NOT READY (OPTIONAL FALLBACK ACTIVE)"
    else:
        ai_summary = "NOT READY (BLOCKING IN CURRENT POLICY)"
    print(f"AI final-review check: {ai_summary}")
    print(f"Telegram check: {'PASS' if telegram_ok else 'NOT READY'}")
    print(f"News/macro check: {'PASS' if news_ok else 'PARTIAL/NOT READY'}")
    print(f"Webull read-only check: {'PASS' if webull_ok else 'NOT READY'}")
    print("Note: live Alpaca/Webull freshness is verified after run_live.py starts. AI-disabled mode does not make OpenAI calls.")


if __name__ == "__main__":
    asyncio.run(main())
