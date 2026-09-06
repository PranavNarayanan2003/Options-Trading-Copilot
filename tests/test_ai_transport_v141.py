import asyncio
import json

from app.ai.reviewer import AIReviewer
from app.execution.webull_readonly import WebullReadOnlyClient


class FakeResponse:
    status_code = 200
    text = ""
    def raise_for_status(self):
        return None
    def json(self):
        return {
            "model": "gpt-5.6-terra",
            "output_text": json.dumps({
                "verdict": "APPROVE",
                "reason_code": "CLEAN_ALIGNMENT",
                "reason": "coherent",
                "confidence": 0.7,
            }),
            "usage": {"input_tokens": 50, "output_tokens": 12},
        }


class FakeAIClient:
    def __init__(self):
        self.calls = []
    async def post(self, url, headers=None, json=None):
        self.calls.append((url, json))
        return FakeResponse()
    async def aclose(self):
        pass


class FakeWebullResponse:
    status_code = 200
    text = ""
    def json(self):
        return {"data": [{"symbol": "SPY260918C00600000", "bid": "1.20", "ask": "1.24"}]}


class FakeWebullHTTPClient:
    def __init__(self):
        self.calls = 0
    async def get(self, *args, **kwargs):
        self.calls += 1
        return FakeWebullResponse()
    async def aclose(self):
        pass


def test_ai_request_is_compact_100_tokens_and_none_reasoning():
    async def run():
        r = AIReviewer("key", "gpt-5.6-terra", "none", 3)
        fake = FakeAIClient(); r._client = fake
        review = await r.precheck({
            "symbol": "SPY", "direction": "PUT", "score": 90, "opposite_score": 10,
            "edge": 80, "setup": "BREAKOUT_IGNITION", "regime": "TRENDING",
            "trends": {"1m": "bearish", "5m": "bearish", "15m": "bearish"},
            "news": [], "upcoming_events": [],
        })
        assert review.verdict == "APPROVE"
        body = fake.calls[0][1]
        assert body["reasoning"]["effort"] == "none"
        assert body["max_output_tokens"] == 100
        assert "technical_snapshot" not in body["input"]
    asyncio.run(run())


def test_webull_http_client_is_reused_for_multiple_quotes():
    async def run():
        c = WebullReadOnlyClient("app", "secret", "acct", "api.webull.com")
        fake = FakeWebullHTTPClient(); c._client = fake
        q1 = await c.get_option_snapshot("SPY260918C00600000")
        q2 = await c.get_option_snapshot("SPY260918C00600000")
        assert q1.ask == 1.24 and q2.bid == 1.20
        assert fake.calls == 2
        assert c._client is fake
    asyncio.run(run())
