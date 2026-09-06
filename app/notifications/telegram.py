from __future__ import annotations

import asyncio
import html
import json
from datetime import datetime

import httpx

from app.models import NewsItem, TradeAlert, TrackedTrade, TradeUpdate


def _pretty_setup(name:str)->str: return name.replace("_"," ").title()
def _pct(value:float|None)->str: return "—" if value is None else f"{value*100:+.1f}%"
def _money(value:float|None)->str: return "—" if value is None else f"${value:.2f}"

def _contract_title(alert:TradeAlert)->str:
    c=alert.contract
    if not c: return "No suitable option contract"
    try: expiry=datetime.fromisoformat(c.expiration).strftime("%d %b %Y")
    except Exception: expiry=c.expiration
    return f"{alert.symbol} {c.strike:g} {alert.direction} • {expiry}"


def format_tracking_html(trade:TrackedTrade|None)->str:
    if trade is None: return ""
    quote=""
    if trade.current_option_bid is not None and trade.current_option_ask is not None:
        quote=f"Current option: <b>${trade.current_option_bid:.2f} / ${trade.current_option_ask:.2f}</b>\n"
    trail=f"Trailing underlying level: <b>${trade.trailing_level_underlying:.2f}</b>\n" if trade.trailing_level_underlying is not None else ""
    return ("\n<b>LIVE STATUS</b>\n"+f"State: <b>{html.escape(trade.status)}</b>\n"+f"Underlying: <b>${trade.current_underlying:.2f}</b>\n"+
            quote+f"Reference move: <b>{_pct(trade.reference_pnl_pct)}</b>\n"+trail+f"Latest: <b>{html.escape(trade.last_message)}</b>")


