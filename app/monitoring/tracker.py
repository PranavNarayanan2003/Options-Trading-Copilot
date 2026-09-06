from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo

from app.models import DirectionScore, TechnicalSnapshot, TradeAlert, TrackedTrade, TradeUpdate


@dataclass(frozen=True)
class MonitorResult:
    trade: TrackedTrade
    update: TradeUpdate | None


class TradeMonitor:
    """State machine for a recommended trade after the initial alert.

    It tracks the assistant's recommendation from the alert-time option ask by default.
    That is reference performance, not a claim about the user's real fill. If an actual
    fill is later confirmed, the same state machine uses that fill instead.
    """

    def __init__(self, strategy: dict):
        self.risk = strategy["risk"]
        self.monitor_cfg = strategy.get("monitoring", {})
        self.session_cfg = strategy.get("session", {})
        self.session_tz = ZoneInfo(self.session_cfg.get("timezone", "America/New_York"))
        self.session_end = time.fromisoformat(self.session_cfg.get("secondary_end", "11:30"))

    @staticmethod
    def start(alert: TradeAlert, shadow: bool = False) -> TrackedTrade:
        if not alert.contract:
            raise ValueError("cannot track an alert without a contract")
        c = alert.contract
        return TrackedTrade(
            alert_id=alert.id,
            created_at=alert.created_at,
            updated_at=alert.created_at,
            symbol=alert.symbol,
            direction=alert.direction,
            contract_symbol=c.contract_symbol,
            expiration=c.expiration,
            reference_entry_underlying=alert.underlying_price,
            reference_entry_option_price=c.ask,
            current_underlying=alert.underlying_price,
            current_option_bid=c.bid,
            current_option_ask=c.ask,
            reference_pnl_pct=(c.bid / c.ask - 1.0) if c.ask > 0 else None,
            max_reference_pnl_pct=(c.bid / c.ask - 1.0) if c.ask > 0 else None,
            min_reference_pnl_pct=(c.bid / c.ask - 1.0) if c.ask > 0 else None,
            high_water_underlying=alert.underlying_price,
            low_water_underlying=alert.underlying_price,
            last_event_at=alert.created_at,
            shadow=shadow,
        )

    @staticmethod
    def _direction_holds(direction: str, snap: TechnicalSnapshot) -> bool:
        if direction == "CALL":
            return snap.price >= snap.vwap and snap.ema_fast >= snap.ema_medium
        return snap.price <= snap.vwap and snap.ema_fast <= snap.ema_medium

    @staticmethod
    def _favorable_break(direction: str, price: float, level: float | None) -> bool:
        if level is None:
            return False
        return price > level if direction == "CALL" else price < level

    @staticmethod
    def _failed_invalidation(direction: str, price: float, invalidation: float) -> bool:
        return price <= invalidation if direction == "CALL" else price >= invalidation

    @staticmethod
    def _trailing_breached(direction: str, price: float, level: float | None) -> bool:
        if level is None:
            return False
        return price <= level if direction == "CALL" else price >= level

    @staticmethod
    def _near_key(direction: str, high: float, low: float, key: float | None, atr: float) -> bool:
        if key is None:
            return False
        tolerance = max(0.08 * atr, 0.01)
        return high >= key - tolerance if direction == "CALL" else low <= key + tolerance

    @staticmethod
    def _rejected_key(direction: str, snap: TechnicalSnapshot, trade: TrackedTrade, alert: TradeAlert) -> bool:
        key = alert.key_level_price
        if key is None:
            return False
        retrace = max(0.28 * snap.atr, 0.01)
        touched = TradeMonitor._near_key(direction, trade.high_water_underlying, trade.low_water_underlying, key, snap.atr)
        if not touched or trade.key_level_broken:
            return False
        if direction == "CALL":
            return snap.price < key - retrace and (snap.macd_hist <= 0 or snap.relative_volume < 1.0)
        return snap.price > key + retrace and (snap.macd_hist >= 0 or snap.relative_volume < 1.0)

    @staticmethod
    def _trail_level(direction: str, snap: TechnicalSnapshot, alert: TradeAlert) -> float:
        key = alert.key_level_price
        if direction == "CALL":
            candidates = [x for x in [snap.ema_fast, snap.vwap, key] if x is not None and x < snap.price]
            level = max(candidates) if candidates else snap.price - 0.45 * snap.atr
            return round(min(level, snap.price - 0.08 * snap.atr), 2)
        candidates = [x for x in [snap.ema_fast, snap.vwap, key] if x is not None and x > snap.price]
        level = min(candidates) if candidates else snap.price + 0.45 * snap.atr
        return round(max(level, snap.price + 0.08 * snap.atr), 2)

    def _update_obj(
        self, trade: TrackedTrade, snap: TechnicalSnapshot, event: str, severity: str,
        headline: str, action: str, reason: str,
    ) -> TradeUpdate:
        return TradeUpdate(
            timestamp=snap.timestamp,
            event=event,
            severity=severity,
            headline=headline,
            action=action,
            reason=reason,
            underlying_price=snap.price,
            option_bid=trade.current_option_bid,
            option_ask=trade.current_option_ask,
            reference_pnl_pct=trade.reference_pnl_pct,
            trailing_level_underlying=trade.trailing_level_underlying,
        )


    def evaluate_premium_risk(
        self,
        alert: TradeAlert,
        trade: TrackedTrade,
        option_bid: float | None,
        option_ask: float | None,
        timestamp,
    ) -> MonitorResult:
        """Fast quote-only safety pass used between 1-minute underlying bars.

        It intentionally does not infer technical structure from a stale candle. Its only
        terminal action is the configured hard premium cap, so the faster poll cannot
        create new discretionary exits from incomplete market data.
        """
        if trade.terminal:
            return MonitorResult(trade=trade, update=None)
        trade.updated_at = timestamp
        if option_bid is not None and option_bid > 0:
            trade.current_option_bid = option_bid
        if option_ask is not None and option_ask > 0:
            trade.current_option_ask = option_ask
        entry = trade.effective_entry_option_price
        if trade.current_option_bid is not None and entry > 0:
            trade.reference_pnl_pct = trade.current_option_bid / entry - 1.0
            trade.max_reference_pnl_pct = trade.reference_pnl_pct if trade.max_reference_pnl_pct is None else max(trade.max_reference_pnl_pct, trade.reference_pnl_pct)
            trade.min_reference_pnl_pct = trade.reference_pnl_pct if trade.min_reference_pnl_pct is None else min(trade.min_reference_pnl_pct, trade.reference_pnl_pct)
        pnl = trade.reference_pnl_pct
        if pnl is not None and pnl <= -float(alert.emergency_stop_pct):
            trade.status = "EXIT"
            trade.terminal = True
            trade.last_event = "PREMIUM_HARD_STOP"
            update = TradeUpdate(
                timestamp=timestamp, event="PREMIUM_HARD_STOP", severity="EXIT",
                headline="Hard premium risk cap reached",
                action="EXIT the contract; do not wait for a recovery.",
                reason=f"Reference premium is {pnl*100:.1f}% from the tracked entry versus the -{alert.emergency_stop_pct*100:.0f}% hard cap.",
                underlying_price=trade.current_underlying, option_bid=trade.current_option_bid, option_ask=trade.current_option_ask,
                reference_pnl_pct=trade.reference_pnl_pct, trailing_level_underlying=trade.trailing_level_underlying,
            )
            trade.last_event_at = timestamp
            trade.last_message = update.headline
            trade.updates.append(update)
            trade.updates = trade.updates[-int(self.monitor_cfg.get("max_update_history",20)):]
            return MonitorResult(trade=trade, update=update)
        return MonitorResult(trade=trade, update=None)

    def evaluate(
        self,
        alert: TradeAlert,
        trade: TrackedTrade,
        snap: TechnicalSnapshot,
        direction_score: DirectionScore,
        opposite_score: DirectionScore,
        option_bid: float | None,
        option_ask: float | None,
    ) -> MonitorResult:
        if trade.terminal:
            return MonitorResult(trade=trade, update=None)

        trade.updated_at = snap.timestamp
        trade.current_underlying = snap.price
        trade.high_water_underlying = max(trade.high_water_underlying, snap.price)
        trade.low_water_underlying = min(trade.low_water_underlying, snap.price)
        trade.current_option_bid = option_bid if option_bid and option_bid > 0 else trade.current_option_bid
        trade.current_option_ask = option_ask if option_ask and option_ask > 0 else trade.current_option_ask

        entry = trade.effective_entry_option_price
        if trade.current_option_bid is not None and entry > 0:
            trade.reference_pnl_pct = trade.current_option_bid / entry - 1.0
            trade.max_reference_pnl_pct = trade.reference_pnl_pct if trade.max_reference_pnl_pct is None else max(trade.max_reference_pnl_pct, trade.reference_pnl_pct)
            trade.min_reference_pnl_pct = trade.reference_pnl_pct if trade.min_reference_pnl_pct is None else min(trade.min_reference_pnl_pct, trade.reference_pnl_pct)

        pnl = trade.reference_pnl_pct
        direction_holds = self._direction_holds(alert.direction, snap)
        edge = direction_score.total - opposite_score.total
        strong_momentum = (
            direction_score.total >= self.monitor_cfg.get("trail_min_direction_score", 78)
            and edge >= self.monitor_cfg.get("trail_min_direction_edge", 18)
            and snap.relative_volume >= self.monitor_cfg.get("trail_min_rvol", 1.15)
            and direction_holds
        )

        update: TradeUpdate | None = None

        # 0) User rule: this is an intraday/scalp assistant, never silently carry yesterday's tracker.
        local_ts = snap.timestamp.astimezone(self.session_tz) if snap.timestamp.tzinfo else snap.timestamp.replace(tzinfo=ZoneInfo("UTC")).astimezone(self.session_tz)
        created_local = trade.created_at.astimezone(self.session_tz) if trade.created_at.tzinfo else trade.created_at.replace(tzinfo=self.session_tz)
        stale_day = local_ts.date() > created_local.date()
        session_cutoff = self.monitor_cfg.get("force_exit_at_session_end", True) and local_ts.time().replace(tzinfo=None) >= self.session_end
        if stale_day:
            trade.status = "EXPIRED"; trade.terminal = True; trade.last_event = "STALE_SESSION"
            update = self._update_obj(
                trade, snap, "STALE_SESSION", "EXIT", "Previous-session tracker closed",
                "Do not treat yesterday's intraday recommendation as an active trade today.",
                "The assistant was restarted on a later New York trading date; V1.2 does not carry scalp recommendations overnight.",
            )
        elif session_cutoff:
            trade.status = "EXIT"; trade.terminal = True; trade.last_event = "SESSION_CUTOFF"
            update = self._update_obj(
                trade, snap, "SESSION_CUTOFF", "EXIT", "Trading window ended",
                f"EXIT / do not carry the scalp beyond the {self.session_end.strftime('%H:%M')} ET trading cutoff.",
                f"Your configured trading window ends at {self.session_end.strftime('%H:%M')} ET, so the assistant closes the monitoring plan rather than turning an intraday scalp into a later-session hold.",
            )

        # 1) Technical thesis failure has priority over premium percentages.
        elif self._failed_invalidation(alert.direction, snap.price, alert.invalidation_underlying):
            trade.status = "EXIT"; trade.terminal = True; trade.last_event = "THESIS_INVALIDATED"
            update = self._update_obj(
                trade, snap, "THESIS_INVALIDATED", "EXIT", "Technical thesis invalidated",
                "EXIT / avoid re-entering until a new setup forms.",
                f"The underlying crossed the planned invalidation at ${alert.invalidation_underlying:.2f}.",
            )

        # 2) Hard premium cap is a last-resort safety net.
        elif pnl is not None and pnl <= -float(alert.emergency_stop_pct):
            trade.status = "EXIT"; trade.terminal = True; trade.last_event = "PREMIUM_HARD_STOP"
            update = self._update_obj(
                trade, snap, "PREMIUM_HARD_STOP", "EXIT", "Hard premium risk cap reached",
                "EXIT the contract; do not wait for a recovery.",
                f"Reference premium is {pnl*100:.1f}% from the tracked entry versus the -{alert.emergency_stop_pct*100:.0f}% hard cap.",
            )

        # 3) Trailing stop after a confirmed breakout/target continuation.
        elif trade.status == "TRAILING" and self._trailing_breached(alert.direction, snap.price, trade.trailing_level_underlying):
            trade.status = "EXIT"; trade.terminal = True; trade.last_event = "TRAILING_STOP"
            update = self._update_obj(
                trade, snap, "TRAILING_STOP", "EXIT", "Trailing structure failed",
                "EXIT / protect the winning trade.",
                f"Price lost the dynamic trailing level near ${trade.trailing_level_underlying:.2f}.",
            )

        # 4) Stretch objective reached. For a one-contract scalp this is a strong full-exit signal.
        elif alert.stretch_target_pct is not None and pnl is not None and pnl >= alert.stretch_target_pct:
            trade.stretch_target_reached = True; trade.status = "TARGET_HIT"; trade.terminal = True; trade.last_event = "STRETCH_TARGET_HIT"
            update = self._update_obj(
                trade, snap, "STRETCH_TARGET_HIT", "ACTION", "Stretch target reached",
                "Strongly consider taking the full profit on the one-contract position.",
                f"Reference option performance reached {pnl*100:.1f}%, above the +{alert.stretch_target_pct*100:.0f}% stretch zone.",
            )

        # 5) Primary target reached: trail only when the market still confirms continuation.
        elif not trade.primary_target_reached and pnl is not None and pnl >= alert.take_profit_pct:
            trade.primary_target_reached = True
            if alert.stretch_target_pct is not None and strong_momentum:
                trade.status = "TRAILING"; trade.last_event = "PRIMARY_TARGET_TRAIL"
                trade.trailing_level_underlying = self._trail_level(alert.direction, snap, alert)
                update = self._update_obj(
                    trade, snap, "PRIMARY_TARGET_TRAIL", "ACTION", "Primary target reached — trail the winner",
                    f"Do not use a fixed exit yet; trail the underlying near ${trade.trailing_level_underlying:.2f} while momentum holds.",
                    f"Reference premium reached {pnl*100:.1f}% and trend/RVOL still support the +{alert.stretch_target_pct*100:.0f}% stretch zone.",
                )
            else:
                trade.status = "TARGET_HIT"; trade.terminal = True; trade.last_event = "PRIMARY_TARGET_HIT"
                update = self._update_obj(
                    trade, snap, "PRIMARY_TARGET_HIT", "ACTION", "Primary target reached",
                    "Consider taking the full profit rather than forcing a larger target.",
                    f"Reference option performance reached {pnl*100:.1f}% and continuation conditions are not strong enough to justify the stretch plan.",
                )

        # 6) A technical wall breaks before the percentage target: switch to structural trailing.
        elif not trade.key_level_broken and self._favorable_break(alert.direction, snap.price, alert.key_level_price) and strong_momentum:
            trade.key_level_broken = True; trade.status = "TRAILING"; trade.last_event = "KEY_LEVEL_BREAK"
            trade.trailing_level_underlying = self._trail_level(alert.direction, snap, alert)
            update = self._update_obj(
                trade, snap, "KEY_LEVEL_BREAK", "ACTION", "Key level broke with confirmation",
                f"Stay with the move and trail using approximately ${trade.trailing_level_underlying:.2f}; do not mechanically sell at the original target.",
                f"{alert.key_level_label or 'The next technical level'} near ${alert.key_level_price:.2f} broke while direction score/RVOL remained supportive.",
            )

        # 7) Once trailing, ratchet the trail only in the favorable direction; no notification for every small change.
        elif trade.status == "TRAILING":
            candidate = self._trail_level(alert.direction, snap, alert)
            old = trade.trailing_level_underlying
            if old is None:
                trade.trailing_level_underlying = candidate
            elif alert.direction == "CALL":
                trade.trailing_level_underlying = max(old, candidate)
            else:
                trade.trailing_level_underlying = min(old, candidate)

            # A material loss of momentum while profitable is actionable even if the trail has not yet broken.
            fade_edge = self.monitor_cfg.get("momentum_fade_edge", 12)
            if pnl is not None and pnl > 0.05 and (edge < fade_edge or not direction_holds):
                trade.status = "PROTECT_PROFIT"; trade.last_event = "MOMENTUM_FADE"
                update = self._update_obj(
                    trade, snap, "MOMENTUM_FADE", "ACTION", "Momentum is fading",
                    "Protect the profit; with one contract, favour exiting if the next candle cannot reclaim trend structure.",
                    f"Directional edge fell to {edge:.0f} and/or EMA/VWAP alignment weakened while the reference option is still positive.",
                )

        # 8) Rejection at the planned wall while momentum fades.
        elif self._rejected_key(alert.direction, snap, trade, alert) and pnl is not None and pnl > -0.03:
            trade.status = "PROTECT_PROFIT"; trade.last_event = "KEY_LEVEL_REJECTION"
            update = self._update_obj(
                trade, snap, "KEY_LEVEL_REJECTION", "ACTION", "Key level rejected",
                "Protect profit / consider exiting the one contract instead of waiting for the percentage target.",
                f"Price tested {alert.key_level_label or 'the key level'} near ${alert.key_level_price:.2f} and rotated away as momentum/RVOL weakened.",
            )

        # 9) -10% is a caution state only while structure remains valid.
        elif pnl is not None and pnl <= -float(alert.premium_caution_pct) and trade.status != "CAUTION":
            trade.status = "CAUTION"; trade.last_event = "PREMIUM_CAUTION"
            if direction_holds and not self._failed_invalidation(alert.direction, snap.price, alert.invalidation_underlying):
                action = "HOLD cautiously for now; do not add. Exit immediately if the stated underlying invalidation breaks."
                reason = f"Reference premium is {pnl*100:.1f}% lower, but EMA/VWAP structure still supports the original thesis."
            else:
                action = "Prepare to EXIT; the pullback is no longer behaving like a healthy retracement."
                reason = f"Reference premium is {pnl*100:.1f}% lower and the underlying trend structure is weakening."
            update = self._update_obj(trade, snap, "PREMIUM_CAUTION", "CAUTION", "Premium entered the caution zone", action, reason)

        # 10) Recovery after a caution event is useful context and prevents a stale warning.
        elif trade.status == "CAUTION" and pnl is not None and pnl > -0.05 and direction_holds:
            trade.status = "TRACKING"; trade.last_event = "PULLBACK_RECOVERED"
            update = self._update_obj(
                trade, snap, "PULLBACK_RECOVERED", "INFO", "Pullback recovered",
                "Return to the original plan; keep watching the key level and invalidation.",
                f"Reference premium recovered to {pnl*100:.1f}% and EMA/VWAP structure is back in alignment.",
            )

        # 11) Strong opposite evidence + broken structure: don't wait for the hard line to be ticked exactly.
        elif opposite_score.total > direction_score.total and not direction_holds:
            trade.status = "EXIT"; trade.terminal = True; trade.last_event = "DIRECTION_FLIP"
            update = self._update_obj(
                trade, snap, "DIRECTION_FLIP", "EXIT", "Directional thesis flipped",
                "EXIT; wait for a completely new setup rather than averaging down.",
                f"Opposing evidence ({opposite_score.total:.0f}/100) now exceeds the original direction ({direction_score.total:.0f}/100) and EMA/VWAP structure no longer confirms the trade.",
            )

        if update is not None:
            trade.last_event_at = snap.timestamp
            trade.last_message = update.headline
            trade.updates.append(update)
            trade.updates = trade.updates[-int(self.monitor_cfg.get("max_update_history",20)):]
        return MonitorResult(trade=trade, update=update)
