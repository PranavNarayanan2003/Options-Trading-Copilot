from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from app.models import AIReview, DirectionScore, OptionCandidate, TechnicalSnapshot


_REASON_CODES = [
    "CLEAN_ALIGNMENT",
    "HIGHER_TIMEFRAME_CONFLICT",
    "CHOP_RISK",
    "LATE_ENTRY",
    "WEAK_PARTICIPATION",
    "EVENT_RISK",
    "OPTION_QUALITY",
    "QUOTE_CONFLICT",
    "MOMENTUM_DIVERGENCE",
    "STRUCTURE_CONFLICT",
    "MATERIAL_STATE_CHANGE",
    "OTHER",
]


class AIReviewer:
    """Fast conservative context-verification layer.

    Python owns signal generation, contract selection and hard risk gates. AI only
    checks whether an already-promising setup is contextually coherent. V1.4.2
    supports an ARMED-state precheck, cached approval reuse, and a compact
    trigger-time micro-review when material state changed.
    """

    def __init__(self, api_key: str, model: str, reasoning_effort: str = "none", timeout_seconds: float = 3.0):
        self.api_key = api_key
        self.model = model
        self.reasoning_effort = reasoning_effort if reasoning_effort in {"none", "low", "medium", "high", "xhigh", "max"} else "none"
        self.timeout_seconds = float(timeout_seconds)
        self._client: httpx.AsyncClient | None = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.model)

    async def start(self):
        if self.configured and self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds),
                limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
                http2=False,
            )

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            await self.start()
        if self._client is None:
            # Allows deterministic tests to instantiate without a key and still
            # receive the normal fail-closed result instead of crashing here.
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds))
        return self._client

    @staticmethod
    def _round(value: float | None, digits: int = 3):
        return None if value is None else round(float(value), digits)

    @staticmethod
    def _bucket(value: float | None, step: float):
        if value is None:
            return None
        return round(math.floor(float(value) / step) * step, 4)

    @staticmethod
    def _ema_alignment(snapshot: TechnicalSnapshot) -> str:
        if snapshot.ema_fast > snapshot.ema_medium > snapshot.ema_slow:
            return "bullish"
        if snapshot.ema_fast < snapshot.ema_medium < snapshot.ema_slow:
            return "bearish"
        return "mixed"

    @staticmethod
    def _sign(value: float) -> str:
        return "positive" if value > 0 else "negative" if value < 0 else "flat"

    def build_feature_vector(
        self,
        snapshot: TechnicalSnapshot,
        score: DirectionScore,
        opposite: DirectionScore,
        contract: OptionCandidate | None,
        macro_mode: str,
        context: dict | None = None,
    ) -> dict[str, Any]:
        """Compact, decision-relevant feature vector for AI.

        This intentionally excludes most raw TechnicalSnapshot fields. Python has
        already calculated them; AI receives the derived context that helps detect
        contradictions and regime/entry-quality issues.
        """
        context = context or {}
        atr = max(float(snapshot.atr or 0.0), 1e-6)
        price_vs_vwap_atr = (float(snapshot.price) - float(snapshot.vwap)) / atr
        ema_spread_atr = (float(snapshot.ema_fast) - float(snapshot.ema_medium)) / atr
        news = []
        for item in (context.get("recent_relevant_news") or [])[:3]:
            news.append({
                "headline": str(item.get("headline", ""))[:180],
                "impact": item.get("impact", ""),
                "category": item.get("category", ""),
            })
        events = []
        for event in (context.get("upcoming_events") or [])[:3]:
            events.append({
                "event_at": event.get("event_at"),
                "name": str(event.get("name", ""))[:120],
                "importance": event.get("importance"),
                "category": event.get("category", ""),
            })
        option = None
        if contract is not None:
            option = {
                "contract": contract.contract_symbol,
                "strike": self._round(contract.strike, 3),
                "dte": contract.dte,
                "bid": self._round(contract.bid, 4),
                "ask": self._round(contract.ask, 4),
                "spread_pct": self._round(contract.spread_pct, 2),
                "delta": self._round(contract.delta, 3),
                "iv": self._round(contract.iv, 3),
                "volume": self._round(contract.volume, 0),
                "option_score": self._round(contract.option_score, 1),
                "quote_source": contract.quote_source,
                "quote_discrepancy_pct": self._round(contract.quote_discrepancy_pct, 2),
            }
        return {
            "symbol": snapshot.symbol,
            "is_etf": bool(context.get("is_etf", False)),
            "direction": score.direction,
            "score": self._round(score.total, 1),
            "opposite_score": self._round(opposite.total, 1),
            "edge": self._round(score.total - opposite.total, 1),
            "score_components": {
                "trend": self._round(score.trend, 1),
                "structure": self._round(score.structure, 1),
                "volume": self._round(score.volume, 1),
                "momentum": self._round(score.momentum, 1),
                "levels": self._round(score.levels, 1),
                "market_context": self._round(score.market_context, 1),
            },
            "setup": score.setup,
            "setup_score": self._round(score.setup_score, 1),
            "breakout_phase": score.breakout_phase,
            "chase_utilization": self._round(score.chase_utilization, 3),
            "regime": snapshot.market_regime,
            "trends": {"1m": snapshot.trend_1m, "5m": snapshot.trend_5m, "15m": snapshot.trend_15m},
            "trend_efficiency_5m": self._round(snapshot.trend_efficiency_5m, 3),
            "recent_vwap_crosses": int(snapshot.vwap_crosses_recent),
            "ema_alignment": self._ema_alignment(snapshot),
            "fast_ema_slope_atr": self._round(snapshot.fast_ema_slope_atr, 3),
            "price_vs_vwap_atr": self._round(price_vs_vwap_atr, 3),
            "ema_fast_medium_spread_atr": self._round(ema_spread_atr, 3),
            "rsi": self._round(snapshot.rsi, 1),
            "macd_hist_sign": self._sign(snapshot.macd_hist),
            "macd_acceleration_sign": self._sign(snapshot.macd_hist_change),
            "rvol": self._round(snapshot.relative_volume, 2),
            "volume_acceleration": self._round(snapshot.volume_acceleration, 2),
            "candle_body_ratio": self._round(snapshot.bar_body_ratio, 2),
            "close_location": self._round(snapshot.close_location, 2),
            "risk_mode": macro_mode,
            "symbol_risk_reason": str(context.get("symbol_risk_reason", ""))[:220],
            "market_risk_reason": str(context.get("market_risk_reason", ""))[:220],
            "profit_lock_active": bool(context.get("profit_lock_active", False)),
            "wins_today": int(context.get("wins_today", 0)),
            "alerts_today": int(context.get("alerts_today", 0)),
            "news": news,
            "upcoming_events": events,
            "option": option,
        }

    def feature_fingerprint(self, vector: dict[str, Any]) -> str:
        """Hash material context only; expected ARMED->TRIGGERED movement is excluded."""
        trends = vector.get("trends") or {}
        news = vector.get("news") or []
        events = vector.get("upcoming_events") or []
        material = {
            "symbol": vector.get("symbol"),
            "direction": vector.get("direction"),
            "setup": vector.get("setup"),
            "is_etf": vector.get("is_etf"),
            "regime": vector.get("regime"),
            "trend_1m": trends.get("1m"),
            "trend_5m": trends.get("5m"),
            "trend_15m": trends.get("15m"),
            "risk_mode": vector.get("risk_mode"),
            "ema_alignment": vector.get("ema_alignment"),
            "macd_hist_sign": vector.get("macd_hist_sign"),
            "macd_acceleration_sign": vector.get("macd_acceleration_sign"),
            "score_bucket": self._bucket(vector.get("score"), 5.0),
            "edge_bucket": self._bucket(vector.get("edge"), 10.0),
            "rvol_bucket": self._bucket(vector.get("rvol"), 0.25),
            "efficiency_bucket": self._bucket(vector.get("trend_efficiency_5m"), 0.10),
            "vwap_cross_bucket": min(int(vector.get("recent_vwap_crosses") or 0), 4),
            "news_sig": [(x.get("headline"), x.get("impact")) for x in news],
            "event_sig": [(x.get("event_at"), x.get("name"), x.get("importance")) for x in events],
        }
        raw = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    def material_changes(
        self,
        previous: dict[str, Any],
        current: dict[str, Any],
        previous_contract_symbol: str | None = None,
        previous_option_ask: float | None = None,
        *,
        score_drop: float = 6.0,
        edge_drop: float = 10.0,
        rvol_drop_fraction: float = 0.25,
        option_ask_drift_pct: float = 0.06,
    ) -> list[str]:
        changes: list[str] = []
        p_trends, c_trends = previous.get("trends") or {}, current.get("trends") or {}
        for key in ("1m", "5m", "15m"):
            if p_trends.get(key) != c_trends.get(key):
                changes.append(f"trend_{key}_changed")
        for key in ("direction", "setup", "regime", "risk_mode", "ema_alignment"):
            if previous.get(key) != current.get(key):
                changes.append(f"{key}_changed")
        if previous.get("news") != current.get("news"):
            changes.append("news_changed")
        if previous.get("upcoming_events") != current.get("upcoming_events"):
            changes.append("event_context_changed")
        p_score, c_score = float(previous.get("score") or 0), float(current.get("score") or 0)
        p_edge, c_edge = float(previous.get("edge") or 0), float(current.get("edge") or 0)
        if c_score < p_score - score_drop:
            changes.append("score_weakened")
        if c_edge < p_edge - edge_drop:
            changes.append("edge_weakened")
        p_rvol, c_rvol = float(previous.get("rvol") or 0), float(current.get("rvol") or 0)
        if p_rvol > 0 and c_rvol < p_rvol * (1.0 - rvol_drop_fraction):
            changes.append("rvol_weakened")
        option = current.get("option") or {}
        current_symbol = option.get("contract")
        if previous_contract_symbol and current_symbol and previous_contract_symbol != current_symbol:
            changes.append("contract_changed")
        current_ask = option.get("ask")
        if previous_option_ask and current_ask:
            drift = abs(float(current_ask) / float(previous_option_ask) - 1.0)
            if drift > option_ask_drift_pct:
                changes.append("option_price_changed")
        return changes

    async def _call(self, payload: dict[str, Any], mode: str, prior: AIReview | None = None, changes: list[str] | None = None) -> AIReview:
        if not self.api_key:
            return AIReview(
                verdict="SKIPPED", confidence=0.0, summary="AI review is not configured.",
                reason_code="OTHER", review_mode=mode, risk_flags=["ai_not_configured"], model=self.model,
                reviewed_at=datetime.now(timezone.utc),
            )

        schema = {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["APPROVE", "VETO"]},
                "reason_code": {"type": "string", "enum": _REASON_CODES},
                "reason": {"type": "string", "maxLength": 180},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["verdict", "reason_code", "reason", "confidence"],
            "additionalProperties": False,
        }

        if mode == "MICRO":
            instructions = (
                "You are a very fast FINAL consistency check for an intraday long-options setup. A prior AI precheck exists. "
                "Review ONLY the listed material changes and the compact current feature vector. APPROVE if the prior thesis remains coherent; "
                "VETO if the changes materially damage trend coherence, regime fit, participation, entry freshness, option quality, quote consistency, or event context. "
                "Do not find a new trade, change direction/strike/expiry, or reinterpret confidence as win probability. Return only the schema."
            )
            body_payload = {
                "mode": "MICRO",
                "prior_verdict": prior.verdict if prior else None,
                "prior_reason_code": prior.reason_code if prior else None,
                "material_changes": changes or [],
                "current": payload,
            }
        elif mode == "PRECHECK":
            instructions = (
                "You are a low-latency pre-entry context gate for an intraday long-options engine. The setup is ARMED but has not triggered yet. "
                "Python already owns all hard technical and risk rules. Decide whether the surrounding structure is coherent enough to remain eligible IF the trigger occurs soon. "
                "Focus on 1m/5m/15m agreement, regime/chop risk, momentum and participation, entry quality, likely option liquidity/quote consistency, and relevant event/news context. "
                "APPROVE means context is coherent, not that the trade is guaranteed. VETO meaningful contradictions. Do not invent or modify a trade. Return only the schema."
            )
            body_payload = {"mode": "PRECHECK", "candidate": payload}
        else:
            instructions = (
                "You are the low-latency FINAL context-verification gate for an intraday long-options engine. Python has already passed all deterministic signal, ETF, regime, risk and option-quality rules. "
                "Do not find trades, change direction, strike or expiry, or increase the quant score. APPROVE only if the compact evidence is coherent across timeframes, regime, participation, entry freshness, option quality/quotes and event context. "
                "VETO material contradictions. Confidence is review-strength only and MUST NOT be interpreted as win probability. Return only the schema."
            )
            body_payload = {"mode": "FULL", "candidate": payload}

        body = {
            "model": self.model,
            "instructions": instructions,
            "input": json.dumps(body_payload, separators=(",", ":"), default=str),
            "reasoning": {"effort": self.reasoning_effort},
            "text": {"verbosity": "low", "format": {"type": "json_schema", "name": "trade_review", "strict": True, "schema": schema}},
            "store": False,
            "max_output_tokens": 100,
        }

        started = time.perf_counter()
        try:
            client = await self._get_client()
            response = await client.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=body,
            )
            response.raise_for_status()
            data = response.json()
            output_text = data.get("output_text", "") or ""
            if not output_text:
                for item in data.get("output", []):
                    if item.get("type") == "message":
                        for content in item.get("content", []):
                            if content.get("type") == "output_text":
                                output_text += content.get("text", "")
            if not output_text:
                raise ValueError("OpenAI response did not contain structured output text")
            raw = json.loads(output_text)
            usage = data.get("usage") or {}
            vector_fingerprint = self.feature_fingerprint(payload)
            return AIReview(
                verdict=raw["verdict"],
                confidence=float(raw.get("confidence", 0.0)),
                summary=str(raw.get("reason") or raw.get("reason_code") or "AI review complete."),
                reason_code=str(raw.get("reason_code") or "OTHER"),
                review_mode=mode,
                feature_fingerprint=vector_fingerprint,
                risk_flags=[] if raw["verdict"] == "APPROVE" else [str(raw.get("reason_code") or "OTHER")],
                positives=[str(raw.get("reason_code") or "CLEAN_ALIGNMENT")] if raw["verdict"] == "APPROVE" else [],
                model=str(data.get("model") or self.model),
                reviewed_at=datetime.now(timezone.utc),
                latency_ms=int((time.perf_counter() - started) * 1000),
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                would_have_triggered_without_ai=True,
            )
        except Exception as exc:
            return AIReview(
                verdict="ERROR", confidence=0.0,
                summary="AI verification failed; candidate blocked by fail-closed policy.",
                reason_code="OTHER", review_mode=mode,
                risk_flags=[f"ai_review_error:{type(exc).__name__}"], positives=[], model=self.model,
                reviewed_at=datetime.now(timezone.utc), latency_ms=int((time.perf_counter() - started) * 1000),
                would_have_triggered_without_ai=True,
            )

    async def precheck(self, vector: dict[str, Any]) -> AIReview:
        return await self._call(vector, "PRECHECK")

    async def micro_review(self, vector: dict[str, Any], prior: AIReview, changes: list[str]) -> AIReview:
        review = await self._call(vector, "MICRO", prior=prior, changes=changes)
        review.material_changes = list(changes)
        return review

    async def review(
        self,
        snapshot: TechnicalSnapshot,
        score: DirectionScore,
        opposite: DirectionScore,
        contract: OptionCandidate | None,
        macro_mode: str,
        macro_reason: str,
        context: dict | None = None,
    ) -> AIReview:
        # Backward-compatible public entry point, now using the compact vector.
        merged = dict(context or {})
        merged.setdefault("market_risk_reason", macro_reason)
        vector = self.build_feature_vector(snapshot, score, opposite, contract, macro_mode, merged)
        return await self._call(vector, "FULL")
