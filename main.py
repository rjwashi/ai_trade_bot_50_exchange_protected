import time                      # Standard library for time-based operations (timestamps, sleep)
from decimal import Decimal       # Decimal for precise financial calculations (avoids float rounding issues)

from binance_us import BinanceUS  # Exchange client wrapper for Binance.US REST API
from config import Config         # Configuration loader/validator (API keys, risk params, etc.)
from journal import log_trade     # Trade logging utility (likely writes to file/db for audit/backtest)
from paper_trader import PaperTrader  # Simulated trader for PAPER mode
from portfolio import Portfolio       # Tracks positions, cash, equity in PAPER mode
from risk_manager import size_trade   # Position sizing logic based on risk parameters
from state_store import RuntimeState  # Persistent runtime state (daily P/L, live position, etc.)
from strategy import score_setup      # Signal scoring function (EMA/RSI/MACD/volume-based)

SYMBOL = "BTCUSD"               # Trading symbol (BTC/USD pair on Binance.US)
INTERVAL = "5m"                 # Candle interval (5-minute bars)
BOT_OCO_PREFIX = "AIB50"        # Prefix for client-order IDs to tag bot-created OCOs


def confirmation():
    """
    Require explicit human confirmation before enabling LIVE trading.

    This is a critical safety step to prevent accidental live execution.
    """
    phrase = input(
        "\nLIVE TRADING IS ENABLED.\n"
        "Type exactly: ENABLE LIVE BINANCE TRADING\n> "
    ).strip()
    if phrase != "ENABLE LIVE BINANCE TRADING":
        # Hard exit if confirmation phrase is not exact.
        raise SystemExit("Live trading confirmation failed. No order sent.")


def oco_id():
    """
    Generate a unique client-order ID for OCO orders.

    Uses a prefix plus current epoch time; short enough for Binance constraints.
    """
    # Suggestion: consider adding a random suffix to avoid collisions if multiple bots run.
    return f"{BOT_OCO_PREFIX}{int(time.time())}"


def weighted_fill_price(order, fallback):
    """
    Compute a volume-weighted average fill price from an order object.

    If executed quantity or quote amount is missing/zero, fall back to a provided price.
    """
    qty = float(order.get("executedQty", 0) or 0)          # Base asset executed quantity
    quote = float(order.get("cummulativeQuoteQty", 0) or 0)  # Total quote spent (USD)
    return (quote / qty) if qty > 0 and quote > 0 else float(fallback)


def recover_live_position(client, state):
    """
    Reconcile persisted state with Binance.US.

    Safety behavior:
    - If our saved OCO is still executing, resume monitoring it.
    - If it has completed, process the filled leg and clear local position state.
    - If local state is missing but a bot-tagged OCO exists, recover the OCO metadata
      and refuse to open a second position until it is reconciled.

    This function prevents the bot from opening new positions while an old OCO
    is still active or in an uncertain state.
    """
    pos = state.live_position  # Previously saved live position metadata

    # If we have a saved OCO ID, attempt to query its status.
    if pos and pos.get("order_list_id") is not None:
        try:
            order_list = client.get_order_list(pos["order_list_id"])
            status = order_list.get("listOrderStatus")
            if status == "EXECUTING":
                # OCO is still active; resume monitoring without opening new entries.
                print(
                    f"RECOVERY: protective OCO {pos['order_list_id']} is active "
                    f"for {pos.get('qty')} {SYMBOL}."
                )
                return pos

            if status in {"ALL_DONE", "REJECT"}:
                # OCO has completed or been rejected; determine which leg filled.
                filled = None
                for leg in order_list.get("orders", []):
                    order = client.get_order(SYMBOL, leg["orderId"])
                    if order.get("status") == "FILLED":
                        filled = order
                        break

                if filled:
                    # Compute exit quantity and price, then P/L.
                    qty = float(filled.get("executedQty", pos.get("qty", 0)) or 0)
                    exit_price = weighted_fill_price(filled, pos.get("stop", 0))
                    pnl = (exit_price - float(pos["entry"])) * qty
                    state.realized_today += pnl
                    log_trade(
                        "SELL", SYMBOL, "LIVE", exit_price, qty,
                        exit_price * qty, pnl, "RECOVERED_OCO_EXIT"
                    )
                    print(
                        f"RECOVERY: prior OCO completed at {exit_price:.2f}; "
                        f"estimated P/L=${pnl:.2f}."
                    )

                # Clear live position state after reconciliation.
                state.live_position = None
                state.save()
                return None
        except Exception as exc:
            # If we cannot query the OCO, mark recovery as uncertain and block new entries.
            print(f"RECOVERY WARNING: could not query saved OCO: {exc}")
            print("No new live entry will be opened until this state is reconciled.")
            pos["recovery_uncertain"] = True
            state.save()
            return pos

    # Extra guard: detect a bot-tagged OCO even if local state disappeared.
    try:
        for item in client.open_order_lists():
            cid = str(item.get("listClientOrderId", ""))
            if item.get("symbol") == SYMBOL and cid.startswith(BOT_OCO_PREFIX):
                # Found an active OCO with our prefix but no local state; block new entries.
                recovered = {
                    "entry": None,
                    "qty": None,
                    "stop": None,
                    "target": None,
                    "order_list_id": item["orderListId"],
                    "list_client_order_id": cid,
                    "recovery_uncertain": True,
                }
                state.live_position = recovered
                state.save()
                print(
                    f"RECOVERY GUARD: found active bot OCO {item['orderListId']} "
                    "without matching local entry data. New entries are blocked."
                )
                return recovered
    except Exception as exc:
        # If scanning open OCOs fails, mark state as uncertain and block new entries.
        print(f"RECOVERY WARNING: open OCO scan failed: {exc}")
        # A failed recovery check must not lead to a fresh live order.
        state.live_position = {
            "recovery_uncertain": True,
            "order_list_id": None,
        }
        state.save()
        return state.live_position

    # No active OCO or recovery issues detected.
    return None


