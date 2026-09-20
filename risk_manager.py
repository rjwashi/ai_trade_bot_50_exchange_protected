from dataclasses import dataclass

@dataclass
class RiskDecision:
    allowed: bool
    position_usd: float
    stop_price: float
    take_profit_price: float
    reason: str

def size_trade(
    equity: float,
    price: float,
    risk_per_trade: float,
    stop_loss_pct: float,
    take_profit_pct: float,
    max_position_usd: float,
) -> RiskDecision:
    if equity <= 0 or price <= 0:
        return RiskDecision(False, 0, 0, 0, "invalid equity/price")

    risk_dollars = equity * risk_per_trade
    raw_position = risk_dollars / stop_loss_pct
    position_usd = min(raw_position, max_position_usd, equity)

    if position_usd < 1:
        return RiskDecision(False, 0, 0, 0, "position too small")

    stop_price = price * (1 - stop_loss_pct)
    take_profit_price = price * (1 + take_profit_pct)

    return RiskDecision(
        True,
        round(position_usd, 2),
        stop_price,
        take_profit_price,
        f"risk ${risk_dollars:.2f}; position ${position_usd:.2f}"
    )
