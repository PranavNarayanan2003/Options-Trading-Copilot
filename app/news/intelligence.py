from __future__ import annotations

import asyncio
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Awaitable, Callable

import httpx
import websockets

from app.models import EconomicEvent, NewsItem

NewsCallback = Callable[[NewsItem], Awaitable[None]]


class NewsIntelligence:
    """Automatic market-news + scheduled-event awareness.

    This module never creates trade direction. It classifies event risk and lets
    the deterministic signal engine raise its minimum score threshold.
    """

    ALPACA_NEWS_URL = "wss://stream.data.alpaca.markets/v1beta1/news"
    FED_RSS = (
        "https://www.federalreserve.gov/feeds/press_monetary.xml",
        "https://www.federalreserve.gov/feeds/speeches.xml",
        "https://www.federalreserve.gov/feeds/testimony.xml",
    )
    BLS_ICS = "https://www.bls.gov/schedule/news_release/bls.ics"

    CRITICAL_TERMS = (
        "fomc statement", "rate decision", "emergency rate", "cpi", "consumer price index",
        "nonfarm payroll", "non-farm payroll", "employment situation", "jobs report",
        "pce inflation", "core pce", "federal reserve cuts", "federal reserve raises",
        "tariff", "sanction", "military strike", "war", "ceasefire", "bank failure",
    )
    HIGH_TERMS = (
        "federal reserve", "fed chair", "federal reserve chair", "chairman", "powell", "fomc", "interest rate",
        "inflation", "producer price", "ppi", "unemployment", "jobless claims", "treasury yield",
        "white house", "president", "executive order", "administration", "trade deal", "section 301",
        "earnings", "guidance", "revenue", "eps", "sec investigation", "antitrust",
        "downgrade", "upgrade", "acquisition", "merger", "bankruptcy",
    )
    MEDIUM_TERMS = (
        "federal reserve governor", "fed governor", "treasury", "beige book", "gdp", "ism",
        "consumer sentiment", "retail sales", "housing", "oil", "opec", "china", "taiwan",
    )

    def __init__(self, settings, strategy: dict, symbols: list[str], on_news: NewsCallback | None = None):
        self.settings = settings
        self.strategy = strategy
        self.symbols = set(symbols)
        self.on_news = on_news
        self.items: list[NewsItem] = []
        self.events: list[EconomicEvent] = []
        self.connected = False
        self.calendar_connected = False
        self.tasks: list[asyncio.Task] = []
        self._seen: dict[str, datetime] = {}
        self._last_risk_reason = "No active macro/news restriction."

    async def start(self):
        if not self.settings.enable_news:
            return
        self.tasks = [asyncio.create_task(self._poll_official_sources(), name="official-macro-sources")]
        if self.settings.alpaca_api_key and self.settings.alpaca_api_secret:
            self.tasks.append(asyncio.create_task(self._run_alpaca_news(), name="alpaca-news"))
        if self.settings.trading_economics_api_key:
            self.tasks.append(asyncio.create_task(self._poll_trading_economics(), name="economic-calendar"))

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.tasks = []

    @staticmethod
    def _parse_dt(value: str | datetime | None) -> datetime:
        if isinstance(value, datetime):
            dt = value
        elif value:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        else:
            dt = datetime.now(timezone.utc)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)

    def _classify(self, headline: str, summary: str, symbols: list[str], source: str) -> tuple[str, str, str]:
        text = f"{headline} {summary}".lower()
        matched_symbols = [s for s in symbols if s in self.symbols]
        if any(term in text for term in self.CRITICAL_TERMS):
            category = "macro" if not matched_symbols else "stock+macro"
            return "CRITICAL", category, "High-impact macro/policy or market-shock keyword detected."
        if any(term in text for term in self.HIGH_TERMS):
            category = "stock" if matched_symbols else "macro"
            return "HIGH", category, "Market-moving policy/company keyword detected."
        if matched_symbols:
            return "MEDIUM", "stock", f"Headline directly mentions monitored symbol(s): {', '.join(matched_symbols[:4])}."
        if any(term in text for term in self.MEDIUM_TERMS):
            return "MEDIUM", "macro", "Macro-sensitive headline detected."
        return "LOW", "market", "General market news."

    async def _ingest(self, item: NewsItem):
        now = datetime.now(timezone.utc)
        dedupe_minutes = int(self.strategy.get("news", {}).get("headline_dedupe_minutes", 180))
        key = re.sub(r"\W+", " ", item.headline.lower()).strip()
        old = self._seen.get(key)
        if old and now - old < timedelta(minutes=dedupe_minutes):
            return
        self._seen[key] = now
        self.items.insert(0, item)
        self.items = self.items[: int(self.strategy.get("news", {}).get("max_dashboard_items", 20))]
        cutoff = now - timedelta(minutes=dedupe_minutes * 2)
        self._seen = {k: v for k, v in self._seen.items() if v >= cutoff}
        if self.on_news and item.impact in {"HIGH", "CRITICAL"} and now - item.created_at <= timedelta(minutes=10):
            await self.on_news(item)

    async def _run_alpaca_news(self):
        backoff = 1
        while True:
            try:
                async with websockets.connect(self.ALPACA_NEWS_URL, ping_interval=20, ping_timeout=20) as ws:
                    await ws.send(json.dumps({"action": "auth", "key": self.settings.alpaca_api_key, "secret": self.settings.alpaca_api_secret}))
                    await ws.recv()
                    await ws.send(json.dumps({"action": "subscribe", "news": ["*"]}))
                    await ws.recv()
                    self.connected = True
                    backoff = 1
                    async for raw in ws:
                        messages = json.loads(raw)
                        if isinstance(messages, dict):
                            messages = [messages]
                        for msg in messages:
                            if msg.get("T") != "n":
                                continue
                            symbols = list(msg.get("symbols") or [])
                            headline = msg.get("headline", "")
                            summary = re.sub("<[^>]+>", "", msg.get("summary", ""))
                            impact, category, reason = self._classify(headline, summary, symbols, msg.get("source", "alpaca"))
                            if impact == "LOW" and not any(s in self.symbols for s in symbols):
                                continue
                            await self._ingest(NewsItem(
                                id=f"alpaca-{msg.get('id', hashlib.sha1(raw.encode()).hexdigest()[:12])}",
                                created_at=self._parse_dt(msg.get("created_at")), headline=headline or "Untitled market news",
                                summary=summary[:600], source=msg.get("source", "Alpaca"), url=msg.get("url", ""),
                                symbols=symbols, category=category, impact=impact, reason=reason,
                            ))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected = False
                print(f"News WebSocket reconnecting after error: {exc}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _poll_official_sources(self):
        while True:
            try:
                async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": "AI-Options-Copilot/1.3"}) as client:
                    for url in self.FED_RSS:
                        response = await client.get(url)
                        response.raise_for_status()
                        await self._parse_rss(response.text, "Federal Reserve")
                    bls = await client.get(self.BLS_ICS)
                    bls.raise_for_status()
                    self._parse_bls_ics(bls.text)
                    self.calendar_connected = True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"Official macro-source poll failed: {exc}")
            await asyncio.sleep(300)

    async def _parse_rss(self, text: str, source: str):
        root = ET.fromstring(text)
        for entry in root.findall(".//item")[:20]:
            title = (entry.findtext("title") or "").strip()
            link = (entry.findtext("link") or "").strip()
            desc = re.sub("<[^>]+>", "", entry.findtext("description") or "").strip()
            pub = entry.findtext("pubDate") or entry.findtext("date")
            try:
                created = parsedate_to_datetime(pub).astimezone(timezone.utc) if pub else datetime.now(timezone.utc)
            except Exception:
                created = datetime.now(timezone.utc)
            impact, category, reason = self._classify(title, desc, [], source)
            if source == "Federal Reserve" and impact == "LOW":
                impact, category, reason = "MEDIUM", "macro", "Official Federal Reserve publication."
            await self._ingest(NewsItem(
                id=f"rss-{hashlib.sha1((source+title+link).encode()).hexdigest()[:16]}", created_at=created,
                headline=title, summary=desc[:600], source=source, url=link, symbols=[],
                category=category, impact=impact, reason=reason,
            ))

    def _parse_bls_ics(self, text: str):
        events: list[EconomicEvent] = []
        chunks = text.replace("\r\n ", "").split("BEGIN:VEVENT")
        for chunk in chunks[1:]:
            if "END:VEVENT" not in chunk:
                continue
            fields: dict[str, str] = {}
            for line in chunk.splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    fields[key] = value
            summary = next((v for k, v in fields.items() if k.startswith("SUMMARY")), "BLS release")
            raw_dt = next((v for k, v in fields.items() if k.startswith("DTSTART")), "")
            if not raw_dt:
                continue
            try:
                if raw_dt.endswith("Z"):
                    dt = datetime.strptime(raw_dt, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                else:
                    from zoneinfo import ZoneInfo
                    fmt = "%Y%m%dT%H%M%S" if "T" in raw_dt else "%Y%m%d"
                    dt = datetime.strptime(raw_dt, fmt).replace(tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
            except ValueError:
                continue
            low = summary.lower()
            importance = 3 if any(x in low for x in ("consumer price", "employment situation", "producer price", "employment cost")) else 2
            events.append(EconomicEvent(
                id=f"bls-{hashlib.sha1((summary+dt.isoformat()).encode()).hexdigest()[:12]}",
                event_at=dt, name=summary.replace("\\,", ","), category="BLS release", importance=importance,
                source="U.S. Bureau of Labor Statistics",
            ))
        now = datetime.now(timezone.utc)
        future = [e for e in events if now - timedelta(minutes=30) <= e.event_at <= now + timedelta(days=14)]
        others = [e for e in self.events if e.source != "U.S. Bureau of Labor Statistics"]
        self.events = sorted(others + future, key=lambda e: e.event_at)[:80]

    async def _poll_trading_economics(self):
        while True:
            try:
                now = datetime.now(timezone.utc)
                d1 = (now - timedelta(days=1)).date().isoformat()
                d2 = (now + timedelta(days=14)).date().isoformat()
                key = self.settings.trading_economics_api_key
                async with httpx.AsyncClient(timeout=25) as client:
                    econ = await client.get(
                        f"https://api.tradingeconomics.com/calendar/country/united%20states/{d1}/{d2}",
                        params={"c": key, "f": "json"},
                    )
                    econ.raise_for_status()
                    earnings = await client.get(
                        "https://api.tradingeconomics.com/earnings-revenues",
                        params={"c": key, "d1": d1, "d2": d2, "f": "json"},
                    )
                    earnings.raise_for_status()
                te_events: list[EconomicEvent] = []
                for row in econ.json() or []:
                    try:
                        importance = int(row.get("Importance") or 1)
                    except Exception:
                        importance = 1
                    if importance < 2:
                        continue
                    raw = row.get("Date") or row.get("date")
                    if not raw:
                        continue
                    dt = self._parse_dt(raw)
                    te_events.append(EconomicEvent(
                        id=f"te-{row.get('CalendarId') or hashlib.sha1(str(row).encode()).hexdigest()[:12]}",
                        event_at=dt, name=row.get("Event") or row.get("Category") or "U.S. economic event",
                        category=row.get("Category") or "Economic calendar", importance=importance,
                        source="Trading Economics",
                    ))
                from zoneinfo import ZoneInfo
                ny = ZoneInfo("America/New_York")
                for row in earnings.json() or []:
                    symbol_raw = str(row.get("Symbol") or "")
                    symbol = symbol_raw.split(":", 1)[0].upper()
                    if symbol not in self.symbols:
                        continue
                    raw = row.get("Date") or row.get("date")
                    if not raw:
                        continue
                    base = self._parse_dt(raw).astimezone(ny)
                    release = str(row.get("MarketRelease") or row.get("marketRelease") or "").lower()
                    # Provider sometimes gives only date/session labels. Approximate
                    # before/after-market timestamps solely for risk-window gating.
                    hour, minute = (8, 0) if "before" in release else (16, 5) if "after" in release else (12, 0)
                    dt = base.replace(hour=hour, minute=minute, second=0, microsecond=0).astimezone(timezone.utc)
                    te_events.append(EconomicEvent(
                        id=f"te-earn-{hashlib.sha1((symbol+dt.isoformat()).encode()).hexdigest()[:12]}",
                        event_at=dt, name=f"{symbol} earnings", category="earnings", importance=3,
                        source="Trading Economics", symbols=[symbol],
                    ))
                non_te = [e for e in self.events if e.source != "Trading Economics"]
                self.events = sorted(non_te + te_events, key=lambda e: e.event_at)[:100]
                self.calendar_connected = True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"Trading Economics poll failed: {exc}")
            await asyncio.sleep(self.settings.economic_calendar_poll_seconds)

    @staticmethod
    def _rank(mode: str) -> int:
        return {"normal": 0, "caution": 1, "high": 2}.get(mode, 0)

    def risk_mode(self, now: datetime | None = None) -> tuple[str, str]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cfg = self.strategy["macro"]
        mode, reason = "normal", "No active macro/news restriction."
        for event in self.events:
            if event.symbols:
                continue
            delta = (event.event_at - now).total_seconds() / 60
            if event.importance >= 3 and -cfg["high_event_minutes_after"] <= delta <= cfg["high_event_minutes_before"]:
                return "high", f"High-impact event: {event.name} ({delta:+.0f} min)."
            if event.importance >= 2 and -cfg["caution_event_minutes_after"] <= delta <= cfg["caution_event_minutes_before"]:
                if self._rank("caution") > self._rank(mode):
                    mode, reason = "caution", f"Scheduled event nearby: {event.name} ({delta:+.0f} min)."
        for item in self.items:
            age = (now - item.created_at).total_seconds() / 60
            if age < 0:
                continue
            if item.impact == "CRITICAL" and age <= cfg["critical_news_hold_minutes"]:
                return "high", f"Critical headline: {item.headline[:110]}"
            if item.impact == "HIGH" and age <= cfg["high_news_hold_minutes"] and not item.symbols:
                mode, reason = "caution", f"High-impact market headline: {item.headline[:110]}"
        return mode, reason

    def symbol_risk_mode(self, symbol: str, now: datetime | None = None) -> tuple[str, str]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        symbol = symbol.upper()
        mode, reason = "normal", "No symbol-specific event restriction."
        for event in self.events:
            if symbol not in event.symbols:
                continue
            delta = (event.event_at - now).total_seconds() / 60
            if -60 <= delta <= 60:
                return "high", f"{symbol} scheduled event: {event.name}."
            if -120 <= delta <= 480:
                mode, reason = "caution", f"{symbol} earnings/event window: {event.name}."
        cfg = self.strategy["macro"]
        for item in self.items:
            if symbol not in item.symbols:
                continue
            age = (now - item.created_at).total_seconds() / 60
            if item.impact in {"HIGH", "CRITICAL"} and 0 <= age <= max(cfg["high_news_hold_minutes"], 30):
                return "high", f"{symbol} high-impact headline: {item.headline[:100]}"
            if item.impact == "MEDIUM" and 0 <= age <= 30:
                mode, reason = "caution", f"{symbol} recent company headline: {item.headline[:100]}"
        return mode, reason

    def upcoming(self, hours: int = 24) -> list[EconomicEvent]:
        now = datetime.now(timezone.utc)
        return [e for e in sorted(self.events, key=lambda x: x.event_at) if now - timedelta(minutes=15) <= e.event_at <= now + timedelta(hours=hours)]