def install_live_protection(client, entry_order, fallback_price, cfg):
    """
    After a live market buy, install an exchange-side OCO (stop + take profit)
    to protect the position.

    This function:
    - Computes entry, stop, and target prices.
    - Normalizes quantity to available free base balance.
    - Places a sell OCO on Binance.US.
    - Returns a structured live_position dict for state tracking.
    """
    executed_qty = float(entry_order.get("executedQty", 0) or 0)
    if executed_qty <= 0:
        # If no quantity was executed, something went wrong with the market buy.
        raise RuntimeError("Market buy returned no executed quantity.")

    entry = weighted_fill_price(entry_order, fallback_price)
    stop = entry * (1 - cfg.stop_loss_pct)      # Stop loss below entry
    target = entry * (1 + cfg.take_profit_pct)  # Take profit above entry

    # Commission may have been charged in BTC, so protect no more than free base balance.
    rules = client.symbol_rules(SYMBOL)
    free_base = client.asset_balance(rules["base_asset"])["free"]
    # Use min(executed_qty, free_base) to avoid over-selling.
    protect_qty = min(Decimal(str(executed_qty)), free_base)
    protect_qty = client.normalize_quantity(SYMBOL, protect_qty)  # Conform to lot size rules

    client_id = oco_id()
    oco = client.place_sell_oco(
        SYMBOL,
        protect_qty,
        take_profit_price=target,
        stop_price=stop,
        list_client_order_id=client_id,
    )

    # Suggestion: consider logging OCO placement details for debugging.
    return {
        "entry": entry,
        "qty": float(protect_qty),
        "usd": float(entry_order.get("cummulativeQuoteQty", 0) or 0),
        "stop": float(client.normalize_price(SYMBOL, stop)),
        "target": float(client.normalize_price(SYMBOL, target)),
        "order_list_id": int(oco["orderListId"]),
        "list_client_order_id": client_id,
        "recovery_uncertain": False,
    }


def emergency_flatten(client, entry_order, reason):
    """
    Fail closed: if the entry filled but exchange-side OCO cannot be installed,
    immediately attempt to flatten the purchased base asset.

    This is a last-resort safety mechanism to avoid being left unprotected.
    """
    executed_qty = Decimal(str(entry_order.get("executedQty", "0") or "0"))
    if executed_qty <= 0:
        # If no quantity was executed, nothing to flatten.
        return

    rules = client.symbol_rules(SYMBOL)
    free_base = client.asset_balance(rules["base_asset"])["free"]
    sell_qty = min(executed_qty, free_base)
    if sell_qty <= 0:
        # If we cannot sell anything, raise a hard error.
        raise RuntimeError(
            f"Protection failed ({reason}) and no free base balance was available to flatten."
        )

    client.market_sell_quantity(SYMBOL, sell_qty)
    print(
        "EMERGENCY FLATTEN: entry protection could not be installed, "
        "so the bot attempted an immediate market exit."
    )


