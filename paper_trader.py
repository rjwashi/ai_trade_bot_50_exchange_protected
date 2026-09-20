"""Paper-trading execution engine with stop and target handling."""

from dataclasses import dataclass
from decimal import Decimal

import portfolio as portfolio_


@dataclass(frozen=True)
class OrderResult:
    market: str
    quantity: Decimal
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal


class PaperTrader:
    def __init__(self, portfolio: portfolio_.Portfolio,
                 fee_rate: Decimal = Decimal("0.0002")) -> None:
        if fee_rate < 0:
            raise ValueError("fee_rate must not be negative")
        self.portfolio = portfolio
        self.fee_rate = fee_rate

    def buy(self, market: str, entry_price: Decimal, quantity: Decimal,
            stop_loss: Decimal, take_profit: Decimal) -> OrderResult:
        if quantity <= 0 or entry_price <= 0:
            raise ValueError("entry_price and quantity must be greater than zero")
        if stop_loss <= 0 or stop_loss >= entry_price:
            raise ValueError("stop_loss must be greater than zero and below entry_price")
        if take_profit <= entry_price:
            raise ValueError("take_profit must be above entry_price")

        fee = entry_price * quantity * self.fee_rate
        total_required = entry_price * quantity + fee
        if total_required > self.portfolio.cash:
            raise ValueError("insufficient paper cash for position and entry fee")

        position = portfolio_.Position(
            market=market,
            entry_price=entry_price,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_fee=fee,
        )
        self.portfolio.add_position(position)  # reserves principal
        self.portfolio.cash -= fee             # charges entry fee
        return OrderResult(market, quantity, entry_price, stop_loss, take_profit)

    def process_price(self, market: str, price: Decimal) -> Decimal | None:
        if price <= 0:
            raise ValueError("price must be greater than zero")
        position = self.portfolio.positions.get(market)
        if position is None:
            return None
        if price <= position.stop_loss or price >= position.take_profit:
            fee = price * position.quantity * self.fee_rate
            return self.portfolio.close_position(market, price, fee)
        return None
