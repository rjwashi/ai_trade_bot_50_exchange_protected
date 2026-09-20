# $50 Binance.US Bot — Exchange-Protected Build

This version keeps the original paper-trading engine and adds live-trading protection designed for restart safety.

## What changed

- After a successful live MARKET buy, the bot immediately submits a Binance.US SELL OCO:
  - take-profit LIMIT leg
  - STOP_LOSS leg
- If OCO placement fails after the buy fills, the bot fails closed and attempts an immediate market sell.
- Live state is written to `runtime_state.json`.
- On restart, the bot queries the saved OCO and resumes monitoring it.
- If it discovers an active bot OCO without complete local entry data, it blocks new entries instead of risking a duplicate position.
- Daily realized P/L is persisted so restarting the script does not intentionally reset the daily kill switch.
- Base-asset commission is handled by protecting no more than the actual free base balance.
- Quantity and prices are normalized against the live Binance.US LOT_SIZE and PRICE_FILTER rules.

## Default safety settings

- Starting paper capital: $50
- Risk per trade: 1%
- Maximum position: $15
- Daily loss limit: $1.50
- Stop: 2%
- Target: 4%
- One BTC/USD position at a time
- No leverage
- No martingale
- Live trading remains OFF by default

## Install

```bat
python -m pip install -r requirements.txt
copy .env.example .env
```

Keep this OFF while testing:

```text
ENABLE_LIVE_TRADING=false
```

Run tests:

```bat
python -m unittest discover -s tests -v
```

Start:

```bat
python main.py
```

## Before live mode

Create a Binance.US API key with only the permissions required for account reading and spot trading.

Do not enable withdrawals.

Do not paste your API secret into ChatGPT or any message.

Put credentials only in your local `.env` file.

## Live mode

When you intentionally decide to use real funds:

```text
ENABLE_LIVE_TRADING=true
```

The program still requires this typed confirmation at startup:

```text
ENABLE LIVE BINANCE TRADING
```

## Protective-order behavior

A live entry is considered successfully armed only after Binance.US acknowledges its OCO order list.
The OCO is exchange-side, so its stop/target can remain active if this Python process exits.

If the entry fills but OCO creation fails, the bot attempts to immediately flatten the new position rather than leave it unprotected.

## Restart recovery

`runtime_state.json` records the active OCO order-list ID and current daily realized P/L.

On restart:
1. The bot loads local state.
2. It queries Binance.US for the saved OCO.
3. If the OCO is still executing, monitoring resumes.
4. If an exit leg filled, it records the exit and clears the local position.
5. If reconciliation is uncertain, new live entries are blocked.

Do not delete `runtime_state.json` while a live bot position or protective OCO exists.

## Important limitations

- Market orders can slip.
- A stop order is not a guaranteed execution price.
- API/network/exchange failures can occur.
- The P/L estimate does not fully reconstruct every possible commission asset or tax-lot treatment.
- This bot is automation software, not a guarantee of profit.
