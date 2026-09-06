from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Direction = Literal["CALL", "PUT"]
SessionName = Literal["prime", "secondary", "closed"]
BreakoutPhase = Literal["NONE", "ARMED", "TRIGGERED", "EXTENDED"]
NewsImpact = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
TrackingStatus = Literal[
    "TRACKING", "CAUTION", "TRAILING", "PROTECT_PROFIT",
    "TARGET_HIT", "EXIT", "EXPIRED", "DISMISSED",
]


class Bar(BaseModel):
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class TechnicalSnapshot(BaseModel):
    symbol: str
    timestamp: datetime
    session: SessionName
    price: float
    ema_fast: float
    ema_medium: float
    ema_slow: float
    vwap: float
    rsi: float
    macd_hist: float
    macd_hist_change: float = 0.0
    atr: float
    relative_volume: float
    volume_acceleration: float = 1.0
    opening_range_high: float | None = None
    opening_range_low: float | None = None
    opening_range_complete: bool = False
    session_high: float | None = None
    session_low: float | None = None
    previous_close: float | None = None
    recent_high: float | None = None
    recent_low: float | None = None
    micro_high: float | None = None
    micro_low: float | None = None
    trend_1m: str = "neutral"
    trend_5m: str = "neutral"
    trend_15m: str = "neutral"
    cross_fast_medium: int = 0
    cross_fast_slow: int = 0
    price_cross_vwap: int = 0
    fast_ema_slope_atr: float = 0.0
    medium_ema_slope_atr: float = 0.0
    bar_body_ratio: float = 0.0
    close_location: float = 0.5
    breakout_trigger_call: float | None = None
    breakout_trigger_put: float | None = None
    breakout_distance_atr_call: float | None = None
    breakout_distance_atr_put: float | None = None
    breakout_age_bars_call: int | None = None
    breakout_age_bars_put: int | None = None
    breakout_phase_call: BreakoutPhase = "NONE"
    breakout_phase_put: BreakoutPhase = "NONE"
    trend_efficiency_5m: float = 0.5
    vwap_crosses_recent: int = 0
    market_regime: Literal["TRENDING", "NEUTRAL", "CHOPPY"] = "NEUTRAL"
    bars_ready: int = 0


class DirectionScore(BaseModel):
    symbol: str
    direction: Direction
    total: float
    trend: float
    structure: float
    volume: float
    momentum: float
    levels: float
    market_context: float
    setup: str
    setup_score: float
    reasons: list[str] = Field(default_factory=list)
    invalidation_underlying: float | None = None
    target_underlying: float | None = None
    trigger_underlying: float | None = None
    chase_limit_underlying: float | None = None
    breakout_phase: BreakoutPhase = "NONE"
    early_entry_eligible: bool = False
    chase_utilization: float | None = None
    regime_eligible: bool = True


class OptionCandidate(BaseModel):
    symbol: str
    contract_symbol: str
    direction: Direction
    expiration: str
    dte: int
    strike: float
    bid: float
    ask: float
    mid: float
    spread_pct: float
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    iv: float | None = None
    volume: float | None = None
    option_score: float
    estimated_emergency_risk_usd: float
    # Quote provenance. Alpaca remains the contract-discovery source; Webull may
    # replace the executable bid/ask when its option market-data entitlement is available.
    quote_source: str = "alpaca_indicative"
    quote_timestamp: datetime | None = None
    alpaca_bid: float | None = None
    alpaca_ask: float | None = None
    webull_bid: float | None = None
    webull_ask: float | None = None
    quote_discrepancy_pct: float | None = None


class AIReview(BaseModel):
    verdict: Literal["APPROVE", "DOWNGRADE", "VETO", "SKIPPED", "ERROR"] = "SKIPPED"
    # Confidence is AI review-strength only. It is never treated as win probability
    # and V1.4.2 no longer uses it as a hard READY threshold.
    confidence: float = 0.0
    summary: str = "AI review disabled"
    reason_code: str = "OTHER"
    review_mode: Literal["PRECHECK", "CACHE", "MICRO", "FULL", "SKIPPED"] = "SKIPPED"
    risk_flags: list[str] = Field(default_factory=list)
    positives: list[str] = Field(default_factory=list)
    model: str = ""
    reviewed_at: datetime | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    feature_fingerprint: str = ""
    precheck_age_ms: int | None = None
    cache_hit: bool = False
    material_changes: list[str] = Field(default_factory=list)
    underlying_before: float | None = None
    underlying_after: float | None = None
    option_ask_before: float | None = None
    option_ask_after: float | None = None
    precheck_option_ask: float | None = None
    option_move_during_review_pct: float | None = None
    precheck_to_trigger_option_move_pct: float | None = None
    would_have_triggered_without_ai: bool = False




class AICounterfactual(BaseModel):
    id: str
    created_at: datetime
    expires_at: datetime
    updated_at: datetime
    symbol: str
    direction: Direction
    contract_symbol: str
    reference_option_ask: float
    current_option_bid: float | None = None
    current_option_ask: float | None = None
    current_pnl_pct: float | None = None
    max_pnl_pct: float | None = None
    min_pnl_pct: float | None = None
    terminal: bool = False
    outcome: Literal["OPEN", "WIN", "LOSS", "FLAT"] = "OPEN"
    terminal_reason: str = ""
    ai_verdict: str = "VETO"
    ai_reason_code: str = "OTHER"
    ai_review_mode: str = ""
    quant_score: float = 0.0
    directional_edge: float = 0.0
    setup: str = ""
    invalidation_underlying: float | None = None
    target_underlying: float | None = None
    reference_underlying: float | None = None