def format_alert_html(alert:TradeAlert,trade:TrackedTrade|None=None)->str:
    c=alert.contract; emoji="🟢" if alert.direction=="CALL" else "🔴"; setup=html.escape(_pretty_setup(alert.setup)); phase=f" • {alert.breakout_phase}" if alert.breakout_phase!="NONE" else ""
    if c:
        webull_quote=c.quote_source=="webull_openapi"
        quote_label="Webull OpenAPI" if webull_quote else "Alpaca indicative fallback"
        entry_note="Webull ask" if webull_quote else "Alpaca ask; verify in Webull"
        comparison=""
        if webull_quote and c.alpaca_bid is not None and c.alpaca_ask is not None:
            comparison=f"Alpaca indicative comparison: <b>${c.alpaca_bid:.2f} / ${c.alpaca_ask:.2f}</b>"
            if c.quote_discrepancy_pct is not None:
                comparison+=f" • midpoint difference {c.quote_discrepancy_pct:+.1f}%"
            comparison+="\n"
        contract_block=(
            f"<b>BUY TO OPEN</b>\n<b>{html.escape(_contract_title(alert))}</b>\nContract: <code>{html.escape(c.contract_symbol)}</code>\n"
            f"Strike: <b>${c.strike:g}</b> {alert.direction}\nReference entry: <b>{_money(alert.option_entry_reference)}</b> ({entry_note})\n"
            f"Bid / Ask: <b>${c.bid:.2f} / ${c.ask:.2f}</b> • DTE {c.dte} • spread {c.spread_pct:.1f}%\n"
            f"Quote source: <b>{quote_label}</b>\n{comparison}\n"
            f"<b>OPTION PRICE PLAN</b>\nTP1: <b>{_money(alert.option_take_profit_price)}</b>\n"
            +(f"Stretch TP: <b>{_money(alert.option_stretch_price)}</b>\n" if alert.option_stretch_price else "")+
            f"Caution: <b>{_money(alert.option_caution_price)}</b>\nHard premium SL: <b>{_money(alert.option_hard_stop_price)}</b>\n"
        )
    else: contract_block="<b>CONTRACT</b>\nNo suitable contract selected.\n"
    trigger=f"Trigger: <b>${alert.trigger_underlying:.2f}</b>\n" if alert.trigger_underlying is not None else ""
    chase=f"Do not chase beyond: <b>${alert.chase_limit_underlying:.2f}</b>\n" if alert.chase_limit_underlying is not None else ""
    freshness=(f"Market regime: <b>{html.escape(alert.market_regime)}</b>"+
               (f" • chase allowance used: <b>{alert.chase_utilization*100:.0f}%</b>" if alert.chase_utilization is not None else "")+"\n")
    score=f"{alert.direction} evidence {alert.quant_score:.0f}/100 vs opposite {alert.opposite_score:.0f}/100 (edge +{alert.directional_edge:.0f})."
    stretch_zone=f"Stretch zone: <b>+{alert.stretch_target_pct*100:.0f}%</b>\n" if alert.stretch_target_pct is not None else ""
    percentage_plan=(f"Primary option-profit zone: <b>+{alert.take_profit_pct*100:.0f}%</b>\n"+stretch_zone+
                     f"<b>-{alert.premium_caution_pct*100:.0f}%</b> premium: caution only\n"+
                     f"<b>-{alert.emergency_stop_pct*100:.0f}%</b> premium: hard risk cap\n")
    reasons=" • ".join(html.escape(x) for x in alert.reasons[:5])
    ai=alert.ai_review
    if ai.verdict=="APPROVE":
        risk_flags=""
        if ai.risk_flags:
            risk_flags="\nWatch: "+" • ".join(html.escape(x) for x in ai.risk_flags[:3])
        mode = html.escape(ai.review_mode or "FULL")
        latency = f" • {ai.latency_ms}ms" if ai.latency_ms is not None and not ai.cache_hit else (f" • precheck age {ai.precheck_age_ms}ms" if ai.precheck_age_ms is not None else "")
        ai_block=(f"\n<b>AI FINAL CHECK</b>\n✅ <b>APPROVED</b> • {mode}{latency} • review strength {ai.confidence*100:.0f}%\n"
                  f"{html.escape(ai.reason_code)}: {html.escape(ai.summary)}{risk_flags}\n"
                  f"<i>AI review strength is not a probability of winning.</i>\n")
    elif ai.verdict in {"ERROR", "SKIPPED"} and ai.model:
        ai_block=(f"\n<b>AI REVIEW</b>\n⚠️ <b>UNAVAILABLE — DETERMINISTIC FALLBACK</b>\n"
                  f"The setup passed the normal quantitative/market filters; AI infrastructure did not provide a usable verdict.\n")
    else:
        ai_block=""
    quote_disclaimer=("<i>Webull OpenAPI quote was used for the reference entry; live broker prices can still move before you submit an order.</i>"
                      if c and c.quote_source=="webull_openapi" else
                      "<i>Webull option quote was unavailable/not enabled; this alert uses Alpaca indicative pricing, so verify the exact live bid/ask in Webull before entry.</i>")
    return (f"{emoji} <b>{alert.symbol} {alert.direction} — {alert.conviction_label}</b>\n{setup}{phase}\n\n"
            f"<b>ENTRY SETUP</b>\n{trigger}{chase}{freshness}Underlying now: <b>${alert.underlying_price:.2f}</b>\n"
            f"Technical invalidation: <b>${alert.invalidation_underlying:.2f}</b>\nUnderlying target zone: <b>${alert.breakout_target_underlying:.2f}</b>\n\n"
            f"{contract_block}\n<b>DYNAMIC RISK ZONES</b>\n{percentage_plan}\n<b>WHY NOW</b>\n{score}\n{reasons}\n<i>Evidence score is not a probability of winning; it measures evidence strength.</i>\n"
            f"{ai_block}\n<i>Technical invalidation overrides premium percentages.</i>\n{quote_disclaimer}"+format_tracking_html(trade))


