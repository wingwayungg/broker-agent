"""
Pydantic models for everything the agent can query. These double as the
return-type contract for app/ibkr_client.py — when you wire in your real
IBKR script, match these shapes and nothing downstream needs to change.
They also double as FastAPI response models if you expose raw endpoints
later, so this isn't throwaway typing work.
"""
from pydantic import BaseModel


class Position(BaseModel):
    symbol: str
    quantity: float
    avg_cost: float
    current_price: float

    @property
    def market_value(self) -> float:
        return round(self.quantity * self.current_price, 2)

    @property
    def unrealized_pnl_pct(self) -> float:
        if self.avg_cost == 0:
            return 0.0
        return round((self.current_price - self.avg_cost) / self.avg_cost * 100, 2)


class AccountSummary(BaseModel):
    net_liquidation: float
    total_cash: float
    buying_power: float
    unrealized_pnl: float
    realized_pnl_today: float


class OpenOrder(BaseModel):
    order_id: int
    symbol: str
    action: str  # BUY / SELL
    order_type: str  # e.g. LMT, MKT, bracket parent/child
    quantity: float
    limit_price: float | None = None
    status: str


class Fill(BaseModel):
    symbol: str
    action: str
    quantity: float
    price: float
    timestamp: str


class BracketOrderStatus(BaseModel):
    symbol: str
    parent_status: str
    take_profit_status: str | None = None
    stop_loss_status: str | None = None
