"""Paper portfolio accounting for simulated spot positions."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class Position:
    market: str
    entry_price: Decimal
    quantity: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    entry_fee: Decimal = Decimal("0")

    @property
    def notional(self) -> Decimal:
        return self.entry_price * self.quantity


class Portfolio:
    def __init__(self, starting_cash: Decimal) -> None:
        if starting_cash <= 0:
            raise ValueError("starting_cash must be greater than zero")
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.positions: dict[str, Position] = {}
        self.realized_pnl = Decimal("0")

    def add_position(self, position: Position) -> None:
        if position.market in self.positions:
            raise ValueError(f"position already open for {position.market}")
        if position.notional > self.cash:
            raise ValueError("insufficient paper cash for position principal")
        self.cash -= position.notional
        self.positions[position.market] = position

    def close_position(self, market: str, exit_price: Decimal, exit_fee: Decimal) -> Decimal:
        if exit_price <= 0:
            raise ValueError("exit_price must be greater than zero")
        if exit_fee < 0:
            raise ValueError("exit_fee must not be negative")
        position = self.positions.pop(market, None)
        if position is None:
            raise ValueError(f"no open position for {market}")

        proceeds = exit_price * position.quantity
        self.cash += proceeds - exit_fee
        pnl = ((exit_price - position.entry_price) * position.quantity
               - position.entry_fee - exit_fee)
        self.realized_pnl += pnl
        return pnl

    def equity(self, prices: dict[str, Decimal] | None = None) -> Decimal:
        prices = prices or {}
        total = self.cash
        for market, position in self.positions.items():
            mark = prices.get(market, position.entry_price)
            total += mark * position.quantity
        return total
