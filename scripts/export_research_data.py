from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from app.config import load_settings
from app.storage.db import AlertRepository


def _write_csv(path: Path, rows: list[dict]):
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key); keys.append(key)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            clean = dict(row)
            for key, value in list(clean.items()):
                if isinstance(value, (dict, list)):
                    clean[key] = json.dumps(value, separators=(",", ":"), default=str)
            writer.writerow(clean)


def _parse_payload(value):
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value or "{}")
    except Exception:
        return {}


def _effectiveness_summary(outcomes: list[dict], counterfactuals: list[dict]) -> list[dict]:
    approved = []
    for row in outcomes:
        payload = _parse_payload(row.get("payload"))
        alert = payload.get("alert") or {}
        review = alert.get("ai_review") or {}
        if review.get("verdict") == "APPROVE" and row.get("pnl_pct") is not None:
            approved.append(float(row["pnl_pct"]))

    vetoed = []
    for row in counterfactuals:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else _parse_payload(row.get("payload"))
        if payload.get("terminal") and payload.get("current_pnl_pct") is not None:
            vetoed.append(float(payload["current_pnl_pct"]))

    def summarize(label: str, values: list[float]):
        n = len(values)
        wins = sum(1 for x in values if x > 0)
        return {
            "cohort": label,
            "sample_size": n,
            "wins": wins,
            "win_rate": (wins / n) if n else None,
            "avg_pnl_pct": (sum(values) / n) if n else None,
            "evidence_status": "USABLE" if n >= 50 else "PRELIMINARY",
        }

    return [summarize("AI_APPROVED_REAL_TRADES", approved), summarize("AI_VETOED_COUNTERFACTUALS", vetoed)]


def main():
    settings = load_settings()
    repo = AlertRepository(settings.database_path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = Path("research_exports") / stamp
    out.mkdir(parents=True, exist_ok=True)
    observations = repo.research_rows(50000)
    outcomes = repo.outcome_rows(10000)
    counterfactuals = repo.counterfactual_rows(10000)
    summary = _effectiveness_summary(outcomes, counterfactuals)
    _write_csv(out / "signal_observations.csv", observations)
    _write_csv(out / "trade_outcomes.csv", outcomes)
    _write_csv(out / "ai_veto_counterfactuals.csv", counterfactuals)
    _write_csv(out / "ai_effectiveness_summary.csv", summary)
    print(
        f"Exported {len(observations)} signal observations, {len(outcomes)} real trade outcomes, "
        f"and {len(counterfactuals)} AI-veto counterfactuals to {out.resolve()}"
    )
    for row in summary:
        wr = "n/a" if row["win_rate"] is None else f"{row['win_rate']*100:.1f}%"
        avg = "n/a" if row["avg_pnl_pct"] is None else f"{row['avg_pnl_pct']*100:+.1f}%"
        print(f"  {row['cohort']}: n={row['sample_size']} win_rate={wr} avg={avg} [{row['evidence_status']}]")


if __name__ == "__main__":
    main()