def format_trade_update_html(alert:TradeAlert,trade:TrackedTrade,update:TradeUpdate)->str:
    emoji="🛑" if update.severity=="EXIT" else "⚡" if update.severity=="ACTION" else "🟠" if update.severity=="CAUTION" else "🔄"
    text=f"{emoji} <b>{alert.symbol} {alert.direction} — {html.escape(update.headline.upper())}</b>\n{html.escape(_contract_title(alert))}\n\nUnderlying: <b>${update.underlying_price:.2f}</b>\n"
    if update.option_bid is not None and update.option_ask is not None: text+=f"Option: <b>${update.option_bid:.2f} / ${update.option_ask:.2f}</b>\n"
    text+=f"Reference move: <b>{_pct(update.reference_pnl_pct)}</b>\n"
    if update.trailing_level_underlying is not None: text+=f"Trailing level: <b>${update.trailing_level_underlying:.2f}</b>\n"
    return text+f"\n<b>ACTION</b>\n{html.escape(update.action)}\n\n<b>WHY</b>\n{html.escape(update.reason)}"


def format_news_html(item:NewsItem)->str:
    emoji="🚨" if item.impact=="CRITICAL" else "⚠️"; symbols=f" • {', '.join(item.symbols[:5])}" if item.symbols else ""
    return f"{emoji} <b>{item.impact} MARKET NEWS{symbols}</b>\n<b>{html.escape(item.headline)}</b>\nSource: {html.escape(item.source)}\nImpact reason: {html.escape(item.reason)}"


