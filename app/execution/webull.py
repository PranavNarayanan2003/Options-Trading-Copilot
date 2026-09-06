from __future__ import annotations

from dataclasses import dataclass

from app.models import TradeAlert


class ExecutionDisabled(RuntimeError):
    pass


@dataclass
class WebullOrderPreview:
    symbol: str
    option_contract: str
    side: str
    quantity: int
    order_type: str
    limit_price: float


class WebullExecutionAdapter:
    """Level-C seam only. V1 intentionally cannot submit a live order."""
    def __init__(self, enabled: bool = False):
        self.enabled = enabled

    def build_preview(self, alert: TradeAlert) -> WebullOrderPreview:
        if not alert.contract:
            raise ValueError("Alert has no option contract")
        return WebullOrderPreview(
            symbol=alert.symbol,
            option_contract=alert.contract.contract_symbol,
            side="BUY",
            quantity=1,
            order_type="LIMIT",
            limit_price=alert.contract.ask,
        )

    async def place(self, alert: TradeAlert):
        # Deliberately disabled even if ENABLE_BROKER_EXECUTION=true. This prevents accidental live execution
        # during strategy development; V2/C can replace this with the official Webull SDK after API approval.
        raise ExecutionDisabled("Live broker execution is intentionally disabled in V1")
