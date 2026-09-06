from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from app.models import WebullOptionQuote, WebullPosition


class WebullReadOnlyError(RuntimeError):
    pass


def _occ_symbol(underlying: str, expiration: str | None, option_type: str | None, strike: float | None) -> str | None:
    if not underlying or not expiration or not option_type or strike is None:
        return None
    try:
        dt = datetime.fromisoformat(str(expiration)[:10])
        cp = "C" if str(option_type).upper().startswith("C") else "P"
        strike_code = int(round(float(strike) * 1000))
        return f"{underlying.upper()}{dt:%y%m%d}{cp}{strike_code:08d}"
    except Exception:
        return None


def _float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ms_datetime(value) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000.0
        return datetime.fromtimestamp(number, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


@dataclass
class WebullSyncState:
    connected: bool = False
    last_sync_at: datetime | None = None
    error: str = ""


class WebullReadOnlyClient:
    """Read-only Webull OpenAPI client for positions and option snapshots.

    Authentication follows Webull's documented HMAC-SHA1 signature algorithm.
    No order placement method exists in this class.
    """

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        account_id: str,
        endpoint: str,
        access_token: str = "",
        option_snapshot_path: str = "/market-data/options/snapshots/list",
    ):
        self.app_key = app_key
        self.app_secret = app_secret
        self.account_id = account_id
        self.endpoint = endpoint.replace("https://", "").replace("http://", "").rstrip("/")
        self.access_token = access_token
        self.option_snapshot_path = option_snapshot_path if option_snapshot_path.startswith("/") else f"/{option_snapshot_path}"
        self._client: httpx.AsyncClient | None = None

    @property
    def market_data_configured(self) -> bool:
        return bool(self.app_key and self.app_secret and self.endpoint)

    @property
    def positions_configured(self) -> bool:
        return bool(self.market_data_configured and self.account_id)

    @property
    def configured(self) -> bool:
        # Backward-compatible meaning used by the position monitor.
        return self.positions_configured

    def _build_signature(self, path: str, query: dict[str, str] | None, body: dict | None, timestamp: str, nonce: str) -> str:
        fields = {
            "x-app-key": self.app_key,
            "x-timestamp": timestamp,
            "x-signature-algorithm": "HMAC-SHA1",
            "x-signature-version": "1.0",
            "x-signature-nonce": nonce,
            "host": self.endpoint,
        }
        for k, v in (query or {}).items():
            if v is not None:
                fields[str(k)] = str(v)
        str1 = "&".join(f"{k}={fields[k]}" for k in sorted(fields))
        raw = f"{path}&{str1}"
        if body is not None:
            body_text = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
            body_hash = hashlib.md5(body_text.encode("utf-8")).hexdigest().upper()
            raw += f"&{body_hash}"
        encoded = quote(raw, safe="")
        return base64.b64encode(
            hmac.new(f"{self.app_secret}&".encode("utf-8"), encoded.encode("utf-8"), hashlib.sha1).digest()
        ).decode("ascii")

    def _signed_headers(self, method: str, path: str, query: dict[str, str] | None = None, body: dict | None = None) -> dict[str, str]:
        if not self.market_data_configured:
            raise WebullReadOnlyError("Webull OpenAPI app key/secret/endpoint are incomplete")
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        nonce = secrets.token_hex(16)
        signature = self._build_signature(path, query, body, timestamp, nonce)
        headers = {
            "Accept": "application/json",
            "x-app-key": self.app_key,
            "x-timestamp": timestamp,
            "x-signature": signature,
            "x-signature-algorithm": "HMAC-SHA1",
            "x-signature-version": "1.0",
            "x-signature-nonce": nonce,
            "x-version": "v2",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.access_token:
            headers["x-access-token"] = self.access_token
        return headers

    async def start(self):
        if self.market_data_configured and self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(8.0),
                limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            )

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            await self.start()
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(8.0))
        return self._client

    async def _get(self, path: str, query: dict[str, str] | None = None):
        headers = self._signed_headers("GET", path, query=query)
        client = await self._get_client()
        response = await client.get(f"https://{self.endpoint}{path}", params=query or {}, headers=headers)
        if response.status_code >= 400:
            detail = response.text[:320]
            raise WebullReadOnlyError(f"Webull GET {path} failed ({response.status_code}): {detail}")
        return response.json()

    async def get_option_snapshot(self, contract_symbol: str) -> WebullOptionQuote | None:
        if not self.market_data_configured:
            raise WebullReadOnlyError("Webull market-data credentials are incomplete")
        symbol = contract_symbol.upper().strip()
        payload = await self._get(self.option_snapshot_path, {"symbols": symbol, "category": "US_OPTION"})
        rows = payload.get("data", payload) if isinstance(payload, dict) else payload
        if isinstance(rows, dict):
            rows = rows.get("snapshots", rows.get("list", rows.get("data", [])))
        if isinstance(rows, dict):
            rows = [rows]
        row = next((r for r in (rows or []) if isinstance(r, dict) and str(r.get("symbol") or r.get("contract_symbol") or "").upper() == symbol), None)
        if row is None and isinstance(rows, list) and rows and isinstance(rows[0], dict):
            row = rows[0]
        if not row:
            return None
        bid = _float(row.get("bid") or row.get("bid_price") or row.get("bp"))
        ask = _float(row.get("ask") or row.get("ask_price") or row.get("ap"))
        if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
            return None
        return WebullOptionQuote(
            contract_symbol=symbol,
            bid=bid,
            ask=ask,
            last=_float(row.get("price") or row.get("last_price") or row.get("last")),
            volume=_float(row.get("volume")),
            open_interest=_float(row.get("open_interest")),
            delta=_float(row.get("delta")),
            gamma=_float(row.get("gamma")),
            theta=_float(row.get("theta")),
            vega=_float(row.get("vega")),
            iv=_float(row.get("imp_vol") or row.get("iv")),
            quote_time=_ms_datetime(row.get("quote_time") or row.get("timestamp")),
            bid_size=_float(row.get("bid_size")),
            ask_size=_float(row.get("ask_size")),
        )

    async def get_positions(self) -> list[WebullPosition]:
        if not self.positions_configured:
            raise WebullReadOnlyError("Webull positions require app credentials, account ID and endpoint")
        query = {"account_id": self.account_id}
        path = "/trading/assets/positions/list"
        payload = await self._get(path, query)
        rows = payload.get("data", payload) if isinstance(payload, dict) else payload
        if isinstance(rows, dict):
            rows = rows.get("positions", rows.get("list", []))
        out = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or row.get("ticker") or "").upper()
            instrument = str(row.get("instrument_type") or row.get("asset_type") or row.get("security_type") or "").upper()
            legs = row.get("legs") or []
            if instrument == "OPTION" and legs and isinstance(legs[0], dict):
                leg = legs[0]
                option_type = leg.get("option_type") or leg.get("call_put")
                expiration = leg.get("expire_date") or leg.get("expiration_date")
                strike = leg.get("exercise_price") or leg.get("strike_price")
            else:
                option_type = row.get("option_type")
                expiration = row.get("expire_date") or row.get("expiration_date")
                strike = row.get("exercise_price") or row.get("strike_price")
            strike_f = _float(strike)

            def f(*names):
                for name in names:
                    value = _float(row.get(name))
                    if value is not None:
                        return value
                return None

            out.append(WebullPosition(
                symbol=symbol,
                instrument_type=instrument,
                quantity=f("quantity", "qty") or 0.0,
                cost_price=f("cost_price", "avg_price"),
                last_price=f("last_price", "market_price"),
                unrealized_profit_loss=f("unrealized_profit_loss", "unrealized_pnl"),
                option_type=str(option_type).upper() if option_type else None,
                expiration=str(expiration)[:10] if expiration else None,
                strike=strike_f,
                contract_symbol=_occ_symbol(symbol, expiration, option_type, strike_f),
            ))
        return out


class WebullPositionMonitor:
    def __init__(self, client: WebullReadOnlyClient, enabled: bool, poll_seconds: int = 15):
        self.client = client
        self.enabled = enabled and client.positions_configured
        self.poll_seconds = poll_seconds
        self.state = WebullSyncState()
        self.positions: list[WebullPosition] = []
        self._task: asyncio.Task | None = None
        self._poll_id = 0

    async def start(self):
        if self.enabled and not self._task:
            self._task = asyncio.create_task(self._run(), name="webull-readonly")

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self):
        while True:
            try:
                self.positions = await self.client.get_positions()
                self.state.connected = True
                self.state.error = ""
                self.state.last_sync_at = datetime.now(timezone.utc)
                self._poll_id += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.state.connected = False
                self.state.error = str(exc)
            await asyncio.sleep(self.poll_seconds)

    def by_contract(self, contract_symbol: str) -> WebullPosition | None:
        target = contract_symbol.upper()
        return next((p for p in self.positions if (p.contract_symbol or "").upper() == target and p.quantity != 0), None)
