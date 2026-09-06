from __future__ import annotations

from collections import defaultdict, deque
from datetime import time

import pandas as pd

from app.market.indicators import atr, crossover, ema, macd_hist, relative_volume, rsi, session_vwap, trend_from_frame
from app.market.session import SessionClock
from app.models import Bar, TechnicalSnapshot


class MarketStateStore:
    def __init__(self, strategy: dict):
        self.strategy = strategy
        self.clock = SessionClock(strategy["session"])
        self.buffers: dict[str, deque[Bar]] = defaultdict(lambda: deque(maxlen=900))
        self.previous_close: dict[str, float] = {}

    def add_bar(self, bar: Bar) -> None:
        buf = self.buffers[bar.symbol]
        if buf:
            prev_local = self.clock.localize(buf[-1].timestamp)
            now_local = self.clock.localize(bar.timestamp)
            if now_local.date() != prev_local.date():
                prev_day = [b for b in buf if self.clock.localize(b.timestamp).date() == prev_local.date()]
                if prev_day:
                    self.previous_close[bar.symbol] = prev_day[-1].close
        buf.append(bar)

    def _frame(self, symbol: str) -> pd.DataFrame:
        bars = list(self.buffers[symbol])
        if not bars:
            return pd.DataFrame()
        df = pd.DataFrame([b.model_dump() for b in bars])
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return df.set_index("timestamp").sort_index()

    @staticmethod
    def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
        if df.empty:
            return df
        return (df.resample(rule, label="right", closed="right")
                .agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum","symbol":"last"})
                .dropna(subset=["close"]))

    @staticmethod
    def _efficiency_ratio(frame: pd.DataFrame, periods: int = 6) -> float:
        """Kaufman-style efficiency ratio on recent closes; 1 = directional, 0 = noisy."""
        if frame.empty or len(frame) < 4:
            return 0.5
        closes = frame["close"].tail(periods + 1).astype(float)
        if len(closes) < 4:
            return 0.5
        net = abs(float(closes.iloc[-1] - closes.iloc[0]))
        path = float(closes.diff().abs().sum())
        return max(0.0, min(1.0, net / path)) if path > 1e-9 else 0.0

    @staticmethod
    def _vwap_cross_count(regular: pd.DataFrame, lookback: int = 10) -> int:
        if regular.empty or len(regular) < 3:
            return 0
        vw = session_vwap(regular)
        diff = (regular["close"].astype(float) - vw.astype(float)).tail(lookback)
        signs = []
        for value in diff:
            if value > 1e-9:
                signs.append(1)
            elif value < -1e-9:
                signs.append(-1)
        if len(signs) < 2:
            return 0
        return sum(1 for a, b in zip(signs, signs[1:]) if a != b)

    @staticmethod
    def _bars_since_break(frame: pd.DataFrame, trigger: float | None, direction: str) -> int | None:
        if trigger is None or frame.empty:
            return None
        closes = list(frame["close"].tail(12).astype(float))
        if not closes:
            return None
        if direction == "CALL":
            crossed = [i for i, x in enumerate(closes) if x >= trigger]
        else:
            crossed = [i for i, x in enumerate(closes) if x <= trigger]
        if not crossed:
            return None
        # Age from the first bar of the current consecutive breakout sequence.
        idx = len(closes) - 1
        pred = (lambda x: x >= trigger) if direction == "CALL" else (lambda x: x <= trigger)
        while idx > 0 and pred(closes[idx - 1]):
            idx -= 1
        return len(closes) - 1 - idx

    def _breakout_phase(self, price: float, trigger: float | None, atr_value: float, age: int | None, direction: str) -> tuple[str, float | None]:
        if trigger is None:
            return "NONE", None
        cfg = self.strategy["signals"]
        atr_value = max(atr_value, 0.01)
        if direction == "CALL":
            dist = (trigger - price) / atr_value if price < trigger else (price - trigger) / atr_value
            if price < trigger and 0 <= dist <= cfg["breakout_armed_distance_atr"]:
                return "ARMED", -dist
            if price >= trigger:
                ext = (price - trigger) / atr_value
                if ext <= cfg["breakout_max_extension_atr"] and (age or 0) <= cfg["breakout_max_age_bars"]:
                    return "TRIGGERED", ext
                return "EXTENDED", ext
        else:
            dist = (price - trigger) / atr_value if price > trigger else (trigger - price) / atr_value
            if price > trigger and 0 <= dist <= cfg["breakout_armed_distance_atr"]:
                return "ARMED", -dist
            if price <= trigger:
                ext = (trigger - price) / atr_value
                if ext <= cfg["breakout_max_extension_atr"] and (age or 0) <= cfg["breakout_max_age_bars"]:
                    return "TRIGGERED", ext
                return "EXTENDED", ext
        return "NONE", None

    def snapshot(self, symbol: str) -> TechnicalSnapshot | None:
        df = self._frame(symbol)
        cfg = self.strategy["signals"]
        if len(df) < 3:
            return None
        local_index = df.index.tz_convert(self.clock.tz)
        current_date = local_index[-1].date()
        same_day = df[local_index.date == current_date].copy()
        same_day_local = same_day.index.tz_convert(self.clock.tz)
        regular_mask = (same_day_local.time >= time(9,30)) & (same_day_local.time < time(16,0))
        regular = same_day[regular_mask].copy()

        indicator_df = df.tail(max(cfg["slow_ema"] * 4, 220)).copy()
        closes = indicator_df["close"]
        ef, em, es = ema(closes,cfg["fast_ema"]), ema(closes,cfg["medium_ema"]), ema(closes,cfg["slow_ema"])
        rs, mh = rsi(closes,cfg["rsi_period"]), macd_hist(closes)
        at = atr(indicator_df,cfg["atr_period"])
        rv = relative_volume(indicator_df["volume"])
        atr_value = float(at.iloc[-1]) if pd.notna(at.iloc[-1]) else max(float(indicator_df["high"].iloc[-1]-indicator_df["low"].iloc[-1]),0.01)

        opening_n = int(cfg["opening_range_minutes"])
        if regular.empty:
            vwap_value=float(closes.iloc[-1]); or_high=or_low=session_high=session_low=None; price_cross=0; opening_complete=False
        else:
            vw=session_vwap(regular); vwap_value=float(vw.iloc[-1])
            opening=regular.head(opening_n)
            opening_complete=len(regular) >= opening_n
            or_high=float(opening["high"].max()) if len(opening) else None
            or_low=float(opening["low"].min()) if len(opening) else None
            session_high=float(regular["high"].max()); session_low=float(regular["low"].min())
            if len(regular)>=2:
                prev_close=float(regular["close"].iloc[-2]); prev_vwap=float(session_vwap(regular.iloc[:-1]).iloc[-1]); cur=float(regular["close"].iloc[-1])
                price_cross=1 if prev_close<=prev_vwap and cur>vwap_value else -1 if prev_close>=prev_vwap and cur<vwap_value else 0
            else: price_cross=0

        five=self._resample(df,"5min"); fifteen=self._resample(df,"15min")
        efficiency_5m=self._efficiency_ratio(five, 6)
        vwap_crosses=self._vwap_cross_count(regular, 10)
        trend5=trend_from_frame(five,cfg["fast_ema"],cfg["medium_ema"])
        trend15=trend_from_frame(fifteen,min(5,cfg["fast_ema"]),min(10,cfg["medium_ema"]))
        enough_regime_data=len(regular)>=8 and len(five)>=4
        if enough_regime_data and efficiency_5m <= float(cfg.get("regime_efficiency_choppy_max",0.35)) and vwap_crosses >= int(cfg.get("regime_vwap_crosses_choppy_min",3)):
            regime="CHOPPY"
        elif enough_regime_data and efficiency_5m >= float(cfg.get("regime_efficiency_trending_min",0.45)) and vwap_crosses <= int(cfg.get("regime_vwap_crosses_trending_max",2)) and trend5 != "neutral":
            regime="TRENDING"
        else:
            regime="NEUTRAL"
        recent=regular.tail(20) if not regular.empty else df.tail(20)
        prev_regular=regular.iloc[:-1] if len(regular)>1 else regular.iloc[:0]
        micro=prev_regular.tail(6)
        micro_high=float(micro["high"].max()) if len(micro)>=2 else None
        micro_low=float(micro["low"].min()) if len(micro)>=2 else None
        recent_high=float(recent["high"].iloc[:-1].max()) if len(recent)>2 else None
        recent_low=float(recent["low"].iloc[:-1].min()) if len(recent)>2 else None

        trigger_call = or_high if opening_complete and or_high is not None else micro_high
        trigger_put = or_low if opening_complete and or_low is not None else micro_low
        price=float(closes.iloc[-1])
        age_call=self._bars_since_break(regular,trigger_call,"CALL")
        age_put=self._bars_since_break(regular,trigger_put,"PUT")
        phase_call,dist_call=self._breakout_phase(price,trigger_call,atr_value,age_call,"CALL")
        phase_put,dist_put=self._breakout_phase(price,trigger_put,atr_value,age_put,"PUT")

        efv=float(ef.iloc[-1]) if pd.notna(ef.iloc[-1]) else price
        emv=float(em.iloc[-1]) if pd.notna(em.iloc[-1]) else price
        esv=float(es.iloc[-1]) if pd.notna(es.iloc[-1]) else price
        efslope=(efv-float(ef.iloc[-3]))/max(atr_value,0.01) if len(ef)>=3 and pd.notna(ef.iloc[-3]) else 0.0
        emslope=(emv-float(em.iloc[-3]))/max(atr_value,0.01) if len(em)>=3 and pd.notna(em.iloc[-3]) else 0.0
        mhv=float(mh.iloc[-1]); mhprev=float(mh.iloc[-2]) if len(mh)>=2 else mhv
        vol_now=float(indicator_df["volume"].iloc[-1]); vol_prev=float(indicator_df["volume"].iloc[-2]) if len(indicator_df)>=2 else vol_now
        vol_accel=vol_now/max(vol_prev,1.0)
        last=indicator_df.iloc[-1]; rng=max(float(last["high"]-last["low"]),1e-9)
        body=abs(float(last["close"]-last["open"]))/rng
        close_loc=(float(last["close"]-last["low"]))/rng
        ts=indicator_df.index[-1].to_pydatetime()

        return TechnicalSnapshot(
            symbol=symbol,timestamp=ts,session=self.clock.classify(ts),price=price,
            ema_fast=efv,ema_medium=emv,ema_slow=esv,vwap=vwap_value,rsi=float(rs.iloc[-1]),
            macd_hist=mhv,macd_hist_change=mhv-mhprev,atr=atr_value,relative_volume=float(rv.iloc[-1]),volume_acceleration=vol_accel,
            opening_range_high=or_high,opening_range_low=or_low,opening_range_complete=opening_complete,
            session_high=session_high,session_low=session_low,previous_close=self.previous_close.get(symbol),recent_high=recent_high,recent_low=recent_low,
            micro_high=micro_high,micro_low=micro_low,
            trend_1m=trend_from_frame(indicator_df,cfg["fast_ema"],cfg["medium_ema"]),
            trend_5m=trend5,trend_15m=trend15,
            cross_fast_medium=crossover(ef,em),cross_fast_slow=crossover(ef,es),price_cross_vwap=price_cross,
            fast_ema_slope_atr=efslope,medium_ema_slope_atr=emslope,bar_body_ratio=body,close_location=close_loc,
            breakout_trigger_call=trigger_call,breakout_trigger_put=trigger_put,
            breakout_distance_atr_call=dist_call,breakout_distance_atr_put=dist_put,
            breakout_age_bars_call=age_call,breakout_age_bars_put=age_put,
            breakout_phase_call=phase_call,breakout_phase_put=phase_put,
            trend_efficiency_5m=round(efficiency_5m,4),vwap_crosses_recent=vwap_crosses,market_regime=regime,bars_ready=len(df),
        )
