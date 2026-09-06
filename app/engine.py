from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.ai.reviewer import AIReviewer
from app.config import Settings
from app.data.alpaca import AlpacaDataClient
from app.data.demo import DemoFeed
from app.execution.webull_readonly import WebullPositionMonitor, WebullReadOnlyClient
from app.market.state import MarketStateStore
from app.models import AICounterfactual, AIReview, DashboardState, DirectionScore, TradeAlert, TrackedTrade, TradeUpdate
from app.monitoring.tracker import TradeMonitor
from app.news.intelligence import NewsIntelligence
from app.notifications.telegram import TelegramNotifier
from app.options.scorer import OptionScorer
from app.risk.exit_plan import ExitPlanner
from app.signals.scorer import SignalScorer
from app.storage.db import AlertRepository


@dataclass
class _AIPrecheckEntry:
    created_at: datetime
    expires_at: datetime
    vector: dict
    fingerprint: str
    review: AIReview
    contract_symbol: str | None = None
    option_ask: float | None = None
    option_spread_pct: float | None = None


class TradingEngine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.state_store = MarketStateStore(settings.strategy)
        self.scorer = SignalScorer(settings.strategy)
        self.option_scorer = OptionScorer(settings.strategy)
        self.exit_planner = ExitPlanner(settings.strategy)
        self.trade_monitor = TradeMonitor(settings.strategy)
        self.ai = AIReviewer(settings.openai_api_key, settings.openai_model, settings.openai_reasoning_effort, settings.openai_timeout_seconds)
        self.repo = AlertRepository(settings.database_path)
        self.telegram = TelegramNotifier(
            settings.telegram_bot_token, self.repo, settings.enable_telegram,
            settings.telegram_bootstrap_chat_id, settings.public_base_url,
        )
        self.news = NewsIntelligence(settings, settings.strategy, settings.symbols, on_news=self._on_high_news)
        self.webull_client = WebullReadOnlyClient(
            settings.webull_app_key, settings.webull_app_secret, settings.webull_account_id,
            settings.webull_api_endpoint, settings.webull_access_token, settings.webull_option_snapshot_path,
        )
        self.webull = WebullPositionMonitor(self.webull_client, settings.webull_sync_enabled, settings.webull_poll_seconds)
        self.latest = {}
        self.latest_scores: dict[str, dict[str, DirectionScore]] = {}
        self.alerts: list[TradeAlert] = self.repo.recent(60)
        # V1.3.3 no longer runs watch-only/shadow positions. Ignore any old V1.3.2
        # shadow trackers that may still exist in a migrated SQLite database.
        self.tracked_trades: list[TrackedTrade] = [t for t in self.repo.tracked(60) if not t.shadow]
        self._manual_macro_mode: str | None = None
        self.macro_risk_mode = settings.strategy["macro"]["default_mode"]
        self.macro_risk_reason = "No active macro/news restriction."
        self.cooldowns: dict[str, datetime] = {}
        self.ai_reject_cooldowns: dict[str, datetime] = {}
        self.ai_prechecks: dict[str, _AIPrecheckEntry] = {}
        self.ai_precheck_tasks: dict[str, asyncio.Task] = {}
        self.ai_precheck_error_until: dict[str, datetime] = {}
        # Internal-only research counterfactuals for AI-vetoed trades. They are
        # never exposed as alerts, trackers or Telegram notifications.
        self.ai_counterfactuals: list[AICounterfactual] = self.repo.ai_counterfactuals(500, active_only=True)
        self.alert_count_by_date: dict[str, int] = defaultdict(int)
        self._restore_limits_from_history()
        self.task: asyncio.Task | None = None
        self.fast_risk_task: asyncio.Task | None = None
        self.demo: DemoFeed | None = None
        self.alpaca: AlpacaDataClient | None = None

    @staticmethod
    def _risk_rank(mode: str) -> int:
        return {"normal": 0, "caution": 1, "high": 2}.get(mode, 0)

    def _day_key(self, ts: datetime) -> str:
        return self.state_store.clock.localize(ts).date().isoformat()

    def _max_alerts_per_day(self) -> int | None:
        raw = self.settings.strategy["signals"].get("max_alerts_per_day", 0)
        try:
            value = int(raw or 0)
        except (TypeError, ValueError):
            value = 0
        return value if value > 0 else None

    def _daily_alert_cap_reached(self, day_key: str) -> bool:
        cap = self._max_alerts_per_day()
        return cap is not None and self.alert_count_by_date[day_key] >= cap

    def _ready_alerts_for_day(self, day_key: str) -> list[TradeAlert]:
        return [a for a in self.alerts if a.status == "READY" and self._day_key(a.created_at) == day_key]

    def _session_signal_alerts(self, symbol: str, day_key: str) -> list[TradeAlert]:
        """Only real READY recommendations count toward same-symbol repeat controls."""
        return sorted(
            [a for a in self.alerts if a.symbol == symbol and a.status == "READY" and self._day_key(a.created_at) == day_key],
            key=lambda a: a.created_at,
        )

    def _wins_today(self, day_key: str) -> int:
        wins = 0
        for trade in self.tracked_trades:
            if not trade.terminal or trade.shadow or trade.reference_pnl_pct is None or trade.reference_pnl_pct <= 0:
                continue
            alert = self._alert_by_id(trade.alert_id)
            if alert and alert.status == "READY" and self._day_key(alert.created_at) == day_key:
                wins += 1
        return wins

    def _profit_lock_active(self, day_key: str) -> bool:
        threshold = int(self.settings.strategy["signals"].get("profit_lock_after_wins", 3))
        return threshold > 0 and self._wins_today(day_key) >= threshold

    def _win_stop_reached(self, day_key: str) -> bool:
        threshold = int(self.settings.strategy["signals"].get("stop_after_wins", 4))
        return threshold > 0 and self._wins_today(day_key) >= threshold

    def _restore_limits_from_history(self):
        cooldown_minutes = int(self.settings.strategy["signals"].get("symbol_cooldown_minutes", 10))
        for alert in self.alerts:
            if alert.status != "READY":
                continue
            day_key = self._day_key(alert.created_at)
            self.alert_count_by_date[day_key] += 1
            until = alert.created_at + timedelta(minutes=cooldown_minutes)
            if self.cooldowns.get(alert.symbol) is None or until > self.cooldowns[alert.symbol]:
                self.cooldowns[alert.symbol] = until

    def _telegram_news_relevant(self, item) -> bool:
        """Standalone Telegram news pushes are limited to configured company symbols.

        Market-wide/macro news still influences the internal risk engine and may remain
        visible on the dashboard; it simply does not create a standalone Telegram push.
        """
        cfg = self.settings.strategy.get("news", {})
        allowed = {str(s).upper() for s in cfg.get("telegram_symbols", [])}
        aliases = {str(k).upper(): str(v).upper() for k, v in cfg.get("telegram_symbol_aliases", {}).items()}
        symbols = {aliases.get(str(s).upper(), str(s).upper()) for s in (item.symbols or [])}
        return bool(allowed.intersection(symbols))

    async def _on_high_news(self, item):
        if not self._telegram_news_relevant(item):
            return
        try:
            await self.telegram.send_news(item)
        except Exception as exc:
            print(f"Telegram news notification failed: {exc}")

    def _refresh_macro_risk(self, now: datetime | None = None):
        auto_mode, auto_reason = self.news.risk_mode(now)
        default = self.settings.strategy["macro"]["default_mode"]
        if self._risk_rank(default) > self._risk_rank(auto_mode):
            auto_mode, auto_reason = default, "Configured default market-risk floor."
        if self._manual_macro_mode and self._risk_rank(self._manual_macro_mode) > self._risk_rank(auto_mode):
            self.macro_risk_mode = self._manual_macro_mode
            self.macro_risk_reason = f"Manual admin override: {self._manual_macro_mode.upper()}. Automatic status: {auto_reason}"
        else:
            self.macro_risk_mode = auto_mode
            self.macro_risk_reason = auto_reason

    async def start(self):
        if self.task and not self.task.done():
            return
        await self.telegram.start()
        await self.news.start()
        await self.ai.start()
        await self.webull_client.start()
        await self.webull.start()
        if self.settings.data_mode == "live":
            self.alpaca = AlpacaDataClient(
                self.settings.alpaca_api_key, self.settings.alpaca_api_secret,
                self.settings.alpaca_stock_feed, self.settings.alpaca_option_feed,
            )
            bars = await self.alpaca.bootstrap_bars(self.settings.symbols)
            for bar in bars:
                self.state_store.add_bar(bar)
            await self._refresh_all()
            self.task = asyncio.create_task(self._run_live(), name="market-live")
            self.fast_risk_task = asyncio.create_task(self._run_fast_option_risk(), name="option-risk-fast")
        else:
            self.demo = DemoFeed(self.settings.symbols)
            for bar in self.demo.historical(120):
                self.state_store.add_bar(bar)
            await self._refresh_all()
            self.task = asyncio.create_task(self._run_demo(), name="market-demo")

    async def stop(self):
        for attr in ("task", "fast_risk_task"):
            task = getattr(self, attr)
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                setattr(self, attr, None)
        for task in list(self.ai_precheck_tasks.values()):
            if task and not task.done():
                task.cancel()
        if self.ai_precheck_tasks:
            await asyncio.gather(*self.ai_precheck_tasks.values(), return_exceptions=True)
        self.ai_precheck_tasks.clear()
        await self.webull.stop()
        await self.webull_client.close()
        await self.ai.close()
        await self.news.stop()
        await self.telegram.stop()

    async def _run_demo(self):
        assert self.demo
        async for bar in self.demo.stream():
            self.state_store.add_bar(bar)
            if bar.symbol == self.settings.symbols[-1]:
                await self._refresh_all()

    async def _run_live(self):
        assert self.alpaca
        async for bar in self.alpaca.stream_bars(self.settings.symbols):
            self.state_store.add_bar(bar)
            await self._evaluate(bar.symbol)

    async def _run_fast_option_risk(self):
        """Poll active option quotes between 1-minute bars so the -20% safety cap is not bar-bound."""
        seconds = max(5, int(self.settings.strategy.get("monitoring", {}).get("option_poll_seconds", 15)))
        while True:
            await asyncio.sleep(seconds)
            if not self.alpaca:
                continue
            active = [t for t in self.tracked_trades if not t.terminal and t.status not in {"DISMISSED", "EXPIRED"}]
            research = [x for x in self.ai_counterfactuals if not x.terminal]
            if not active and not research:
                continue
            contracts = {t.contract_symbol for t in active} | {x.contract_symbol for x in research}
            try:
                snapshots = await self.alpaca.get_option_snapshots(list(contracts))
            except Exception as exc:
                print(f"Fast option-risk poll failed: {exc}")
                continue
            now = datetime.now(timezone.utc)
            for trade in active:
                alert = self._alert_by_id(trade.alert_id)
                if not alert or not alert.contract:
                    continue
                alpaca_bid, alpaca_ask = self._quote_from_snapshot(snapshots.get(trade.contract_symbol))
                bid, ask = await self._preferred_option_quote(trade.contract_symbol, alpaca_bid, alpaca_ask)
                result = self.trade_monitor.evaluate_premium_risk(alert, trade, bid, ask, now)
                self.repo.save_tracked(result.trade)
                if result.trade.terminal:
                    self._record_outcome(result.trade)
                if result.update is not None and not result.trade.shadow:
                    try:
                        await self.telegram.edit_alert(alert, result.trade)
                        await self.telegram.send_trade_update(alert, result.trade, result.update)
                    except Exception as exc:
                        print(f"Telegram fast-risk update failed for {alert.symbol}: {exc}")
            if research:
                await self._update_ai_counterfactuals(snapshots, now)

    def _benchmark_bias(self, symbol: str) -> float:
        refs = ["SPY", "QQQ"] if symbol not in {"SPY", "QQQ", "IWM"} else [x for x in ["SPY", "QQQ", "IWM"] if x != symbol]
        vals = []
        for ref in refs:
            snap = self.latest.get(ref)
            if snap:
                vals.append(2.0 if snap.trend_5m == "bullish" else -2.0 if snap.trend_5m == "bearish" else 0.0)
        return sum(vals) / len(vals) if vals else 0.0

    async def _refresh_all(self):
        for symbol in self.settings.symbols:
            snap = self.state_store.snapshot(symbol)
            if snap:
                self.latest[symbol] = snap
        for symbol in self.settings.symbols:
            if symbol in self.latest:
                await self._evaluate(symbol, refresh_snapshot=False)

    def _threshold(self, session: str, score: DirectionScore, risk_mode: str) -> float:
        sig = self.settings.strategy["signals"]
        early = score.setup == "BREAKOUT_IGNITION" and score.early_entry_eligible
        if early:
            threshold = sig["breakout_ignition_prime_min_score"] if session == "prime" else sig["breakout_ignition_secondary_min_score"]
        else:
            threshold = sig["prime_min_score"] if session == "prime" else sig["secondary_min_score"]
        if risk_mode == "caution":
            threshold += self.settings.strategy["macro"]["caution_threshold_penalty"]
        elif risk_mode == "high":
            threshold += self.settings.strategy["macro"]["high_threshold_penalty"]
        return float(threshold)

    def _entry_reject_reason(self, score: DirectionScore) -> str | None:
        sig = self.settings.strategy["signals"]
        # V1.3.2: EXTENDED means no actionable entry for every setup, not only OR/ignition.
        if score.breakout_phase == "EXTENDED":
            return "EXTENDED breakout state: the move is too old/far to chase."
        if score.setup == "BREAKOUT_IGNITION" and (score.breakout_phase != "TRIGGERED" or not score.early_entry_eligible):
            return "Breakout ignition is not on a fresh TRIGGERED bar."
        max_chase = float(sig.get("max_chase_utilization", 1.0))
        if score.chase_utilization is not None and score.chase_utilization >= max_chase:
            return f"Entry has consumed {score.chase_utilization*100:.0f}% of the trigger-to-no-chase allowance (limit {max_chase*100:.0f}%)."
        if not score.regime_eligible:
            return "Choppy higher-timeframe regime blocks breakout/momentum entries."
        return None


    def _etf_reject_reason(self, symbol: str, snap, chosen: DirectionScore, other: DirectionScore, risk_mode: str = "normal") -> str | None:
        """Apply a stricter confirmation layer to SPY/QQQ/IWM-style ETF setups."""
        sig = self.settings.strategy["signals"]
        etfs = {str(s).upper() for s in sig.get("etf_symbols", [])}
        if symbol.upper() not in etfs:
            return None

        min_score = float(sig.get("etf_prime_min_score", 84) if snap.session == "prime" else sig.get("etf_secondary_min_score", 88))
        if risk_mode == "caution":
            min_score += float(self.settings.strategy["macro"].get("caution_threshold_penalty", 0))
        elif risk_mode == "high":
            min_score += float(self.settings.strategy["macro"].get("high_threshold_penalty", 0))
        if chosen.total < min_score:
            return f"ETF confirmation requires score >= {min_score:.0f} (got {chosen.total:.1f})."

        edge = chosen.total - other.total
        min_edge = float(sig.get("etf_min_directional_edge", 65))
        if edge < min_edge:
            return f"ETF confirmation requires directional edge >= {min_edge:.0f} (got {edge:.1f})."

        max_chase = float(sig.get("etf_max_chase_utilization", 0.60))
        if chosen.chase_utilization is not None and chosen.chase_utilization >= max_chase:
            return f"ETF setup is too late: {chosen.chase_utilization*100:.0f}% of chase allowance consumed (ETF limit {max_chase*100:.0f}%)."

        min_rvol = float(sig.get("etf_min_rvol", 1.25))
        if snap.relative_volume < min_rvol:
            return f"ETF confirmation requires RVOL >= {min_rvol:.2f}x (got {snap.relative_volume:.2f}x)."

        if bool(sig.get("etf_require_trending_regime", True)) and snap.market_regime != "TRENDING":
            return f"ETF setup requires TRENDING regime (current {snap.market_regime})."

        if bool(sig.get("etf_require_5m_15m_alignment", True)):
            aligned = "bullish" if chosen.direction == "CALL" else "bearish"
            if snap.trend_5m != aligned or snap.trend_15m != aligned:
                return f"ETF setup requires aligned 5m/15m {aligned} trend confirmation."
        return None

    def _repeat_symbol_reject_reason(self, symbol: str, day_key: str, now: datetime, chosen: DirectionScore, other: DirectionScore, snap) -> str | None:
        sig = self.settings.strategy["signals"]
        previous = self._session_signal_alerts(symbol, day_key)
        normal = int(sig.get("normal_ready_trades_per_symbol", 1))
        maximum = int(sig.get("max_ready_trades_per_symbol", 2))
        if len(previous) < normal:
            return None
        if len(previous) >= maximum:
            return f"Maximum {maximum} qualifying signal(s) for {symbol} already recorded this session."
        last = previous[-1]
        min_wait = int(sig.get("reentry_cooldown_minutes", 30))
        if now < last.created_at + timedelta(minutes=min_wait):
            return f"Re-entry requires at least {min_wait} minutes after the prior {symbol} signal."
        edge = chosen.total - other.total
        if chosen.total < float(sig.get("reentry_min_score", 92)) or edge < float(sig.get("reentry_min_edge", 70)):
            return "Second same-symbol trade requires exceptional score and directional edge."
        new_structure = False
        if chosen.trigger_underlying is not None and last.trigger_underlying is not None:
            new_structure = abs(chosen.trigger_underlying - last.trigger_underlying) >= float(sig.get("reentry_new_structure_atr", 0.30)) * max(snap.atr, 0.01)
        aligned = "bullish" if chosen.direction == "CALL" else "bearish"
        direction_reset = chosen.direction != last.direction and snap.trend_5m == aligned and snap.trend_15m == aligned
        if not (new_structure or direction_reset):
            return "Second same-symbol trade needs a materially new trigger level or confirmed higher-timeframe regime reset."
        return None

    def _profit_lock_reject_reason(self, day_key: str, chosen: DirectionScore, other: DirectionScore) -> str | None:
        sig = self.settings.strategy["signals"]
        if not self._profit_lock_active(day_key):
            return None
        edge = chosen.total - other.total
        if chosen.total < float(sig.get("profit_lock_min_score", 90)):
            return "Profit-lock mode requires a score of at least 90."
        if edge < float(sig.get("profit_lock_min_edge", 70)):
            return "Profit-lock mode requires at least a 70-point directional edge."
        allowed = set(sig.get("profit_lock_allowed_setups", []))
        if allowed and chosen.setup not in allowed:
            return f"Profit-lock mode does not allow {chosen.setup}."
        return None

    def _ai_required_for_ready(self) -> bool:
        """Strict AI mode is opt-in. AI disabled must never suppress deterministic READY trades."""
        return self.settings.data_mode == "live" and bool(self.settings.ai_required_for_ready)

    def _ai_policy(self) -> str:
        if not self.settings.enable_ai_review:
            return "disabled"
        return "strict" if self._ai_required_for_ready() else "optional"

    def _ai_reject_cooldown_active(self, symbol: str, now: datetime) -> bool:
        until = self.ai_reject_cooldowns.get(symbol)
        return bool(until and now < until)

    def _set_ai_reject_cooldown(self, symbol: str, now: datetime):
        minutes = int(self.settings.strategy.get("ai", {}).get("reject_cooldown_minutes", 10))
        if minutes > 0:
            self.ai_reject_cooldowns[symbol] = now + timedelta(minutes=minutes)

    def _ai_context(self, symbol: str, now: datetime, effective_risk: str, symbol_reason: str) -> dict:
        cfg = self.settings.strategy.get("ai", {})
        lookback = timedelta(minutes=int(cfg.get("news_lookback_minutes", 120)))
        aliases = {"GOOG": "GOOGL"}
        canonical = aliases.get(symbol.upper(), symbol.upper())
        news_items = []
        for item in self.news.items:
            if now - item.created_at > lookback:
                continue
            item_symbols = {aliases.get(str(x).upper(), str(x).upper()) for x in (item.symbols or [])}
            market_relevant = item.impact in {"HIGH", "CRITICAL"} and item.category in {"macro", "stock+macro", "market"}
            if canonical in item_symbols or market_relevant:
                news_items.append({
                    "created_at": item.created_at.isoformat(),
                    "headline": item.headline,
                    "source": item.source,
                    "impact": item.impact,
                    "symbols": item.symbols,
                    "category": item.category,
                    "reason": item.reason,
                })
            if len(news_items) >= int(cfg.get("max_news_items", 5)):
                break

        upcoming = []
        for event in self.news.upcoming(4):
            if event.event_at < now - timedelta(minutes=5):
                continue
            upcoming.append({
                "event_at": event.event_at.isoformat(),
                "name": event.name,
                "importance": event.importance,
                "source": event.source,
                "category": event.category,
                "symbols": event.symbols,
            })
            if len(upcoming) >= int(cfg.get("max_upcoming_events", 4)):
                break

        etfs = set(self.settings.strategy.get("signals", {}).get("etf_symbols", []))
        return {
            "is_etf": symbol in etfs,
            "effective_risk_mode": effective_risk,
            "symbol_risk_reason": symbol_reason,
            "market_risk_reason": self.macro_risk_reason,
            "recent_relevant_news": news_items,
            "upcoming_events": upcoming,
            "profit_lock_active": self._profit_lock_active(self._day_key(now)),
            "wins_today": self._wins_today(self._day_key(now)),
            "alerts_today": self.alert_count_by_date[self._day_key(now)],
        }

    def _ai_precheck_eligible(self, symbol: str, chosen: DirectionScore, other: DirectionScore) -> bool:
        cfg = self.settings.strategy.get("ai", {})
        if not bool(cfg.get("armed_precheck_enabled", True)):
            return False
        if not self.settings.enable_ai_review or not self.ai.configured:
            return False
        if chosen.breakout_phase != "ARMED":
            return False
        edge = chosen.total - other.total
        etfs = {str(x).upper() for x in self.settings.strategy.get("signals", {}).get("etf_symbols", [])}
        if symbol.upper() in etfs:
            return chosen.total >= float(cfg.get("armed_precheck_etf_min_score", 76)) and edge >= float(cfg.get("armed_precheck_etf_min_edge", 50))
        return chosen.total >= float(cfg.get("armed_precheck_min_score", 62)) and edge >= float(cfg.get("armed_precheck_min_edge", 30))

    def _ai_vector(self, snap, chosen: DirectionScore, other: DirectionScore, contract, effective_risk: str, symbol_reason: str) -> dict:
        context = self._ai_context(snap.symbol, snap.timestamp, effective_risk, symbol_reason)
        return self.ai.build_feature_vector(snap, chosen, other, contract, effective_risk, context)

    async def _run_ai_precheck(self, symbol: str, snap, chosen: DirectionScore, other: DirectionScore, effective_risk: str, symbol_reason: str):
        cfg = self.settings.strategy.get("ai", {})
        contract = None
        try:
            contract = await self._select_option(symbol, chosen.direction, snap.price)
            if contract and contract.option_score >= self.settings.strategy["options"]["min_option_score"]:
                contract, _ = await self._enrich_contract_with_webull(contract, snap.timestamp)
            else:
                contract = None
            vector = self._ai_vector(snap, chosen, other, contract, effective_risk, symbol_reason)
            fingerprint = self.ai.feature_fingerprint(vector)
            review = await self.ai.precheck(vector)
            review.feature_fingerprint = fingerprint
            review.precheck_option_ask = float(contract.ask) if contract is not None else None
            if review.verdict not in {"APPROVE", "VETO"}:
                backoff = max(5, int(cfg.get("precheck_error_backoff_seconds", 60)))
                self.ai_precheck_error_until[symbol] = datetime.now(timezone.utc) + timedelta(seconds=backoff)
                self._log_decision(
                    snap, chosen, other, "AI_PRECHECK_UNAVAILABLE",
                    f"AI ARMED-state precheck unavailable ({review.verdict}): {review.summary}; retry backed off for {backoff}s.",
                    effective_risk, option=contract, ai_review=review,
                )
                return
            created = review.reviewed_at or datetime.now(timezone.utc)
            ttl = int(cfg.get("precheck_ttl_seconds", 25))
            self.ai_prechecks[symbol] = _AIPrecheckEntry(
                created_at=created,
                expires_at=created + timedelta(seconds=ttl),
                vector=vector,
                fingerprint=fingerprint,
                review=review,
                contract_symbol=contract.contract_symbol if contract is not None else None,
                option_ask=float(contract.ask) if contract is not None else None,
                option_spread_pct=float(contract.spread_pct) if contract is not None else None,
            )
            decision = "AI_PRECHECK_APPROVE" if review.verdict == "APPROVE" else "AI_PRECHECK_VETO"
            self._log_decision(
                snap, chosen, other, decision,
                f"AI ARMED-state precheck {review.verdict}: {review.reason_code} - {review.summary}",
                effective_risk, option=contract, ai_review=review,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"AI precheck failed for {symbol}: {type(exc).__name__}: {exc}")
        finally:
            current = self.ai_precheck_tasks.get(symbol)
            if current is asyncio.current_task():
                self.ai_precheck_tasks.pop(symbol, None)

    def _maybe_schedule_ai_precheck(self, symbol: str, snap, chosen: DirectionScore, other: DirectionScore, effective_risk: str, symbol_reason: str):
        if not self._ai_precheck_eligible(symbol, chosen, other):
            return
        error_until = self.ai_precheck_error_until.get(symbol)
        if error_until and datetime.now(timezone.utc) < error_until:
            return
        # Avoid paying for AI work that cannot possibly become an actionable trade.
        day_key = self._day_key(snap.timestamp)
        if self._active_tracker_for_symbol(symbol) or self._win_stop_reached(day_key) or self._daily_alert_cap_reached(day_key):
            return
        cool = self.cooldowns.get(symbol)
        if cool and snap.timestamp < cool:
            return
        if self._profit_lock_reject_reason(day_key, chosen, other):
            return
        vector = self._ai_vector(snap, chosen, other, None, effective_risk, symbol_reason)
        fingerprint = self.ai.feature_fingerprint(vector)
        existing = self.ai_prechecks.get(symbol)
        now = datetime.now(timezone.utc)
        if existing and now <= existing.expires_at and existing.fingerprint == fingerprint:
            return
        task = self.ai_precheck_tasks.get(symbol)
        if task and not task.done():
            return
        self.ai_precheck_tasks[symbol] = asyncio.create_task(
            self._run_ai_precheck(
                symbol, snap.model_copy(deep=True), chosen.model_copy(deep=True), other.model_copy(deep=True),
                effective_risk, symbol_reason,
            ),
            name=f"ai-precheck-{symbol}",
        )

    async def _resolve_ai_final_review(self, snap, chosen: DirectionScore, other: DirectionScore, contract, effective_risk: str, symbol_reason: str) -> AIReview:
        """Reuse a fresh ARMED precheck when safe, otherwise perform a tiny micro/full check."""
        cfg = self.settings.strategy.get("ai", {})
        symbol = snap.symbol
        task = self.ai_precheck_tasks.get(symbol)
        if task and not task.done():
            wait_seconds = max(0.0, float(cfg.get("trigger_precheck_wait_ms", 350)) / 1000.0)
            if wait_seconds:
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=wait_seconds)
                except (asyncio.TimeoutError, Exception):
                    pass

        context = self._ai_context(symbol, snap.timestamp, effective_risk, symbol_reason)
        current_vector = self.ai.build_feature_vector(snap, chosen, other, contract, effective_risk, context)
        current_fp = self.ai.feature_fingerprint(current_vector)
        cached = self.ai_prechecks.get(symbol)
        now_wall = datetime.now(timezone.utc)

        if cached and cached.review.verdict in {"APPROVE", "VETO"} and cached.review.reviewed_at:
            age_ms = max(0, int((now_wall - cached.review.reviewed_at).total_seconds() * 1000))
            changes = self.ai.material_changes(
                cached.vector,
                current_vector,
                cached.contract_symbol,
                cached.option_ask,
                score_drop=float(cfg.get("precheck_score_drop_for_micro", 6)),
                edge_drop=float(cfg.get("precheck_edge_drop_for_micro", 10)),
                rvol_drop_fraction=float(cfg.get("precheck_rvol_drop_fraction_for_micro", 0.25)),
                option_ask_drift_pct=float(cfg.get("precheck_option_ask_drift_pct_for_micro", 0.06)),
            )
            fresh = now_wall <= cached.expires_at
            if fresh and cached.fingerprint == current_fp and not changes and cached.review.verdict == "APPROVE":
                review = cached.review.model_copy(deep=True)
                review.review_mode = "CACHE"
                review.cache_hit = True
                review.precheck_age_ms = age_ms
                review.feature_fingerprint = current_fp
                review.precheck_option_ask = cached.option_ask
                if cached.option_ask and contract.ask:
                    review.precheck_to_trigger_option_move_pct = (float(contract.ask) / float(cached.option_ask) - 1.0) * 100
                return review

            # An ARMED-state VETO is advisory context, not a final triggered-trade veto.
            # Once the setup actually TRIGGERS, force a small MICRO re-review because
            # momentum/volume/structure may have improved materially since ARMED.
            if cached.review.verdict == "VETO":
                changes = list(dict.fromkeys(["armed_precheck_veto_requires_trigger_recheck", *changes]))

            # Even an expired precheck remains useful as context for a very small
            # change-only review instead of starting from zero.
            if not fresh:
                changes = list(dict.fromkeys(["precheck_cache_expired", *changes]))
            elif cached.fingerprint != current_fp:
                changes = list(dict.fromkeys(["feature_fingerprint_changed", *changes]))
            review = await self.ai.micro_review(current_vector, cached.review, changes or ["material_state_change"])
            review.precheck_age_ms = age_ms
            review.feature_fingerprint = current_fp
            review.precheck_option_ask = cached.option_ask
            if cached.option_ask and contract.ask:
                review.precheck_to_trigger_option_move_pct = (float(contract.ask) / float(cached.option_ask) - 1.0) * 100
            return review

        review = await self.ai.review(
            snap, chosen, other, contract, effective_risk, self.macro_risk_reason, context,
        )
        review.feature_fingerprint = current_fp
        return review

    def _record_ai_counterfactual(self, snap, chosen: DirectionScore, other: DirectionScore, contract, review: AIReview):
        if review.verdict != "VETO" or contract is None or float(contract.ask or 0) <= 0:
            return
        # Avoid duplicate research candidates for the same contract within a short window.
        recent = [x for x in self.ai_counterfactuals if not x.terminal and x.symbol == snap.symbol and x.contract_symbol == contract.contract_symbol]
        if recent:
            return
        horizon = max(1, int(self.settings.strategy.get("ai", {}).get("counterfactual_horizon_minutes", 15)))
        now = snap.timestamp
        item = AICounterfactual(
            id=f"cf-{uuid.uuid4().hex[:12]}", created_at=now, expires_at=now + timedelta(minutes=horizon), updated_at=now,
            symbol=snap.symbol, direction=chosen.direction, contract_symbol=contract.contract_symbol,
            reference_option_ask=float(contract.ask), current_option_bid=contract.bid, current_option_ask=contract.ask,
            current_pnl_pct=0.0, max_pnl_pct=0.0, min_pnl_pct=0.0,
            ai_verdict=review.verdict, ai_reason_code=review.reason_code, ai_review_mode=review.review_mode,
            quant_score=float(chosen.total), directional_edge=float(chosen.total-other.total), setup=chosen.setup,
            invalidation_underlying=chosen.invalidation_underlying, target_underlying=chosen.target_underlying,
            reference_underlying=float(snap.price),
        )
        self.ai_counterfactuals.append(item)
        self.ai_counterfactuals = self.ai_counterfactuals[-500:]
        self.repo.save_ai_counterfactual(item)

    async def _update_ai_counterfactuals(self, snapshots: dict, now: datetime):
        active = [x for x in self.ai_counterfactuals if not x.terminal]
        for item in active:
            alpaca_bid, alpaca_ask = self._quote_from_snapshot(snapshots.get(item.contract_symbol))
            bid, ask = await self._preferred_option_quote(item.contract_symbol, alpaca_bid, alpaca_ask)
            if bid is not None and bid > 0:
                item.current_option_bid = bid
                item.current_option_ask = ask
                pnl = float(bid) / float(item.reference_option_ask) - 1.0
                item.current_pnl_pct = pnl
                item.max_pnl_pct = max(item.max_pnl_pct if item.max_pnl_pct is not None else pnl, pnl)
                item.min_pnl_pct = min(item.min_pnl_pct if item.min_pnl_pct is not None else pnl, pnl)
            item.updated_at = now
            latest = self.latest.get(item.symbol)
            terminal_reason = ""
            if latest is not None:
                px = float(latest.price)
                if item.direction == "CALL":
                    if item.invalidation_underlying is not None and px <= item.invalidation_underlying:
                        terminal_reason = "TECHNICAL_INVALIDATION"
                    elif item.target_underlying is not None and px >= item.target_underlying:
                        terminal_reason = "UNDERLYING_TARGET"
                else:
                    if item.invalidation_underlying is not None and px >= item.invalidation_underlying:
                        terminal_reason = "TECHNICAL_INVALIDATION"
                    elif item.target_underlying is not None and px <= item.target_underlying:
                        terminal_reason = "UNDERLYING_TARGET"
            if not terminal_reason and now >= item.expires_at:
                terminal_reason = "RESEARCH_HORIZON"
            if terminal_reason:
                item.terminal = True
                item.terminal_reason = terminal_reason
                pnl = item.current_pnl_pct
                item.outcome = "WIN" if pnl is not None and pnl > 0 else "LOSS" if pnl is not None and pnl < 0 else "FLAT"
            self.repo.save_ai_counterfactual(item)

    async def _enrich_contract_with_webull(self, contract, now: datetime):
        # Preserve the Alpaca quote for comparison even when Webull replaces bid/ask.
        contract.alpaca_bid = contract.alpaca_bid if contract.alpaca_bid is not None else contract.bid
        contract.alpaca_ask = contract.alpaca_ask if contract.alpaca_ask is not None else contract.ask
        contract.quote_source = contract.quote_source or "alpaca_indicative"
        if not self.settings.webull_quotes_enabled:
            return contract, None
        if not self.webull_client.market_data_configured:
            reason = "Webull option quotes enabled but Webull app key/secret/endpoint are incomplete."
            return contract, reason
        try:
            quote = await self.webull_client.get_option_snapshot(contract.contract_symbol)
        except Exception as exc:
            return contract, f"Webull option snapshot unavailable: {type(exc).__name__}: {exc}"
        if quote is None:
            return contract, "Webull option snapshot returned no valid bid/ask."

        max_age = int(self.settings.webull_max_quote_age_seconds)
        if quote.quote_time is not None:
            age = abs((now.astimezone(timezone.utc) - quote.quote_time.astimezone(timezone.utc)).total_seconds())
            if age > max_age:
                return contract, f"Webull option quote is stale ({age:.0f}s > {max_age}s)."

        old_mid = max((float(contract.alpaca_bid or 0) + float(contract.alpaca_ask or 0)) / 2, 0.01)
        new_mid = (quote.bid + quote.ask) / 2
        contract.webull_bid = quote.bid
        contract.webull_ask = quote.ask
        if quote.delta is not None:
            contract.delta = quote.delta
        if quote.gamma is not None:
            contract.gamma = quote.gamma
        if quote.theta is not None:
            contract.theta = quote.theta
        if quote.vega is not None:
            contract.vega = quote.vega
        if quote.iv is not None:
            contract.iv = quote.iv
        if quote.volume is not None:
            contract.volume = quote.volume
        repriced = self.option_scorer.reprice(contract, quote.bid, quote.ask)
        if repriced is None:
            return contract, "Webull option quote fails the configured price/spread/risk quality filters."
        contract = repriced
        contract.quote_source = "webull_openapi"
        contract.quote_timestamp = quote.quote_time or now.astimezone(timezone.utc)
        contract.quote_discrepancy_pct = abs(new_mid - old_mid) / old_mid * 100
        return contract, None

    async def _preferred_option_quote(self, contract_symbol: str, alpaca_bid: float | None, alpaca_ask: float | None):
        if self.settings.webull_quotes_enabled and self.webull_client.market_data_configured:
            try:
                q = await self.webull_client.get_option_snapshot(contract_symbol)
                if q and q.bid > 0 and q.ask >= q.bid:
                    return q.bid, q.ask
            except Exception:
                pass
        return alpaca_bid, alpaca_ask

    async def _post_ai_revalidate(self, snap, chosen: DirectionScore, contract):
        """Revalidate underlying and exact option quote after AI latency, before READY/Telegram."""
        checked_at = datetime.now(timezone.utc)
        current_underlying = float(snap.price)
        if self.settings.data_mode == "live":
            if self.alpaca is None:
                return contract, current_underlying, "Live Alpaca client unavailable for post-AI freshness check."
            try:
                current_underlying, _ = await self.alpaca.get_latest_trade(snap.symbol)
            except Exception as exc:
                return contract, current_underlying, f"Post-AI underlying refresh failed: {type(exc).__name__}: {exc}"

        trigger = chosen.trigger_underlying
        chase = chosen.chase_limit_underlying
        invalidation = chosen.invalidation_underlying
        if chosen.direction == "CALL":
            if trigger is not None and current_underlying < trigger:
                return contract, current_underlying, "Post-AI price fell back below the CALL trigger."
            if chase is not None and current_underlying > chase:
                return contract, current_underlying, "Post-AI price moved beyond the CALL no-chase level."
            if invalidation is not None and current_underlying <= invalidation:
                return contract, current_underlying, "Post-AI price crossed the CALL technical invalidation."
        else:
            if trigger is not None and current_underlying > trigger:
                return contract, current_underlying, "Post-AI price reclaimed above the PUT trigger."
            if chase is not None and current_underlying < chase:
                return contract, current_underlying, "Post-AI price moved beyond the PUT no-chase level."
            if invalidation is not None and current_underlying >= invalidation:
                return contract, current_underlying, "Post-AI price crossed the PUT technical invalidation."

        reviewed_ask = float(contract.ask)
        fresh_alpaca = None
        if self.alpaca is not None:
            try:
                snaps = await self.alpaca.get_option_snapshots([contract.contract_symbol])
                bid, ask = self._quote_from_snapshot(snaps.get(contract.contract_symbol))
                if bid is not None and ask is not None and bid > 0 and ask >= bid:
                    base = contract.model_copy(deep=True)
                    base.alpaca_bid = bid
                    base.alpaca_ask = ask
                    base.webull_bid = None
                    base.webull_ask = None
                    base.quote_source = "alpaca_indicative"
                    base.quote_timestamp = checked_at
                    fresh_alpaca = self.option_scorer.reprice(base, bid, ask)
            except Exception:
                fresh_alpaca = None

        refreshed = None
        webull_issue = None
        if self.settings.webull_quotes_enabled:
            if not self.webull_client.market_data_configured:
                webull_issue = "Webull quote configuration became unavailable during post-AI validation."
            else:
                try:
                    q = await self.webull_client.get_option_snapshot(contract.contract_symbol)
                    if q is None:
                        webull_issue = "Webull returned no post-AI option bid/ask."
                    else:
                        if q.quote_time is not None:
                            age = abs((checked_at - q.quote_time.astimezone(timezone.utc)).total_seconds())
                            if age > int(self.settings.webull_max_quote_age_seconds):
                                webull_issue = f"Webull post-AI quote is stale ({age:.0f}s)."
                        if webull_issue is None:
                            base = fresh_alpaca or contract.model_copy(deep=True)
                            if base.alpaca_bid is None:
                                base.alpaca_bid = contract.alpaca_bid
                            if base.alpaca_ask is None:
                                base.alpaca_ask = contract.alpaca_ask
                            refreshed = self.option_scorer.reprice(base, q.bid, q.ask)
                            if refreshed is None:
                                webull_issue = "Post-AI Webull quote fails option price/spread/risk filters."
                            else:
                                refreshed.webull_bid = q.bid
                                refreshed.webull_ask = q.ask
                                refreshed.quote_source = "webull_openapi"
                                refreshed.quote_timestamp = q.quote_time or checked_at
                                if refreshed.alpaca_bid is not None and refreshed.alpaca_ask is not None:
                                    old_mid = max((refreshed.alpaca_bid + refreshed.alpaca_ask) / 2, 0.01)
                                    refreshed.quote_discrepancy_pct = abs(((q.bid + q.ask) / 2) - old_mid) / old_mid * 100
                except Exception as exc:
                    webull_issue = f"Post-AI Webull quote failed: {type(exc).__name__}: {exc}"

            if refreshed is None and self.settings.webull_quotes_required_for_ready:
                return contract, current_underlying, webull_issue or "Required post-AI Webull quote unavailable."

        if refreshed is None:
            refreshed = fresh_alpaca
        if refreshed is None:
            return contract, current_underlying, "No fresh exact-option quote was available after AI review."
        if refreshed.option_score < self.settings.strategy["options"]["min_option_score"]:
            return refreshed, current_underlying, "Post-AI exact-option quote no longer meets the option-quality threshold."

        max_move = float(self.settings.strategy.get("ai", {}).get("post_ai_max_option_move_pct", 0.08))
        if reviewed_ask > 0 and abs(float(refreshed.ask) / reviewed_ask - 1.0) > max_move:
            return refreshed, current_underlying, f"Option ask moved more than {max_move*100:.0f}% while AI was reviewing; candidate is stale."
        return refreshed, current_underlying, None

    def _alert_by_id(self, alert_id: str) -> TradeAlert | None:
        return next((a for a in self.alerts if a.id == alert_id), None)

    def _active_tracker_for_symbol(self, symbol: str) -> TrackedTrade | None:
        return next((t for t in self.tracked_trades if t.symbol == symbol and not t.terminal and t.status not in {"DISMISSED", "EXPIRED"}), None)

    @staticmethod
    def _quote_from_snapshot(snapshot: dict | None) -> tuple[float | None, float | None]:
        if not snapshot:
            return None, None
        quote = snapshot.get("latestQuote") or snapshot.get("latest_quote") or {}
        bid = quote.get("bp", quote.get("bid_price"))
        ask = quote.get("ap", quote.get("ask_price"))
        try:
            return float(bid) if bid is not None else None, float(ask) if ask is not None else None
        except (TypeError, ValueError):
            return None, None

    def _log_decision(self, snap, chosen: DirectionScore, other: DirectionScore, decision: str, reason: str, risk_mode: str = "", option=None, alert_id: str | None = None, ai_review: AIReview | None = None):
        try:
            payload = {
                "session": snap.session,
                "price": snap.price,
                "market_regime": snap.market_regime,
                "trend_efficiency_5m": snap.trend_efficiency_5m,
                "vwap_crosses_recent": snap.vwap_crosses_recent,
                "trend_1m": snap.trend_1m,
                "trend_5m": snap.trend_5m,
                "trend_15m": snap.trend_15m,
                "vwap": snap.vwap,
                "rvol": snap.relative_volume,
                "volume_acceleration": snap.volume_acceleration,
                "rsi": snap.rsi,
                "macd_hist": snap.macd_hist,
                "macd_hist_change": snap.macd_hist_change,
                "atr": snap.atr,
                "chosen": chosen.model_dump(mode="json"),
                "opposite_total": other.total,
                "directional_edge": chosen.total - other.total,
                "risk_mode": risk_mode,
                "option": option.model_dump(mode="json") if option is not None else None,
                "ai_review": ai_review.model_dump(mode="json") if ai_review is not None else None,
            }
            self.repo.log_signal_observation(snap.timestamp, snap.symbol, chosen.direction, decision, reason, payload, alert_id)
        except Exception as exc:
            print(f"Research logging failed for {snap.symbol}: {exc}")

    def _record_outcome(self, trade: TrackedTrade):
        if not trade.terminal:
            return
        alert = self._alert_by_id(trade.alert_id)
        if alert:
            try:
                self.repo.save_trade_outcome(alert, trade)
            except Exception as exc:
                print(f"Outcome logging failed for {trade.symbol}: {exc}")

    async def _apply_webull_sync(self, trade: TrackedTrade, alert: TradeAlert, snap):
        if trade.shadow or not self.webull.enabled:
            return None
        pos = self.webull.by_contract(trade.contract_symbol)
        if pos:
            trade.webull_position_seen = True
            trade.webull_last_quantity = pos.quantity
            trade.webull_missing_polls = 0
            if pos.cost_price and pos.cost_price > 0 and (trade.entry_source != "confirmed_fill" or trade.confirmed_entry_option_price != pos.cost_price):
                trade.entry_source = "confirmed_fill"
                trade.confirmed_entry_option_price = round(pos.cost_price, 4)
                trade.last_message = f"Webull position detected; tracking uses observed average cost ${pos.cost_price:.2f}."
            return None
        if trade.webull_position_seen and not trade.terminal:
            trade.webull_missing_polls += 1
            if trade.webull_missing_polls >= 2:
                trade.status = "EXIT"
                trade.terminal = True
                trade.last_event = "WEBULL_POSITION_CLOSED"
                trade.last_event_at = snap.timestamp
                trade.last_message = "Webull position is no longer open."
                return TradeUpdate(
                    timestamp=snap.timestamp, event="WEBULL_POSITION_CLOSED", severity="INFO", headline="Webull position closed",
                    action="Tracking closed because the previously detected broker position is no longer open.",
                    reason="Read-only Webull synchronization observed the position as absent on repeated checks.",
                    underlying_price=snap.price, option_bid=trade.current_option_bid, option_ask=trade.current_option_ask,
                    reference_pnl_pct=trade.reference_pnl_pct,
                )
        return None

    async def _monitor_symbol(self, symbol: str, snap, scores: dict[str, DirectionScore]):
        trackers = [
            t for t in self.tracked_trades
            if t.symbol == symbol and not t.terminal and t.status not in {"DISMISSED", "EXPIRED"} and t.updated_at != snap.timestamp
        ]
        if not trackers:
            return
        live_snaps = {}
        if self.alpaca:
            try:
                live_snaps = await self.alpaca.get_option_snapshots([t.contract_symbol for t in trackers])
            except Exception:
                live_snaps = {}
        for trade in trackers:
            alert = self._alert_by_id(trade.alert_id)
            if not alert or not alert.contract:
                continue
            if self.demo:
                bid, ask = self.demo.tracked_option_quote(alert, snap.price)
            else:
                alpaca_bid, alpaca_ask = self._quote_from_snapshot(live_snaps.get(trade.contract_symbol))
                bid, ask = await self._preferred_option_quote(trade.contract_symbol, alpaca_bid, alpaca_ask)
            if bid is not None:
                trade.current_option_bid = bid
            if ask is not None:
                trade.current_option_ask = ask
            webull_update = await self._apply_webull_sync(trade, alert, snap)
            if webull_update is not None:
                trade.updated_at = snap.timestamp
                trade.updates.append(webull_update)
                trade.updates = trade.updates[-int(self.settings.strategy.get("monitoring", {}).get("max_update_history", 20)):]
                self.repo.save_tracked(trade)
                self._record_outcome(trade)
                if not trade.shadow:
                    try:
                        await self.telegram.edit_alert(alert, trade)
                        await self.telegram.send_trade_update(alert, trade, webull_update)
                    except Exception as exc:
                        print(f"Telegram Webull-close update failed for {alert.symbol}: {exc}")
                continue
            result = self.trade_monitor.evaluate(
                alert, trade, snap, scores[alert.direction], scores["PUT" if alert.direction == "CALL" else "CALL"], bid, ask,
            )
            self.repo.save_tracked(result.trade)
            if result.trade.terminal:
                self._record_outcome(result.trade)
            if result.update is not None and not result.trade.shadow:
                try:
                    await self.telegram.edit_alert(alert, result.trade)
                    await self.telegram.send_trade_update(alert, result.trade, result.update)
                except Exception as exc:
                    print(f"Telegram tracking update failed for {alert.symbol}: {exc}")

    async def _evaluate(self, symbol: str, refresh_snapshot: bool = True):
        if refresh_snapshot:
            snap = self.state_store.snapshot(symbol)
            if not snap:
                return
            self.latest[symbol] = snap
        snap = self.latest[symbol]
        self._refresh_macro_risk(snap.timestamp)
        scores = self.scorer.score(snap, self._benchmark_bias(symbol))
        self.latest_scores[symbol] = scores
        await self._monitor_symbol(symbol, snap, scores)
        if snap.session == "closed" or snap.bars_ready < self.settings.strategy["signals"]["min_bars_ready"]:
            return

        direction = "CALL" if scores["CALL"].total >= scores["PUT"].total else "PUT"
        chosen = scores[direction]
        other = scores["PUT" if direction == "CALL" else "CALL"]
        sig = self.settings.strategy["signals"]
        symbol_mode, symbol_reason = self.news.symbol_risk_mode(symbol, snap.timestamp)
        effective_risk = self.macro_risk_mode if self._risk_rank(self.macro_risk_mode) >= self._risk_rank(symbol_mode) else symbol_mode

        # V1.4.2 starts contextual AI work while a promising breakout is still
        # ARMED. This is deliberately non-blocking and never creates an alert.
        self._maybe_schedule_ai_precheck(symbol, snap, chosen, other, effective_risk, symbol_reason)

        reason = self._entry_reject_reason(chosen)
        if reason:
            self._log_decision(snap, chosen, other, "REJECT_ENTRY_FRESHNESS", reason, effective_risk)
            return
        threshold = self._threshold(snap.session, chosen, effective_risk)
        if chosen.total < threshold:
            self._log_decision(snap, chosen, other, "REJECT_SCORE", f"{chosen.total:.1f} < required {threshold:.1f}", effective_risk)
            return
        margin = sig["breakout_min_direction_margin"] if chosen.setup == "BREAKOUT_IGNITION" else sig["minimum_direction_margin"]
        edge = chosen.total - other.total
        if edge < margin:
            self._log_decision(snap, chosen, other, "REJECT_EDGE", f"Directional edge {edge:.1f} < required {margin:.1f}", effective_risk)
            return
        etf_reason = self._etf_reject_reason(symbol, snap, chosen, other, effective_risk)
        if etf_reason:
            self._log_decision(snap, chosen, other, "REJECT_ETF_CONFIRMATION", etf_reason, effective_risk)
            return
        if self._active_tracker_for_symbol(symbol):
            self._log_decision(snap, chosen, other, "REJECT_ACTIVE_SYMBOL", "An active tracker already exists for this symbol.", effective_risk)
            return

        now = snap.timestamp
        day_key = self._day_key(now)
        repeat_reason = self._repeat_symbol_reject_reason(symbol, day_key, now, chosen, other, snap)
        if repeat_reason:
            self._log_decision(snap, chosen, other, "REJECT_REPEAT_SYMBOL", repeat_reason, effective_risk)
            return
        cool = self.cooldowns.get(symbol)
        if cool and now < cool:
            self._log_decision(snap, chosen, other, "REJECT_COOLDOWN", f"Symbol cooldown remains active until {cool.isoformat()}.", effective_risk)
            return

        if self._win_stop_reached(day_key):
            self._log_decision(snap, chosen, other, "REJECT_WIN_STOP", f"Session stopped after {self._wins_today(day_key)} completed reference winners.", effective_risk)
            return
        if self._daily_alert_cap_reached(day_key):
            self._log_decision(snap, chosen, other, "REJECT_DAILY_CAP", "Daily READY recommendation cap reached.", effective_risk)
            return
        profit_reason = self._profit_lock_reject_reason(day_key, chosen, other)
        if profit_reason:
            self._log_decision(snap, chosen, other, "REJECT_PROFIT_LOCK", profit_reason, effective_risk)
            return

        ai_required = self._ai_required_for_ready()
        if ai_required and (not self.settings.enable_ai_review or not self.ai.configured):
            self._log_decision(snap, chosen, other, "REJECT_AI_UNAVAILABLE", "Live READY requires AI final verification, but ENABLE_AI_REVIEW/OpenAI credentials are not configured.", effective_risk)
            return
        if (ai_required or self.settings.enable_ai_review) and self._ai_reject_cooldown_active(symbol, now):
            self._log_decision(snap, chosen, other, "REJECT_AI_COOLDOWN", "Recent AI rejection cooldown is active for this symbol.", effective_risk)
            return

        contract = await self._select_option(symbol, direction, snap.price)
        if not contract or contract.option_score < self.settings.strategy["options"]["min_option_score"]:
            self._log_decision(snap, chosen, other, "REJECT_OPTION", "No option contract met the minimum option-quality requirement.", effective_risk, option=contract)
            return

        contract, webull_quote_issue = await self._enrich_contract_with_webull(contract, now)
        if webull_quote_issue and self.settings.webull_quotes_required_for_ready:
            self._log_decision(snap, chosen, other, "REJECT_WEBULL_QUOTE", webull_quote_issue, effective_risk, option=contract)
            return
        if contract.option_score < self.settings.strategy["options"]["min_option_score"]:
            self._log_decision(snap, chosen, other, "REJECT_OPTION_AFTER_WEBULL", "Broker-reconciled option quote no longer meets the option-quality threshold.", effective_risk, option=contract)
            return

        ai_review = AIReview()
        if self.settings.enable_ai_review:
            ai_review = await self._resolve_ai_final_review(snap, chosen, other, contract, effective_risk, symbol_reason)
            # Only an explicit model VETO is a trade-quality rejection in optional mode.
            # Infrastructure failures (timeout/quota/API/schema) are not trading evidence.
            if ai_review.verdict == "VETO":
                self._record_ai_counterfactual(snap, chosen, other, contract, ai_review)
                self._set_ai_reject_cooldown(symbol, now)
                reason = f"AI final gate VETO [{ai_review.reason_code}]: {ai_review.summary}"
                if webull_quote_issue:
                    reason += f" Quote note: {webull_quote_issue}"
                self._log_decision(snap, chosen, other, "REJECT_AI_FINAL", reason, effective_risk, option=contract, ai_review=ai_review)
                return
            if ai_review.verdict != "APPROVE":
                strict = ai_required or not self.settings.ai_fail_open_on_error
                reason = f"AI review unavailable ({ai_review.verdict}) [{ai_review.reason_code}]: {ai_review.summary}"
                if strict:
                    self._log_decision(snap, chosen, other, "REJECT_AI_UNAVAILABLE", reason, effective_risk, option=contract, ai_review=ai_review)
                    return
                # Optional-AI mode: do not convert an API problem into a market veto.
                # Continue with the already-qualified deterministic setup and record why.
                self._log_decision(snap, chosen, other, "AI_BYPASS_UNAVAILABLE", reason + " Deterministic setup allowed by optional-AI policy.", effective_risk, option=contract, ai_review=ai_review)
        elif ai_required:
            self._log_decision(snap, chosen, other, "REJECT_AI_UNAVAILABLE", "Strict AI mode is enabled but ENABLE_AI_REVIEW=false.", effective_risk, option=contract)
            return

        current_underlying = float(snap.price)
        if ai_review.verdict == "APPROVE":
            ai_review.underlying_before = float(snap.price)
            ai_review.option_ask_before = float(contract.ask)
            pre_revalidate_ask = float(contract.ask)
            contract, current_underlying, post_ai_issue = await self._post_ai_revalidate(snap, chosen, contract)
            ai_review.underlying_after = float(current_underlying)
            ai_review.option_ask_after = float(contract.ask) if contract is not None else None
            if ai_review.option_ask_after is not None and pre_revalidate_ask > 0:
                ai_review.option_move_during_review_pct = (ai_review.option_ask_after / pre_revalidate_ask - 1.0) * 100
            if post_ai_issue:
                self._set_ai_reject_cooldown(symbol, now)
                self._log_decision(snap, chosen, other, "REJECT_POST_AI_STALE", post_ai_issue, effective_risk, option=contract, ai_review=ai_review)
                return

        exit_plan = self.exit_planner.build(snap, chosen, other)
        ask = float(contract.ask)
        alert = TradeAlert(
            id=uuid.uuid4().hex[:12], created_at=now, symbol=symbol, session=snap.session, direction=direction,
            quant_score=chosen.total, opposite_score=other.total, setup=chosen.setup, reasons=chosen.reasons, underlying_price=current_underlying,
            confirmation=f"Maintain {direction.lower()} structure with price {'above' if direction=='CALL' else 'below'} VWAP ${snap.vwap:.2f}",
            invalidation_underlying=float(chosen.invalidation_underlying), target_underlying=float(chosen.target_underlying), contract=contract, ai_review=ai_review,
            take_profit_pct=exit_plan.primary_take_profit_pct, stretch_target_pct=exit_plan.stretch_take_profit_pct,
            emergency_stop_pct=exit_plan.premium_hard_stop_pct, premium_caution_pct=exit_plan.premium_caution_pct,
            conviction_label=exit_plan.conviction_label, directional_edge=exit_plan.directional_edge,
            score_explanation=exit_plan.score_explanation, pullback_hold_underlying=exit_plan.pullback_hold_underlying,
            pullback_hold_label=exit_plan.pullback_hold_label, key_level_price=exit_plan.key_level_price, key_level_label=exit_plan.key_level_label,
            breakout_target_underlying=exit_plan.breakout_target_underlying, exit_strategy=exit_plan.instructions,
            breakout_phase=chosen.breakout_phase, trigger_underlying=chosen.trigger_underlying, chase_limit_underlying=chosen.chase_limit_underlying,
            option_entry_reference=round(ask, 2), option_take_profit_price=round(ask*(1+exit_plan.primary_take_profit_pct), 2),
            option_stretch_price=round(ask*(1+exit_plan.stretch_take_profit_pct), 2) if exit_plan.stretch_take_profit_pct else None,
            option_caution_price=round(ask*(1-exit_plan.premium_caution_pct), 2),
            option_hard_stop_price=round(ask*(1-exit_plan.premium_hard_stop_pct), 2),
            chase_utilization=chosen.chase_utilization, market_regime=snap.market_regime,
            watch_only_reason="", status="READY",
        )
        self.alerts.insert(0, alert)
        self.alerts = self.alerts[:60]
        self.repo.save(alert)

        self.cooldowns[symbol] = now + timedelta(minutes=int(sig["symbol_cooldown_minutes"]))
        self.alert_count_by_date[day_key] += 1
        tracked = self.trade_monitor.start(alert, shadow=False)
        self.tracked_trades.insert(0, tracked)
        self.tracked_trades = self.tracked_trades[:60]
        self.repo.save_tracked(tracked)
        ai_note = f" AI: {ai_review.summary}" if ai_review.verdict == "APPROVE" else ""
        quote_note = f" Quote source: {contract.quote_source}."
        self._log_decision(snap, chosen, other, "READY", f"Qualifying recommendation sent.{quote_note}{ai_note}", effective_risk, option=contract, alert_id=alert.id)
        try:
            message_id = await self.telegram.send_alert(alert, tracked)
            if message_id:
                tracked.telegram_message_id = message_id
                self.repo.save_tracked(tracked)
        except Exception as exc:
            print(f"Telegram alert failed for {alert.symbol}: {exc}")

    async def _select_option(self, symbol: str, direction: str, price: float):
        if self.demo:
            ranked = self.option_scorer.rank(self.demo.option_candidates(symbol, direction, price), price)
            return ranked[0] if ranked else None
        assert self.alpaca
        cfg = self.settings.strategy["options"]
        raw = await self.alpaca.get_option_candidates(symbol, direction, price, cfg["min_dte"], cfg["max_dte"], cfg["strike_window_pct"])
        today = datetime.now().date()
        candidates = []
        for contract_symbol, snap in raw.items():
            candidate = self.option_scorer.from_snapshot(symbol, direction, contract_symbol, snap, today=today)
            if candidate:
                candidates.append(candidate)
        ranked = self.option_scorer.rank(candidates, price)
        return ranked[0] if ranked else None

    def set_macro_risk(self, mode: str):
        if mode == "auto":
            self._manual_macro_mode = None
            self._refresh_macro_risk()
            return
        if mode not in {"normal", "caution", "high"}:
            raise ValueError("mode must be auto, normal, caution, or high")
        self._manual_macro_mode = mode
        self._refresh_macro_risk()

    async def confirm_entry(self, alert_id: str, option_fill_price: float) -> TrackedTrade:
        if option_fill_price <= 0:
            raise ValueError("option_fill_price must be positive")
        trade = next((t for t in self.tracked_trades if t.alert_id == alert_id), None)
        if not trade:
            raise ValueError("tracked trade not found")
        if trade.shadow:
            raise ValueError("watch-only shadow trades cannot be confirmed as real entries")
        trade.entry_source = "confirmed_fill"
        trade.confirmed_entry_option_price = round(float(option_fill_price), 4)
        if trade.current_option_bid is not None:
            trade.reference_pnl_pct = trade.current_option_bid / trade.confirmed_entry_option_price - 1.0
            trade.max_reference_pnl_pct = trade.reference_pnl_pct
            trade.min_reference_pnl_pct = trade.reference_pnl_pct
        trade.last_message = f"Actual fill confirmed at ${option_fill_price:.2f}; monitoring now uses the entered price."
        self.repo.save_tracked(trade)
        alert = self._alert_by_id(alert_id)
        if alert:
            try:
                await self.telegram.edit_alert(alert, trade)
            except Exception as exc:
                print(f"Telegram fill confirmation edit failed for {trade.symbol}: {exc}")
        return trade

    def dashboard_state(self) -> DashboardState:
        self._refresh_macro_risk()
        session = next(iter(self.latest.values())).session if self.latest else "closed"
        now = datetime.now(timezone.utc)
        day_key = self._day_key(now)
        return DashboardState(
            mode=self.settings.data_mode, macro_risk_mode=self.macro_risk_mode, macro_risk_reason=self.macro_risk_reason, session=session,
            alerts_today=self.alert_count_by_date[day_key], max_alerts_today=self._max_alerts_per_day(), latest=self.latest,
            latest_scores=self.latest_scores, alerts=[a for a in self.alerts if a.status == "READY"],
            tracked_trades=[t for t in self.tracked_trades if not t.shadow][:30], latest_news=self.news.items[:20], upcoming_events=self.news.upcoming(24),
            telegram_subscribers=self.telegram.subscriber_count, telegram_bot_username=self.telegram.bot_username, news_connected=self.news.connected,
            economic_calendar_connected=self.news.calendar_connected, webull_sync_enabled=self.settings.webull_sync_enabled,
            webull_sync_connected=self.webull.state.connected, webull_sync_error=self.webull.state.error,
            webull_quotes_enabled=self.settings.webull_quotes_enabled, webull_quotes_required=self.settings.webull_quotes_required_for_ready,
            ai_review_enabled=self.settings.enable_ai_review, ai_review_required=self._ai_required_for_ready(), ai_policy=self._ai_policy(), ai_fail_open_on_error=self.settings.ai_fail_open_on_error, ai_model=self.settings.openai_model,
            wins_today=self._wins_today(day_key), profit_lock_active=self._profit_lock_active(day_key),
            watch_only_symbols=[],
        )
