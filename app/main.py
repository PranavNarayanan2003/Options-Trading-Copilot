from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import load_settings
from app.engine import TradingEngine
from app.execution.webull import WebullExecutionAdapter

settings=load_settings(); engine=TradingEngine(settings); webull_execution=WebullExecutionAdapter(enabled=False)
ROOT=Path(__file__).resolve().parents[1]

@asynccontextmanager
async def lifespan(app:FastAPI):
    await engine.start(); yield; await engine.stop()

app=FastAPI(title="AI Options Trading Copilot V1.4.2",version="1.4.2",lifespan=lifespan)
app.mount("/static",StaticFiles(directory=ROOT/"app"/"static"),name="static")

@app.get("/",response_class=HTMLResponse)
async def dashboard(): return (ROOT/"app"/"templates"/"index.html").read_text(encoding="utf-8")

@app.get("/api/state")
async def state(): return engine.dashboard_state()

def _require_admin(token:str|None):
    if not settings.admin_token: raise HTTPException(403,"Admin API disabled until ADMIN_TOKEN is configured")
    if not token or not secrets.compare_digest(token,settings.admin_token): raise HTTPException(403,"Invalid admin token")

class MacroMode(BaseModel): mode:str
@app.post("/api/admin/macro-risk")
async def set_macro_risk(payload:MacroMode,x_admin_token:str|None=Header(default=None)):
    _require_admin(x_admin_token)
    try: engine.set_macro_risk(payload.mode)
    except ValueError as exc: raise HTTPException(400,str(exc))
    return {"ok":True,"mode":engine.macro_risk_mode,"reason":engine.macro_risk_reason}

class ConfirmEntry(BaseModel): option_fill_price:float
@app.post("/api/admin/tracking/{alert_id}/confirm-entry")
async def confirm_entry(alert_id:str,payload:ConfirmEntry,x_admin_token:str|None=Header(default=None)):
    _require_admin(x_admin_token)
    try: trade=await engine.confirm_entry(alert_id,payload.option_fill_price)
    except ValueError as exc: raise HTTPException(400,str(exc))
    return {"ok":True,"trade":trade}

@app.get("/api/admin/webull/positions")
async def webull_positions(x_admin_token:str|None=Header(default=None)):
    _require_admin(x_admin_token)
    return {"enabled":settings.webull_sync_enabled,"connected":engine.webull.state.connected,"last_sync_at":engine.webull.state.last_sync_at,"error":engine.webull.state.error,"positions":engine.webull.positions}

@app.get("/api/execution-preview/{alert_id}")
async def execution_preview(alert_id:str):
    alert=next((a for a in engine.alerts if a.id==alert_id),None)
    if not alert: raise HTTPException(404,"alert not found")
    return {"execution_enabled":False,"preview":webull_execution.build_preview(alert),"message":"V1.4.2 remains recommendation-only; no broker order can be submitted."}

@app.get("/health")
async def health():
    active=len([t for t in engine.tracked_trades if not t.terminal])
    return {"ok":True,"mode":settings.data_mode,"symbols":settings.symbols,"execution_enabled":False,"active_trade_monitors":active,
            "telegram_enabled":engine.telegram.enabled,"telegram_subscribers":engine.telegram.subscriber_count,"news_enabled":settings.enable_news,
            "news_stream_connected":engine.news.connected,"economic_calendar_connected":engine.news.calendar_connected,
            "webull_sync_enabled":settings.webull_sync_enabled,"webull_sync_connected":engine.webull.state.connected,
            "webull_quotes_enabled":settings.webull_quotes_enabled,"webull_quotes_required":settings.webull_quotes_required_for_ready,
            "ai_review_enabled":settings.enable_ai_review,"ai_review_required":engine._ai_required_for_ready(),"ai_policy":engine._ai_policy(),"ai_fail_open_on_error":settings.ai_fail_open_on_error,"ai_model":settings.openai_model,
            "ai_reasoning_effort":settings.openai_reasoning_effort,
            "ai_precheck_enabled":bool(settings.strategy.get("ai",{}).get("armed_precheck_enabled",True)),
            "ai_precheck_cache_entries":len(engine.ai_prechecks),
            "version":"1.4.2"}
