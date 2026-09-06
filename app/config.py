from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    data_mode: str
    alpaca_api_key: str
    alpaca_api_secret: str
    alpaca_stock_feed: str
    alpaca_option_feed: str
    openai_api_key: str
    openai_model: str
    openai_reasoning_effort: str
    openai_timeout_seconds: float
    enable_ai_review: bool
    ai_required_for_ready: bool
    ai_fail_open_on_error: bool
    enable_telegram: bool
    telegram_bot_token: str
    telegram_bootstrap_chat_id: str
    enable_broker_execution: bool
    admin_token: str
    public_base_url: str
    database_path: str
    enable_news: bool
    trading_economics_api_key: str
    economic_calendar_poll_seconds: int
    webull_sync_enabled: bool
    webull_app_key: str
    webull_app_secret: str
    webull_account_id: str
    webull_region: str
    webull_api_endpoint: str
    webull_access_token: str
    webull_poll_seconds: int
    webull_quotes_enabled: bool
    webull_quotes_required_for_ready: bool
    webull_option_snapshot_path: str
    webull_max_quote_age_seconds: int
    strategy: dict[str, Any]

    @property
    def symbols(self) -> list[str]:
        return list(self.strategy["universe"])


def load_settings() -> Settings:
    with open(ROOT / "strategy.yaml", "r", encoding="utf-8") as fh:
        strategy = yaml.safe_load(fh)
    ai_cfg = strategy.get("ai", {})
    return Settings(
        data_mode=os.getenv("DATA_MODE", "demo").strip().lower(),
        alpaca_api_key=os.getenv("ALPACA_API_KEY", "").strip(),
        alpaca_api_secret=os.getenv("ALPACA_API_SECRET", "").strip(),
        alpaca_stock_feed=os.getenv("ALPACA_STOCK_FEED", "iex").strip(),
        alpaca_option_feed=os.getenv("ALPACA_OPTION_FEED", "indicative").strip(),
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-5.6-terra").strip(),
        openai_reasoning_effort=os.getenv("OPENAI_REASONING_EFFORT", "none").strip().lower(),
        openai_timeout_seconds=max(1.0, float(os.getenv("OPENAI_TIMEOUT_SECONDS", "3"))),
        enable_ai_review=_bool("ENABLE_AI_REVIEW"),
        ai_required_for_ready=_bool("AI_REQUIRED_FOR_READY", bool(ai_cfg.get("required_for_live_ready", False))),
        ai_fail_open_on_error=_bool("AI_FAIL_OPEN_ON_ERROR", bool(ai_cfg.get("fail_open_on_error", True))),
        enable_telegram=_bool("ENABLE_TELEGRAM"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_bootstrap_chat_id=(os.getenv("TELEGRAM_BOOTSTRAP_CHAT_ID", "").strip() or os.getenv("TELEGRAM_CHAT_ID", "").strip()),
        enable_broker_execution=_bool("ENABLE_BROKER_EXECUTION"),
        admin_token=os.getenv("ADMIN_TOKEN", "").strip(),
        public_base_url=os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/"),
        database_path=os.getenv("DATABASE_PATH", "").strip() or str(ROOT / "alerts.sqlite3"),
        enable_news=_bool("ENABLE_NEWS", True),
        trading_economics_api_key=os.getenv("TRADING_ECONOMICS_API_KEY", "").strip(),
        economic_calendar_poll_seconds=max(60, int(os.getenv("ECONOMIC_CALENDAR_POLL_SECONDS", "600"))),
        webull_sync_enabled=_bool("ENABLE_WEBULL_SYNC"),
        webull_app_key=os.getenv("WEBULL_APP_KEY", "").strip(),
        webull_app_secret=os.getenv("WEBULL_APP_SECRET", "").strip(),
        webull_account_id=os.getenv("WEBULL_ACCOUNT_ID", "").strip(),
        webull_region=os.getenv("WEBULL_REGION", "sg").strip(),
        webull_api_endpoint=os.getenv("WEBULL_API_ENDPOINT", "").strip().replace("https://", "").replace("http://", "").rstrip("/"),
        webull_access_token=os.getenv("WEBULL_ACCESS_TOKEN", "").strip(),
        webull_poll_seconds=max(5, int(os.getenv("WEBULL_POLL_SECONDS", "15"))),
        webull_quotes_enabled=_bool("ENABLE_WEBULL_QUOTES"),
        webull_quotes_required_for_ready=_bool("WEBULL_QUOTES_REQUIRED_FOR_READY"),
        webull_option_snapshot_path=os.getenv("WEBULL_OPTION_SNAPSHOT_PATH", "/market-data/options/snapshots/list").strip() or "/market-data/options/snapshots/list",
        webull_max_quote_age_seconds=max(1, int(os.getenv("WEBULL_MAX_QUOTE_AGE_SECONDS", "20"))),
        strategy=strategy,
    )