def poll_live_oco(client, state):
    """
    Poll the status of an active live OCO and process exits.

    Behavior:
    - If OCO is still EXECUTING, do nothing.
    - If OCO is ALL_DONE or REJECT, determine if a leg filled and compute P/L.
    - If no filled leg is found, mark recovery as uncertain and block new entries.
    """
    pos = state.live_position
    if not pos:
        # No live position to monitor.
        return
    if pos.get("recovery_uncertain"):
        # If recovery is uncertain, do not allow new entries.
        print("LIVE ENTRY BLOCKED: unresolved/recovery state requires reconciliation.")
        return

    order_list = client.get_order_list(pos["order_list_id"])
    if order_list.get("listOrderStatus") == "EXECUTING":
        # OCO still active; nothing to do.
        return

    if order_list.get("listOrderStatus") in {"ALL_DONE", "REJECT"}:
        filled = None
        for leg in order_list.get("orders", []):
            order = client.get_order(SYMBOL, leg["orderId"])
            if order.get("status") == "FILLED":
                filled = order
                break

        if filled:
            # Compute exit quantity and price, then P/L.
            qty = float(filled.get("executedQty", pos["qty"]) or pos["qty"])
            exit_price = weighted_fill_price(filled, pos["stop"])
            pnl = (exit_price - pos["entry"]) * qty
            state.realized_today += pnl
            log_trade(
                "SELL", SYMBOL, "LIVE", exit_price, qty,
                exit_price * qty, pnl, "EXCHANGE_OCO_EXIT"
            )
            print(
                f"EXCHANGE EXIT: price={exit_price:.2f} P/L=${pnl:.2f}; "
                f"today=${state.realized_today:.2f}"
            )
        else:
            # OCO finished but no filled leg detected; mark as uncertain.
            print(
                f"OCO {pos['order_list_id']} is no longer executing but no filled leg "
                "was detected. New entries remain blocked until inspected."
            )
            pos["recovery_uncertain"] = True
            state.save()
            return

        # Clear live position after processing exit.
        state.live_position = None
        state.save()


