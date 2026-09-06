from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo


class SessionClock:
    def __init__(self, config: dict):
        self.tz = ZoneInfo(config["timezone"])
        self.prime_start = time.fromisoformat(config["prime_start"])
        self.prime_end = time.fromisoformat(config["prime_end"])
        self.secondary_end = time.fromisoformat(config["secondary_end"])

    def localize(self, dt: datetime) -> datetime:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(self.tz)

    def classify(self, dt: datetime) -> str:
        local = self.localize(dt)
        if local.weekday() >= 5:
            return "closed"
        t = local.time().replace(tzinfo=None)
        if self.prime_start <= t < self.prime_end:
            return "prime"
        if self.prime_end <= t < self.secondary_end:
            return "secondary"
        return "closed"

    def is_regular_session(self, dt: datetime) -> bool:
        local = self.localize(dt)
        if local.weekday() >= 5:
            return False
        t = local.time().replace(tzinfo=None)
        return time(9, 30) <= t < time(16, 0)
