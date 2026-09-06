from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.models import AICounterfactual, TradeAlert, TrackedTrade


class AlertRepository:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self):
        con = sqlite3.connect(self.path, timeout=20)
        con.row_factory = sqlite3.Row
        return con

    def _init(self):
        with self._connect() as con:
            con.execute("CREATE TABLE IF NOT EXISTS alerts (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL)")
            con.execute("CREATE TABLE IF NOT EXISTS tracked_trades (alert_id TEXT PRIMARY KEY, updated_at TEXT NOT NULL, payload TEXT NOT NULL)")
            con.execute("""
                CREATE TABLE IF NOT EXISTS telegram_subscribers (
                    chat_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL DEFAULT '',
                    first_name TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS telegram_messages (
                    alert_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    PRIMARY KEY(alert_id, chat_id)
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS signal_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observed_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    alert_id TEXT,
                    payload TEXT NOT NULL
                )
            """)
            con.execute("CREATE INDEX IF NOT EXISTS idx_signal_observations_time ON signal_observations(observed_at)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_signal_observations_symbol ON signal_observations(symbol, observed_at)")
            con.execute("""
                CREATE TABLE IF NOT EXISTS ai_counterfactuals (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    terminal INTEGER NOT NULL DEFAULT 0,
                    payload TEXT NOT NULL
                )
            """)
            con.execute("CREATE INDEX IF NOT EXISTS idx_ai_counterfactuals_time ON ai_counterfactuals(created_at)")
            con.execute("""
                CREATE TABLE IF NOT EXISTS trade_outcomes (
                    alert_id TEXT PRIMARY KEY,
                    closed_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    pnl_pct REAL,
                    max_pnl_pct REAL,
                    min_pnl_pct REAL,
                    shadow INTEGER NOT NULL DEFAULT 0,
                    payload TEXT NOT NULL
                )
            """)
            con.commit()

    def save(self, alert: TradeAlert):
        with self._connect() as con:
            con.execute("INSERT OR REPLACE INTO alerts(id, created_at, payload) VALUES(?,?,?)",
                        (alert.id, alert.created_at.isoformat(), alert.model_dump_json()))
            con.commit()

    def recent(self, limit: int = 30) -> list[TradeAlert]:
        with self._connect() as con:
            rows = con.execute("SELECT payload FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [TradeAlert.model_validate_json(r[0]) for r in rows]

    def save_tracked(self, trade: TrackedTrade):
        with self._connect() as con:
            con.execute("INSERT OR REPLACE INTO tracked_trades(alert_id, updated_at, payload) VALUES(?,?,?)",
                        (trade.alert_id, trade.updated_at.isoformat(), trade.model_dump_json()))
            con.commit()

    def tracked(self, limit: int = 30, active_only: bool = False) -> list[TrackedTrade]:
        with self._connect() as con:
            rows = con.execute("SELECT payload FROM tracked_trades ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        trades = [TrackedTrade.model_validate_json(r[0]) for r in rows]
        if active_only:
            trades = [t for t in trades if not t.terminal and t.status not in {"DISMISSED", "EXPIRED"}]
        return trades

    def log_signal_observation(
        self,
        observed_at: datetime,
        symbol: str,
        direction: str,
        decision: str,
        reason: str,
        payload: dict,
        alert_id: str | None = None,
    ):
        with self._connect() as con:
            con.execute(
                "INSERT INTO signal_observations(observed_at,symbol,direction,decision,reason,alert_id,payload) VALUES(?,?,?,?,?,?,?)",
                (observed_at.isoformat(), symbol, direction, decision, reason, alert_id, json.dumps(payload, separators=(",", ":"), default=str)),
            )
            con.commit()


    def save_ai_counterfactual(self, item: AICounterfactual):
        with self._connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO ai_counterfactuals(id,created_at,updated_at,terminal,payload) VALUES(?,?,?,?,?)",
                (item.id, item.created_at.isoformat(), item.updated_at.isoformat(), 1 if item.terminal else 0, item.model_dump_json()),
            )
            con.commit()

    def ai_counterfactuals(self, limit: int = 2000, active_only: bool = False) -> list[AICounterfactual]:
        query = "SELECT payload FROM ai_counterfactuals"
        params: list[object] = []
        if active_only:
            query += " WHERE terminal=0"
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as con:
            rows = con.execute(query, params).fetchall()
        return [AICounterfactual.model_validate_json(r[0]) for r in rows]

    def counterfactual_rows(self, limit: int = 10000) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT id,created_at,updated_at,terminal,payload FROM ai_counterfactuals ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out=[]
        for row in rows:
            item=dict(row)
            try:
                payload=json.loads(item["payload"]); item.update({f"cf_{k}":v for k,v in payload.items()}); item["payload"]=payload
            except Exception:
                pass
            out.append(item)
        return out

    def save_trade_outcome(self, alert: TradeAlert, trade: TrackedTrade):
        pnl = trade.reference_pnl_pct
        outcome = "WIN" if pnl is not None and pnl > 0 else "LOSS" if pnl is not None and pnl < 0 else "FLAT"
        payload = {
            "alert": alert.model_dump(mode="json"),
            "trade": trade.model_dump(mode="json"),
        }
        closed_at = trade.updated_at or datetime.now(timezone.utc)
        with self._connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO trade_outcomes(
                    alert_id,closed_at,symbol,direction,outcome,pnl_pct,max_pnl_pct,min_pnl_pct,shadow,payload
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    alert.id, closed_at.isoformat(), alert.symbol, alert.direction, outcome, pnl,
                    trade.max_reference_pnl_pct, trade.min_reference_pnl_pct, 1 if trade.shadow else 0,
                    json.dumps(payload, separators=(",", ":"), default=str),
                ),
            )
            con.commit()

    def research_rows(self, limit: int = 5000) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT observed_at,symbol,direction,decision,reason,alert_id,payload FROM signal_observations ORDER BY observed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out=[]
        for row in rows:
            item=dict(row)
            try: item["payload"]=json.loads(item["payload"])
            except Exception: pass
            out.append(item)
        return out

    def outcome_rows(self, limit: int = 2000) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT alert_id,closed_at,symbol,direction,outcome,pnl_pct,max_pnl_pct,min_pnl_pct,shadow,payload FROM trade_outcomes ORDER BY closed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_subscriber(self, chat_id: str, username: str = "", first_name: str = "", active: bool = True):
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as con:
            con.execute("""
                INSERT INTO telegram_subscribers(chat_id, username, first_name, active, created_at, updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    username=excluded.username,
                    first_name=excluded.first_name,
                    active=excluded.active,
                    updated_at=excluded.updated_at
            """, (str(chat_id), username or "", first_name or "", 1 if active else 0, now, now))
            con.commit()

    def set_subscriber_active(self, chat_id: str, active: bool):
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as con:
            con.execute("UPDATE telegram_subscribers SET active=?, updated_at=? WHERE chat_id=?",
                        (1 if active else 0, now, str(chat_id)))
            con.commit()

    def active_subscribers(self) -> list[dict]:
        with self._connect() as con:
            rows = con.execute("SELECT chat_id, username, first_name FROM telegram_subscribers WHERE active=1 ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    def subscriber_count(self) -> int:
        with self._connect() as con:
            row = con.execute("SELECT COUNT(*) FROM telegram_subscribers WHERE active=1").fetchone()
        return int(row[0])

    def save_telegram_message(self, alert_id: str, chat_id: str, message_id: int):
        with self._connect() as con:
            con.execute("INSERT OR REPLACE INTO telegram_messages(alert_id, chat_id, message_id) VALUES(?,?,?)",
                        (alert_id, str(chat_id), int(message_id)))
            con.commit()

    def telegram_messages(self, alert_id: str) -> list[dict]:
        with self._connect() as con:
            rows = con.execute("SELECT chat_id, message_id FROM telegram_messages WHERE alert_id=?", (alert_id,)).fetchall()
        return [dict(r) for r in rows]