def run():
    """
    Main event loop for the trading bot.

    Responsibilities:
    - Load and validate configuration.
    - Initialize Binance.US client.
    - Load runtime state and roll daily P/L if needed.
    - Handle LIVE vs PAPER mode behavior.
    - Continuously poll market data, evaluate signals, size trades, and execute.
    """
    cfg = Config()          # Load configuration (from env, file, etc.)
    cfg.validate()          # Validate configuration values (sanity checks)
    client = BinanceUS(cfg.api_key, cfg.api_secret)  # Initialize exchange client

    mode = "LIVE" if cfg.enable_live_trading else "PAPER"
    print(f"Mode: {mode}")
    print(f"Exchange: Binance.US | Symbol: {SYMBOL} | Interval: {INTERVAL}")
    print(f"Daily loss cutoff: ${cfg.daily_loss_limit_usd:.2f}")
    print(f"Max position: ${cfg.max_position_usd:.2f}")

    state = RuntimeState.load()   # Load persisted runtime state (P/L, live position, etc.)
    state.roll_day_if_needed()    # Reset daily stats if a new day has started
    state.save()                  # Persist any changes

    if cfg.enable_live_trading:
        # Require explicit confirmation before live trading.
        confirmation()
        # Attempt to recover any existing live position/OCO.
        recover_live_position(client, state)

    # Initialize PAPER trading portfolio and trader.
    paper_portfolio = Portfolio(Decimal(str(cfg.starting_capital)))
    paper_trader = PaperTrader(paper_portfolio)

    # Main infinite loop: poll, decide, trade, manage positions.
    while True:
        state.roll_day_if_needed()
        state.save()

        # Daily loss kill switch: stop opening new trades if loss exceeds limit.
        if state.realized_today <= -cfg.daily_loss_limit_usd:
            print("KILL SWITCH: daily loss limit reached. No more new trades today.")
            if cfg.enable_live_trading and state.live_position:
                # Still monitor live OCO even if no new entries allowed.
                poll_live_oco(client, state)
            time.sleep(cfg.poll_seconds)
            continue

        # Fetch latest candles (200 bars) from Binance.US.
        df = client.klines(SYMBOL, INTERVAL, 200)
        current = float(df.iloc[-1]["close"])      # Latest close price as float
        current_dec = Decimal(str(current))        # Same price as Decimal for PAPER math

        if cfg.enable_live_trading:
            if state.live_position:
                # If a live position exists, just poll OCO and skip new entries.
                poll_live_oco(client, state)
                time.sleep(cfg.poll_seconds)
                continue
            position_open = False   # Live mode: position state tracked via state.live_position
        else:
            # PAPER mode: check if a position exists in the portfolio.
            position_open = paper_portfolio.positions.get(SYMBOL) is not None

        if not position_open:
            # No open position: evaluate entry signal.
            signal = score_setup(df)
            print(
                f"Price={current:.2f} score={signal.get('score', 0)} "
                f"reasons={', '.join(signal.get('reasons', []))}"
            )

            # Only proceed if signal score meets minimum threshold.
            if signal.get("score", 0) >= cfg.min_score:
                # Determine available equity depending on mode.
                equity = (
                    client.usd_balance()
                    if cfg.enable_live_trading
                    else float(paper_portfolio.equity({SYMBOL: current_dec}))
                )
                # Use risk manager to size the trade.
                decision = size_trade(
                    equity=equity,
                    price=current,
                    risk_per_trade=cfg.risk_per_trade,
                    stop_loss_pct=cfg.stop_loss_pct,
                    take_profit_pct=cfg.take_profit_pct,
                    max_position_usd=cfg.max_position_usd,
                )

                if decision.allowed:
                    if cfg.enable_live_trading:
                        # LIVE: place a market buy in USD.
                        entry_order = client.market_buy_usd(SYMBOL, decision.position_usd)
                        try:
                            # Install exchange-side OCO protection.
                            live_position = install_live_protection(
                                client, entry_order, current, cfg
                            )
                        except Exception as exc:
                            # If OCO installation fails, attempt emergency flatten.
                            try:
                                emergency_flatten(client, entry_order, str(exc))
                            finally:
                                # Raise a hard error to force operator attention.
                                raise RuntimeError(
                                    f"Entry filled but OCO installation failed: {exc}"
                                ) from exc

                        # Save live position metadata to state.
                        state.live_position = live_position
                        state.save()
                        entry = live_position["entry"]
                        executed_qty = live_position["qty"]
                        stop = live_position["stop"]
                        target = live_position["target"]

                        # Log the BUY trade with full context.
                        log_trade(
                            "BUY", SYMBOL, mode, entry, executed_qty,
                            live_position["usd"], 0,
                            (
                                f"score={signal['score']}; stop={stop:.2f}; "
                                f"target={target:.2f}; "
                                f"oco={live_position['order_list_id']}"
                            ),
                        )
                        print(
                            f"ENTRY LIVE: ${live_position['usd']:.2f} at {entry:.2f}; "
                            f"exchange OCO={live_position['order_list_id']}; "
                            f"stop={stop:.2f}; target={target:.2f}"
                        )
                    else:
                        # PAPER: simulate entry using Portfolio and PaperTrader.
                        entry = current_dec
                        quantity = Decimal(str(decision.position_usd)) / entry
                        stop = entry * (
                            Decimal("1") - Decimal(str(cfg.stop_loss_pct))
                        )
                        target = entry * (
                            Decimal("1") + Decimal(str(cfg.take_profit_pct))
                        )
                        paper_trader.buy(
                            SYMBOL, entry, quantity, stop, target
                        )
                        log_trade(
                            "BUY", SYMBOL, mode, float(entry), float(quantity),
                            decision.position_usd, 0,
                            f"score={signal['score']}; stop={float(stop):.2f}; "
                            f"target={float(target):.2f}",
                        )
                        print(
                            f"ENTRY PAPER: ${decision.position_usd:.2f} "
                            f"at {float(entry):.2f}; stop={float(stop):.2f}; "
                            f"target={float(target):.2f}"
                        )

        else:
            # Position is open (PAPER mode only; LIVE handled via state.live_position).
            pos = paper_portfolio.positions.get(SYMBOL)
            exit_reason = None
            if pos and current_dec <= pos.stop_loss:
                exit_reason = "STOP"    # Stop loss hit
            elif pos and current_dec >= pos.take_profit:
                exit_reason = "TARGET"  # Take profit hit

            if exit_reason:
                qty = pos.quantity
                pnl = paper_trader.process_price(SYMBOL, current_dec)
                if pnl is not None:
                    state.realized_today += float(pnl)
                    state.save()
                    log_trade(
                        "SELL", SYMBOL, mode, float(current_dec), float(qty),
                        float(current_dec * qty), float(pnl), exit_reason,
                    )
                    print(
                        f"EXIT {exit_reason}: price={float(current_dec):.2f} "
                        f"P/L=${float(pnl):.2f}; today=${state.realized_today:.2f}; "
                        f"paper cash=${float(paper_portfolio.cash):.2f}"
                    )

        # Sleep between polling cycles to avoid hammering the API.
        time.sleep(cfg.poll_seconds)


if __name__ == "__main__":
    # Entry point: start the bot.
    run()
