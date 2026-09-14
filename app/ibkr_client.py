"""
This is the ONLY file you should need to touch to go from mock to live.

Every method below currently returns mock data so the agent is fully
runnable and demoable without TWS/Gateway open. Replace each method body
with a call into your existing ibapi-based script, keeping the same
signature and return type (the Pydantic models in app/models.py). As long
as the contract holds, app/tools.py and app/agent.py don't change at all.

If you already have a class wrapping EClient/EWrapper, the cleanest path
is usually to import it here and call its methods from inside these
wrappers, translating its raw responses into the Pydantic models below.
"""
from datetime import datetime, timezone

from app.config import settings
from app.models import AccountSummary, BracketOrderStatus, Fill, OpenOrder, Position


_MOCK_QUOTES = {
    "AAPL": 194.10,
    "NVDA": 131.20,
    "MSFT": 398.55,
    "TSLA": 248.50,
}


class IBKRClient:
    def __init__(self) -> None:
        self._mock_order_id_counter = 2000

    def get_positions(self) -> list[Position]:
        if settings.use_mock_ibkr:
            return [
                Position(symbol="AAPL", quantity=150, avg_cost=187.32, current_price=194.10),
                Position(symbol="NVDA", quantity=40, avg_cost=118.50, current_price=131.20),
                Position(symbol="MSFT", quantity=60, avg_cost=402.10, current_price=398.55),
            ]
        # TODO: replace with real IBKR call, e.g.:
        # raw = self._real_client.reqPositions()
        # return [Position(symbol=p.symbol, quantity=p.qty, ...) for p in raw]
        raise NotImplementedError("Wire up live IBKR connection here")

    def get_account_summary(self) -> AccountSummary:
        if settings.use_mock_ibkr:
            return AccountSummary(
                net_liquidation=182_450.00,
                total_cash=41_200.00,
                buying_power=82_400.00,
                unrealized_pnl=3_115.50,
                realized_pnl_today=-210.00,
            )
        raise NotImplementedError("Wire up live IBKR connection here")

    def get_open_orders(self) -> list[OpenOrder]:
        if settings.use_mock_ibkr:
            return [
                OpenOrder(
                    order_id=1001,
                    symbol="TSLA",
                    action="BUY",
                    order_type="LMT",
                    quantity=20,
                    limit_price=245.00,
                    status="Submitted",
                ),
            ]
        raise NotImplementedError("Wire up live IBKR connection here")

    def get_recent_fills(self) -> list[Fill]:
        if settings.use_mock_ibkr:
            return [
                Fill(
                    symbol="NVDA",
                    action="BUY",
                    quantity=40,
                    price=118.50,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                ),
            ]
        raise NotImplementedError("Wire up live IBKR connection here")

    def get_bracket_order_status(self, symbol: str) -> BracketOrderStatus:
        if settings.use_mock_ibkr:
            return BracketOrderStatus(
                symbol=symbol,
                parent_status="Filled",
                take_profit_status="Submitted",
                stop_loss_status="Submitted",
            )
        raise NotImplementedError("Wire up live IBKR connection here")

    def get_quote(self, symbol: str) -> float:
        """Current price for a symbol, used to size an order before it's
        placed. Not tied to use_mock_ibkr's positions list — any symbol can
        be quoted, not just ones already held."""
        if settings.use_mock_ibkr:
            return _MOCK_QUOTES.get(symbol.upper(), 100.00)
        raise NotImplementedError("Wire up live IBKR connection here")

    def place_order(
        self,
        symbol: str,
        quantity: float,
        action: str = "BUY",
        order_type: str = "MKT",
        limit_price: float | None = None,
    ) -> OpenOrder:
        if settings.use_mock_ibkr:
            self._mock_order_id_counter += 1
            return OpenOrder(
                order_id=self._mock_order_id_counter,
                symbol=symbol,
                action=action,
                order_type=order_type,
                quantity=quantity,
                limit_price=limit_price,
                status="Submitted",
            )
        raise NotImplementedError("Wire up live IBKR connection here")


ibkr_client = IBKRClient()
