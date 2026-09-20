import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class Config:
    exchange: str = os.getenv("EXCHANGE", "binance_us").strip().lower()
    starting_capital: float = float(os.getenv("STARTING_CAPITAL", "50"))
    risk_per_trade: float = float(os.getenv("RISK_PER_TRADE", "0.01"))
    max_position_usd: float = float(os.getenv("MAX_POSITION_USD", "15"))
    daily_loss_limit_usd: float = float(os.getenv("DAILY_LOSS_LIMIT_USD", "1.50"))
    min_score: int = int(os.getenv("MIN_SCORE", "80"))
    stop_loss_pct: float = float(os.getenv("STOP_LOSS_PCT", "0.02"))
    take_profit_pct: float = float(os.getenv("TAKE_PROFIT_PCT", "0.04"))
    poll_seconds: int = int(os.getenv("POLL_SECONDS", "60"))
    enable_live_trading: bool = os.getenv("ENABLE_LIVE_TRADING", "false").lower() == "true"
    api_key: str = os.getenv("BINANCE_US_API_KEY", "")
    api_secret: str = os.getenv("BINANCE_US_API_SECRET", "")

    def validate(self):
        if self.exchange != "binance_us":
            raise ValueError("This starter build is configured for Binance.US first.")
        if not (0 < self.risk_per_trade <= 0.02):
            raise ValueError("RISK_PER_TRADE must be > 0 and <= 0.02")
        if self.max_position_usd <= 0:
            raise ValueError("MAX_POSITION_USD must be positive")
        if self.daily_loss_limit_usd <= 0:
            raise ValueError("DAILY_LOSS_LIMIT_USD must be positive")
        if self.stop_loss_pct <= 0 or self.take_profit_pct <= 0:
            raise ValueError("Stop-loss and take-profit percentages must be positive")
        if self.enable_live_trading and (not self.api_key or not self.api_secret):
            raise ValueError("Live trading requires BINANCE_US_API_KEY and BINANCE_US_API_SECRET")