class TelegramNotifier:
    """Persistent multi-user Telegram broadcast bot using one server-side bot token."""
    def __init__(self,token:str,repo,enabled:bool=False,bootstrap_chat_id:str="",public_base_url:str=""):
        self.token=token; self.repo=repo; self.enabled=enabled and bool(token); self.bootstrap_chat_id=bootstrap_chat_id; self.public_base_url=public_base_url
        self.bot_username=""; self._task:asyncio.Task|None=None; self._offset:int|None=None

    @property
    def subscriber_count(self)->int: return self.repo.subscriber_count() if self.enabled else 0

    async def start(self):
        if not self.enabled: return
        async with httpx.AsyncClient(timeout=10) as client:
            r=await client.get(f"https://api.telegram.org/bot{self.token}/getMe"); r.raise_for_status(); self.bot_username=r.json().get("result",{}).get("username","")
        if self.bootstrap_chat_id: self.repo.upsert_subscriber(self.bootstrap_chat_id,active=True)
        self._task=asyncio.create_task(self._poll_updates(),name="telegram-bot")

    async def stop(self):
        if self._task:
            self._task.cancel()
            try: await self._task
            except asyncio.CancelledError: pass
            self._task=None

    async def _send(self,chat_id:str,text:str)->int|None:
        if not self.enabled: return None
        async with httpx.AsyncClient(timeout=15) as client:
            r=await client.post(f"https://api.telegram.org/bot{self.token}/sendMessage",json={"chat_id":str(chat_id),"text":text,"parse_mode":"HTML","disable_web_page_preview":True})
            if r.status_code==403:
                self.repo.set_subscriber_active(str(chat_id),False); return None
            if r.status_code==400:
                detail=r.text.lower()
                if any(x in detail for x in ("chat not found","user is deactivated","bot was blocked")):
                    self.repo.set_subscriber_active(str(chat_id),False); return None
            r.raise_for_status(); payload=r.json()
            return int(payload.get("result",{}).get("message_id")) if payload.get("ok") else None

    async def _poll_updates(self):
        while True:
            try:
                params={"timeout":25,"allowed_updates":json.dumps(["message"],separators=(",",":"))}
                if self._offset is not None: params["offset"]=self._offset
                async with httpx.AsyncClient(timeout=35) as client:
                    r=await client.get(f"https://api.telegram.org/bot{self.token}/getUpdates",params=params); r.raise_for_status(); updates=r.json().get("result",[])
                for update in updates:
                    self._offset=int(update["update_id"])+1; msg=update.get("message") or {}; chat=msg.get("chat") or {}; chat_id=str(chat.get("id", ""))
                    if not chat_id: continue
                    text=(msg.get("text") or "").strip().lower(); user=msg.get("from") or {}; username=user.get("username",""); first_name=user.get("first_name","")
                    if text.startswith("/start") or text.startswith("/subscribe"):
                        self.repo.upsert_subscriber(chat_id,username,first_name,True)
                        dash=f"\nDashboard: {html.escape(self.public_base_url)}" if self.public_base_url else ""
                        await self._send(chat_id,"✅ <b>Subscribed to AI Options Trading Copilot alerts.</b>\nYou will receive qualifying READY setups, material trade updates and high-impact MAG 7 company news. AI review is optional and only appears on alerts when it was used successfully."+dash+"\n\nCommands: /status /stop /help")
                    elif text.startswith("/stop") or text.startswith("/unsubscribe"):
                        self.repo.set_subscriber_active(chat_id,False)
                        async with httpx.AsyncClient(timeout=10) as client:
                            await client.post(f"https://api.telegram.org/bot{self.token}/sendMessage",json={"chat_id":chat_id,"text":"You are unsubscribed. Send /start anytime to re-subscribe."})
                    elif text.startswith("/status"):
                        await self._send(chat_id,f"Bot online ✅\nActive subscribers: <b>{self.subscriber_count}</b>\nAlerts are recommendation-only; no broker order is placed.")
                    elif text.startswith("/help"):
                        await self._send(chat_id,"<b>Commands</b>\n/start — subscribe\n/stop — unsubscribe\n/status — bot status\n/help — this message")
            except asyncio.CancelledError: raise
            except Exception as exc:
                print(f"Telegram polling error: {exc}"); await asyncio.sleep(3)

    async def send_alert(self,alert:TradeAlert,trade:TrackedTrade|None=None)->int|None:
        first=None
        for sub in self.repo.active_subscribers():
            try:
                mid=await self._send(sub["chat_id"],format_alert_html(alert,trade))
                if mid is not None:
                    self.repo.save_telegram_message(alert.id,sub["chat_id"],mid); first=first or mid
            except Exception as exc: print(f"Telegram alert failed for chat {sub['chat_id']}: {exc}")
        return first

    async def edit_alert(self,alert:TradeAlert,trade:TrackedTrade)->None:
        if not self.enabled: return
        text=format_alert_html(alert,trade)
        async with httpx.AsyncClient(timeout=15) as client:
            for row in self.repo.telegram_messages(alert.id):
                try:
                    r=await client.post(f"https://api.telegram.org/bot{self.token}/editMessageText",json={"chat_id":row["chat_id"],"message_id":row["message_id"],"text":text,"parse_mode":"HTML","disable_web_page_preview":True})
                    if r.status_code==400 and "message is not modified" in r.text.lower(): continue
                    if r.status_code==403: self.repo.set_subscriber_active(str(row["chat_id"]),False); continue
                    r.raise_for_status()
                except Exception as exc: print(f"Telegram edit failed for chat {row['chat_id']}: {exc}")

    async def send_trade_update(self,alert:TradeAlert,trade:TrackedTrade,update:TradeUpdate)->None:
        text=format_trade_update_html(alert,trade,update)
        for sub in self.repo.active_subscribers():
            try: await self._send(sub["chat_id"],text)
            except Exception as exc: print(f"Telegram update failed for chat {sub['chat_id']}: {exc}")

    async def send_news(self,item:NewsItem)->None:
        text=format_news_html(item)
        for sub in self.repo.active_subscribers():
            try: await self._send(sub["chat_id"],text)
            except Exception as exc: print(f"Telegram news failed for chat {sub['chat_id']}: {exc}")
