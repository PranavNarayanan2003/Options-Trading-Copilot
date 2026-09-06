from datetime import datetime, timezone

from app.storage.db import AlertRepository


def test_signal_observation_research_log_round_trip(tmp_path):
    repo = AlertRepository(tmp_path / "research.sqlite3")
    ts = datetime(2026, 8, 17, 14, 45, tzinfo=timezone.utc)
    repo.log_signal_observation(
        ts, "NVDA", "CALL", "REJECT_SCORE", "79 < 80",
        {"market_regime": "CHOPPY", "directional_edge": 44.0},
    )
    rows = repo.research_rows()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "NVDA"
    assert rows[0]["decision"] == "REJECT_SCORE"
    assert rows[0]["payload"]["market_regime"] == "CHOPPY"
