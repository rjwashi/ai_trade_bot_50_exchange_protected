import csv
from pathlib import Path
from datetime import datetime, timezone

PATH = Path("trades.csv")

def log_trade(event, symbol, mode, price, quantity, usd_value, pnl=0.0, note=""):
    exists = PATH.exists()
    with PATH.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow([
                "timestamp_utc","event","symbol","mode","price",
                "quantity","usd_value","pnl","note"
            ])
        w.writerow([
            datetime.now(timezone.utc).isoformat(),
            event, symbol, mode, price, quantity, usd_value, pnl, note
        ])
