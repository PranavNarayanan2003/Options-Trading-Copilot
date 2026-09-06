import pytest


@pytest.fixture(autouse=True)
def _isolate_tests_from_local_dotenv(monkeypatch, tmp_path):
    """Keep automated tests deterministic regardless of the user's real .env.

    app.config loads the project .env at import time. Without this fixture, a
    developer running pytest with DATA_MODE=live / AI / Webull enabled can make
    otherwise hermetic unit tests enter real network/fail-closed paths. Tests
    that explicitly need live/AI/Webull behavior override these values inside
    the individual test after this fixture runs.
    """
    monkeypatch.setenv("DATA_MODE", "demo")
    monkeypatch.setenv("ENABLE_AI_REVIEW", "false")
    monkeypatch.setenv("AI_REQUIRED_FOR_READY", "false")
    monkeypatch.setenv("AI_FAIL_OPEN_ON_ERROR", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ENABLE_WEBULL_QUOTES", "false")
    monkeypatch.setenv("WEBULL_QUOTES_REQUIRED_FOR_READY", "false")
    monkeypatch.setenv("ENABLE_WEBULL_SYNC", "false")
    monkeypatch.setenv("WEBULL_APP_KEY", "")
    monkeypatch.setenv("WEBULL_APP_SECRET", "")
    monkeypatch.setenv("WEBULL_API_ENDPOINT", "")
    monkeypatch.setenv("WEBULL_ACCESS_TOKEN", "")
    monkeypatch.setenv("ENABLE_TELEGRAM", "false")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("ALPACA_API_KEY", "")
    monkeypatch.setenv("ALPACA_API_SECRET", "")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "isolated-tests.sqlite3"))