class NewsItem(BaseModel):
    id: str
    created_at: datetime
    headline: str
    summary: str = ""
    source: str
    url: str = ""
    symbols: list[str] = Field(default_factory=list)
    category: str = "market"
    impact: NewsImpact = "LOW"
    reason: str = ""


class EconomicEvent(BaseModel):
    id: str
    event_at: datetime
    name: str
    category: str = "macro"
    importance: int = 1
    source: str = ""
    symbols: list[str] = Field(default_factory=list)


class TradeAlert(BaseModel):
    id: str
    created_at: datetime
    symbol: str
    session: SessionName
    direction: Direction
    quant_score: float
    opposite_score: float
    setup: str
    reasons: list[str]
    underlying_price: float
    confirmation: str
    invalidation_underlying: float
    target_underlying: float
    contract: OptionCandidate | None = None
    ai_review: AIReview = Field(default_factory=AIReview)
    take_profit_pct: float
    stretch_target_pct: float | None = None
    emergency_stop_pct: float
    premium_caution_pct: float = 0.10
    conviction_label: str = "VALID"
    directional_edge: float = 0.0
    score_explanation: str = ""
    pullback_hold_underlying: float | None = None
    pullback_hold_label: str | None = None
    key_level_price: float | None = None
    key_level_label: str | None = None
    breakout_target_underlying: float | None = None
    exit_strategy: list[str] = Field(default_factory=list)
    breakout_phase: BreakoutPhase = "NONE"
    trigger_underlying: float | None = None
    chase_limit_underlying: float | None = None
    option_entry_reference: float | None = None
    option_take_profit_price: float | None = None
    option_stretch_price: float | None = None
    option_caution_price: float | None = None
    option_hard_stop_price: float | None = None
    chase_utilization: float | None = None
    market_regime: Literal["TRENDING", "NEUTRAL", "CHOPPY"] = "NEUTRAL"
    watch_only_reason: str = ""
    status: Literal["WATCH", "READY", "VETOED"] = "WATCH"


class TradeUpdate(BaseModel):
    timestamp: datetime
    event: str
    severity: Literal["INFO", "CAUTION", "ACTION", "EXIT"]
    headline: str
    action: str
    reason: str
    underlying_price: float
    option_bid: float | None = None
    option_ask: float | None = None
    reference_pnl_pct: float | None = None
    trailing_level_underlying: float | None = None


class TrackedTrade(BaseModel):
    alert_id: str
    created_at: datetime
    updated_at: datetime
    symbol: str
    direction: Direction
    contract_symbol: str
    expiration: str
    status: TrackingStatus = "TRACKING"
    terminal: bool = False
    entry_source: Literal["alert_reference", "confirmed_fill"] = "alert_reference"
    reference_entry_underlying: float
    reference_entry_option_price: float
    confirmed_entry_option_price: float | None = None
    current_underlying: float
    current_option_bid: float | None = None
    current_option_ask: float | None = None
    reference_pnl_pct: float | None = None
    max_reference_pnl_pct: float | None = None
    min_reference_pnl_pct: float | None = None
    high_water_underlying: float
    low_water_underlying: float
    key_level_broken: bool = False
    primary_target_reached: bool = False
    stretch_target_reached: bool = False
    trailing_level_underlying: float | None = None
    last_event: str = "NEW_SETUP"
    last_event_at: datetime
    last_message: str = "Recommendation is being monitored from its alert-time reference price."
    telegram_message_id: int | None = None
    updates: list[TradeUpdate] = Field(default_factory=list)
    webull_position_seen: bool = False
    webull_last_quantity: float | None = None
    webull_missing_polls: int = 0
    shadow: bool = False

    @property
    def effective_entry_option_price(self) -> float:
        return self.confirmed_entry_option_price or self.reference_entry_option_price


class WebullOptionQuote(BaseModel):
    contract_symbol: str
    bid: float
    ask: float
    last: float | None = None
    volume: float | None = None
    open_interest: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    iv: float | None = None
    quote_time: datetime | None = None
    bid_size: float | None = None
    ask_size: float | None = None

class WebullPosition(BaseModel):
    symbol: str
    instrument_type: str = ""
    quantity: float = 0.0
    cost_price: float | None = None
    last_price: float | None = None
    unrealized_profit_loss: float | None = None
    option_type: str | None = None
    expiration: str | None = None
    strike: float | None = None
    contract_symbol: str | None = None


class DashboardState(BaseModel):
    mode: str
    macro_risk_mode: str
    macro_risk_reason: str = ""
    session: SessionName
    alerts_today: int
    max_alerts_today: int | None
    latest: dict[str, TechnicalSnapshot]
    latest_scores: dict[str, dict[str, DirectionScore]]
    alerts: list[TradeAlert]
    tracked_trades: list[TrackedTrade] = Field(default_factory=list)
    latest_news: list[NewsItem] = Field(default_factory=list)
    upcoming_events: list[EconomicEvent] = Field(default_factory=list)
    telegram_subscribers: int = 0
    telegram_bot_username: str = ""
    news_connected: bool = False
    economic_calendar_connected: bool = False
    webull_sync_enabled: bool = False
    webull_sync_connected: bool = False
    webull_sync_error: str = ""
    webull_quotes_enabled: bool = False
    webull_quotes_required: bool = False
    ai_review_enabled: bool = False
    ai_review_required: bool = False
    ai_policy: str = "disabled"
    ai_fail_open_on_error: bool = True
    ai_model: str = ""
    wins_today: int = 0
    profit_lock_active: bool = False
    watch_only_symbols: list[str] = Field(default_factory=list)
