from __future__ import annotations

from dataclasses import dataclass

from app.models import DirectionScore, TechnicalSnapshot


@dataclass
class SetupResult:
    name: str
    bullish: float
    bearish: float
    bull_reasons: list[str]
    bear_reasons: list[str]


class SignalScorer:
    def __init__(self, strategy: dict):
        self.strategy = strategy

    def _ignition(self, s: TechnicalSnapshot) -> SetupResult:
        bull=bear=0.0; br=[]; rr=[]
        # ARMED is useful on the dashboard, but it can never become a READY
        # recommendation until price actually triggers the level.
        if s.breakout_phase_call == "ARMED":
            bull += 36; br.append("Price is pressing just below the breakout trigger")
        elif s.breakout_phase_call == "TRIGGERED":
            bull += 52; br.append("First clean break through the trigger")
        if s.breakout_phase_put == "ARMED":
            bear += 36; rr.append("Price is pressing just above the breakdown trigger")
        elif s.breakout_phase_put == "TRIGGERED":
            bear += 52; rr.append("First clean break below the trigger")

        if s.ema_fast > s.ema_medium:
            bull += 10; br.append("Fast EMA leads medium EMA")
        if s.ema_fast < s.ema_medium:
            bear += 10; rr.append("Fast EMA trails medium EMA")
        if s.fast_ema_slope_atr >= 0.03:
            bull += 7; br.append("Fast EMA slope accelerating up")
        if s.fast_ema_slope_atr <= -0.03:
            bear += 7; rr.append("Fast EMA slope accelerating down")
        if s.price >= s.vwap:
            bull += 8; br.append("Price holding above VWAP")
        if s.price <= s.vwap:
            bear += 8; rr.append("Price holding below VWAP")
        if s.macd_hist_change > 0:
            bull += 6; br.append("MACD momentum accelerating")
        if s.macd_hist_change < 0:
            bear += 6; rr.append("MACD momentum accelerating lower")
        if s.relative_volume >= 1.15:
            bull += 5; bear += 5
        if s.volume_acceleration >= 1.15:
            bull += 4; bear += 4
        if s.close_location >= 0.68 and s.bar_body_ratio >= 0.45:
            bull += 5; br.append("Breakout candle closes near its high")
        if s.close_location <= 0.32 and s.bar_body_ratio >= 0.45:
            bear += 5; rr.append("Breakdown candle closes near its low")
        if 48 <= s.rsi <= 72:
            bull += 3
        if 28 <= s.rsi <= 52:
            bear += 3
        return SetupResult("BREAKOUT_IGNITION",min(bull,100),min(bear,100),br,rr)

    def _setup_scores(self,s:TechnicalSnapshot)->list[SetupResult]:
        atrv=max(s.atr,0.01); results=[self._ignition(s)]
        bull=bear=0.0;br=[];rr=[]
        if s.ema_fast>s.ema_medium>s.ema_slow: bull+=45;br.append("Fast > medium > slow EMA alignment")
        if s.ema_fast<s.ema_medium<s.ema_slow: bear+=45;rr.append("Fast < medium < slow EMA alignment")
        if s.cross_fast_medium==1 or s.cross_fast_slow==1: bull+=20;br.append("Fresh bullish EMA crossover")
        if s.cross_fast_medium==-1 or s.cross_fast_slow==-1: bear+=20;rr.append("Fresh bearish EMA crossover")
        if s.price>s.vwap: bull+=20;br.append("Price above VWAP")
        else: bear+=20;rr.append("Price below VWAP")
        if s.relative_volume>=1.3:
            if bull>=bear: bull+=15;br.append("Relative volume expansion")
            else: bear+=15;rr.append("Relative volume expansion")
        results.append(SetupResult("EMA_VWAP_MOMENTUM",min(bull,100),min(bear,100),br,rr))

        bull=bear=0.0;br=[];rr=[]
        if s.opening_range_complete and s.opening_range_high is not None and s.price>s.opening_range_high:
            bull=60;br.append("Price breaking opening-range high")
            if s.relative_volume>=1.4: bull+=25;br.append("Breakout supported by RVOL")
            if s.price>s.vwap: bull+=15;br.append("Breakout above VWAP")
        if s.opening_range_complete and s.opening_range_low is not None and s.price<s.opening_range_low:
            bear=60;rr.append("Price breaking opening-range low")
            if s.relative_volume>=1.4: bear+=25;rr.append("Breakdown supported by RVOL")
            if s.price<s.vwap: bear+=15;rr.append("Breakdown below VWAP")
        results.append(SetupResult("OPENING_RANGE_BREAK",min(bull,100),min(bear,100),br,rr))

        bull=bear=0.0;br=[];rr=[]
        if s.price_cross_vwap==1:
            bull=65;br.append("Fresh VWAP reclaim")
            if s.ema_fast>s.ema_medium: bull+=20;br.append("EMA confirms reclaim")
            if s.rsi>=52: bull+=15;br.append("RSI confirms bullish momentum")
        if s.price_cross_vwap==-1:
            bear=65;rr.append("Fresh VWAP rejection/loss")
            if s.ema_fast<s.ema_medium: bear+=20;rr.append("EMA confirms rejection")
            if s.rsi<=48: bear+=15;rr.append("RSI confirms bearish momentum")
        results.append(SetupResult("VWAP_RECLAIM_REJECTION",min(bull,100),min(bear,100),br,rr))

        dist=abs(s.price-s.ema_medium)/atrv; bull=bear=0.0;br=[];rr=[]
        if s.ema_fast>s.ema_medium>s.ema_slow and dist<=.45 and s.price>=s.ema_medium:
            bull=70;br.append("Bull-trend pullback holding medium EMA")
            if s.price>s.vwap: bull+=15;br.append("Still above VWAP")
            if s.macd_hist>0: bull+=15;br.append("Momentum re-accelerating")
        if s.ema_fast<s.ema_medium<s.ema_slow and dist<=.45 and s.price<=s.ema_medium:
            bear=70;rr.append("Bear-trend pullback rejecting medium EMA")
            if s.price<s.vwap: bear+=15;rr.append("Still below VWAP")
            if s.macd_hist<0: bear+=15;rr.append("Momentum re-accelerating")
        results.append(SetupResult("TREND_PULLBACK",min(bull,100),min(bear,100),br,rr))

        bull=bear=0.0;br=[];rr=[]
        if s.recent_low is not None and abs(s.price-s.recent_low)<=.35*atrv and s.rsi<48 and s.price>s.ema_fast:
            bull=75;br.append("Bounce from recent support with EMA reclaim")
            if s.relative_volume>=1.2: bull+=15;br.append("Volume supports reversal")
        if s.recent_high is not None and abs(s.price-s.recent_high)<=.35*atrv and s.rsi>52 and s.price<s.ema_fast:
            bear=75;rr.append("Rejection from recent resistance with EMA loss")
            if s.relative_volume>=1.2: bear+=15;rr.append("Volume supports reversal")
        results.append(SetupResult("SUPPORT_RESISTANCE_REVERSAL",min(bull,100),min(bear,100),br,rr))

        bull=bear=0.0;br=[];rr=[]
        if s.opening_range_low is not None and s.recent_low is not None and s.recent_low<s.opening_range_low and s.price>s.opening_range_low:
            bull=78;br.append("Failed downside break reclaimed opening range")
            if s.price>s.vwap: bull+=15;br.append("Reclaimed VWAP after failure")
        if s.opening_range_high is not None and s.recent_high is not None and s.recent_high>s.opening_range_high and s.price<s.opening_range_high:
            bear=78;rr.append("Failed upside break fell back into opening range")
            if s.price<s.vwap: bear+=15;rr.append("Lost VWAP after failure")
        results.append(SetupResult("FAILED_BREAKOUT",min(bull,100),min(bear,100),br,rr))
        return results

    def score(self,s:TechnicalSnapshot,benchmark_bias:float=0.0)->dict[str,DirectionScore]:
        setups=self._setup_scores(s)
        ignition=next(x for x in setups if x.name=="BREAKOUT_IGNITION")
        best_bull=max(setups,key=lambda x:x.bullish); best_bear=max(setups,key=lambda x:x.bearish)
        # On the first clean breakout bars, preserve the ignition label even when
        # a lagging pattern has a slightly higher pattern-only score.
        if s.breakout_phase_call=="TRIGGERED" and ignition.bullish>=60: best_bull=ignition
        if s.breakout_phase_put=="TRIGGERED" and ignition.bearish>=60: best_bear=ignition

        def comp(direction:str):
            bull=direction=="CALL"; trend=0.0
            trend += 8 if ((s.ema_fast>s.ema_medium)==bull) else 0
            trend += 7 if ((s.ema_medium>s.ema_slow)==bull) else 0
            trend += 5 if ((s.price>s.vwap)==bull) else 0
            trend += 3 if s.trend_5m == ("bullish" if bull else "bearish") else 0
            trend += 2 if s.trend_15m == ("bullish" if bull else "bearish") else 0
            structure=0.0
            phase=s.breakout_phase_call if bull else s.breakout_phase_put
            trigger=s.breakout_trigger_call if bull else s.breakout_trigger_put
            if phase=="TRIGGERED": structure+=12
            if bull:
                if s.recent_high and s.price>s.recent_high: structure+=7
                if s.opening_range_complete and s.opening_range_high and s.price>s.opening_range_high: structure+=6
                if s.price_cross_vwap==1: structure+=5
            else:
                if s.recent_low and s.price<s.recent_low: structure+=7
                if s.opening_range_complete and s.opening_range_low and s.price<s.opening_range_low: structure+=6
                if s.price_cross_vwap==-1: structure+=5
            structure=min(structure,25)
            volume=min(max((s.relative_volume-.8)*18,0),12)
            if s.volume_acceleration>=1.2: volume=min(15,volume+3)
            momentum=0.0
            if bull:
                momentum += 8 if s.rsi>=55 else 3 if s.rsi>=50 else 0
                momentum += 5 if s.macd_hist>0 else 0
                momentum += 2 if s.macd_hist_change>0 else 0
            else:
                momentum += 8 if s.rsi<=45 else 3 if s.rsi<=50 else 0
                momentum += 5 if s.macd_hist<0 else 0
                momentum += 2 if s.macd_hist_change<0 else 0
            levels=0.0
            if phase=="TRIGGERED": levels+=6
            if bull and s.opening_range_complete and s.opening_range_high and s.price>s.opening_range_high: levels+=4
            if (not bull) and s.opening_range_complete and s.opening_range_low and s.price<s.opening_range_low: levels+=4
            context=max(0.0,min(10.0,5.0+(benchmark_bias if bull else -benchmark_bias)))
            best=best_bull if bull else best_bear
            setup_value=best.bullish if bull else best.bearish
            setup_boost=min(setup_value,100)*.10
            total=min(100.0,trend+structure+volume+momentum+levels+context+setup_boost)
            reasons=list(best.bull_reasons if bull else best.bear_reasons)
            if s.trend_5m == ("bullish" if bull else "bearish"): reasons.append(f"5m trend {'bullish' if bull else 'bearish'}")
            if s.relative_volume>=1.3: reasons.append(f"RVOL {s.relative_volume:.2f}x")
            atrv=max(s.atr,.01)
            if bull:
                invalidation=max(s.vwap if s.vwap<s.price else s.price-.8*atrv,s.price-1.25*atrv)
                if trigger and phase in {"ARMED","TRIGGERED"}: invalidation=min(invalidation, trigger-.15*atrv)
                target=s.price+max(1.8*(s.price-invalidation),1.2*atrv)
                chase=trigger+self.strategy["signals"]["breakout_max_extension_atr"]*atrv if trigger else None
            else:
                invalidation=min(s.vwap if s.vwap>s.price else s.price+.8*atrv,s.price+1.25*atrv)
                if trigger and phase in {"ARMED","TRIGGERED"}: invalidation=max(invalidation, trigger+.15*atrv)
                target=s.price-max(1.8*(invalidation-s.price),1.2*atrv)
                chase=trigger-self.strategy["signals"]["breakout_max_extension_atr"]*atrv if trigger else None
            early=best.name=="BREAKOUT_IGNITION" and phase=="TRIGGERED"
            chase_utilization=None
            if trigger is not None and chase is not None and phase=="TRIGGERED":
                allowance=abs(chase-trigger)
                if allowance>1e-9:
                    progress=(s.price-trigger) if bull else (trigger-s.price)
                    chase_utilization=max(0.0,min(2.0,progress/allowance))
            breakout_like=best.name in {"BREAKOUT_IGNITION","OPENING_RANGE_BREAK","EMA_VWAP_MOMENTUM"}
            regime_eligible=not (self.strategy["signals"].get("regime_filter_enabled",True) and s.market_regime=="CHOPPY" and breakout_like)
            if s.market_regime=="CHOPPY": reasons.append(f"Choppy regime: {s.vwap_crosses_recent} recent VWAP crosses")
            elif s.market_regime=="TRENDING": reasons.append(f"Trending regime: 5m efficiency {s.trend_efficiency_5m:.2f}")
            return DirectionScore(symbol=s.symbol,direction=direction,total=round(total,1),trend=round(trend,1),structure=round(structure,1),
                volume=round(volume,1),momentum=round(momentum,1),levels=round(levels,1),market_context=round(context,1),
                setup=best.name,setup_score=round(setup_value,1),reasons=reasons[:7],invalidation_underlying=round(invalidation,2),
                target_underlying=round(target,2),trigger_underlying=round(trigger,2) if trigger is not None else None,
                chase_limit_underlying=round(chase,2) if chase is not None else None,breakout_phase=phase,early_entry_eligible=early,
                chase_utilization=round(chase_utilization,4) if chase_utilization is not None else None,regime_eligible=regime_eligible)
        return {"CALL":comp("CALL"),"PUT":comp("PUT")}
