import time
from decimal import Decimal

from binance_us import BinanceUS
from config import Config
from journal import log_trade
from paper_trader import PaperTrader
from portfolio import Portfolio
from risk_manager import size_trade
from state_store import RuntimeState
from strategy import score_setup

SYMBOL = "BTCUSD"
INTERVAL = "5m"
BOT_OCO_PREFIX = "AIB50"


def confirmation():
    phrase = input(
        "\nLIVE TRADING IS ENABLED.\n"
        "Type exactly: ENABLE LIVE BINANCE TRADING\n> "
    ).strip()
    if phrase != "ENABLE LIVE BINANCE TRADING":
        raise SystemExit("Live trading confirmation failed. No order sent.")


def oco_id():
    # Short enough for Binance client-order ID constraints.
    return f"{BOT_OCO_PREFIX}{int(time.time())}"


def weighted_fill_price(order, fallback):
    qty = float(order.get("executedQty", 0) or 0)
    quote = float(order.get("cummulativeQuoteQty", 0) or 0)
    return (quote / qty) if qty > 0 and quote > 0 else float(fallback)


def recover_live_position(client, state):
    """
    Reconcile persisted state with Binance.US.

    Safety behavior:
    - If our saved OCO is still executing, resume monitoring it.
    - If it has completed, process the filled leg and clear local position state.
    - If local state is missing but a bot-tagged OCO exists, recover the OCO metadata
      and refuse to open a second position until it is reconciled.
    """
    pos = state.live_position

    if pos and pos.get("order_list_id") is not None:
        try:
            order_list = client.get_order_list(pos["order_list_id"])
            status = order_list.get("listOrderStatus")
            if status == "EXECUTING":
                print(
                    f"RECOVERY: protective OCO {pos['order_list_id']} is active "
                    f"for {pos.get('qty')} {SYMBOL}."
                )
                return pos

            if status in {"ALL_DONE", "REJECT"}:
                filled = None
                for leg in order_list.get("orders", []):
                    order = client.get_order(SYMBOL, leg["orderId"])
                    if order.get("status") == "FILLED":
                        filled = order
                        break

                if filled:
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

                state.live_position = None
                state.save()
                return None
        except Exception as exc:
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
        print(f"RECOVERY WARNING: open OCO scan failed: {exc}")
        # A failed recovery check must not lead to a fresh live order.
        state.live_position = {
            "recovery_uncertain": True,
            "order_list_id": None,
        }
        state.save()
        return state.live_position

    return None


def install_live_protection(client, entry_order, fallback_price, cfg):
    executed_qty = float(entry_order.get("executedQty", 0) or 0)
    if executed_qty <= 0:
        raise RuntimeError("Market buy returned no executed quantity.")

    entry = weighted_fill_price(entry_order, fallback_price)
    stop = entry * (1 - cfg.stop_loss_pct)
    target = entry * (1 + cfg.take_profit_pct)

    # Commission may have been charged in BTC, so protect no more than free base balance.
    rules = client.symbol_rules(SYMBOL)
    free_base = client.asset_balance(rules["base_asset"])["free"]
    protect_qty = min(Decimal(str(executed_qty)), free_base)
    protect_qty = client.normalize_quantity(SYMBOL, protect_qty)

    client_id = oco_id()
    oco = client.place_sell_oco(
        SYMBOL,
        protect_qty,
        take_profit_price=target,
        stop_price=stop,
        list_client_order_id=client_id,
    )

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
    """
    executed_qty = Decimal(str(entry_order.get("executedQty", "0") or "0"))
    if executed_qty <= 0:
        return

    rules = client.symbol_rules(SYMBOL)
    free_base = client.asset_balance(rules["base_asset"])["free"]
    sell_qty = min(executed_qty, free_base)
    if sell_qty <= 0:
        raise RuntimeError(
            f"Protection failed ({reason}) and no free base balance was available to flatten."
        )

    client.market_sell_quantity(SYMBOL, sell_qty)
    print(
        "EMERGENCY FLATTEN: entry protection could not be installed, "
        "so the bot attempted an immediate market exit."
    )


def poll_live_oco(client, state):
    pos = state.live_position
    if not pos:
        return
    if pos.get("recovery_uncertain"):
        print("LIVE ENTRY BLOCKED: unresolved/recovery state requires reconciliation.")
        return

    order_list = client.get_order_list(pos["order_list_id"])
    if order_list.get("listOrderStatus") == "EXECUTING":
        return

    if order_list.get("listOrderStatus") in {"ALL_DONE", "REJECT"}:
        filled = None
        for leg in order_list.get("orders", []):
            order = client.get_order(SYMBOL, leg["orderId"])
            if order.get("status") == "FILLED":
                filled = order
                break

        if filled:
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
            print(
                f"OCO {pos['order_list_id']} is no longer executing but no filled leg "
                "was detected. New entries remain blocked until inspected."
            )
            pos["recovery_uncertain"] = True
            state.save()
            return

        state.live_position = None
        state.save()


def run():
    cfg = Config()
    cfg.validate()
    client = BinanceUS(cfg.api_key, cfg.api_secret)

    mode = "LIVE" if cfg.enable_live_trading else "PAPER"
    print(f"Mode: {mode}")
    print(f"Exchange: Binance.US | Symbol: {SYMBOL} | Interval: {INTERVAL}")
    print(f"Daily loss cutoff: ${cfg.daily_loss_limit_usd:.2f}")
    print(f"Max position: ${cfg.max_position_usd:.2f}")

    state = RuntimeState.load()
    state.roll_day_if_needed()
    state.save()

    if cfg.enable_live_trading:
        confirmation()
        recover_live_position(client, state)

    paper_portfolio = Portfolio(Decimal(str(cfg.starting_capital)))
    paper_trader = PaperTrader(paper_portfolio)

    while True:
        state.roll_day_if_needed()
        state.save()

        if state.realized_today <= -cfg.daily_loss_limit_usd:
            print("KILL SWITCH: daily loss limit reached. No more new trades today.")
            if cfg.enable_live_trading and state.live_position:
                poll_live_oco(client, state)
            time.sleep(cfg.poll_seconds)
            continue

        df = client.klines(SYMBOL, INTERVAL, 200)
        current = float(df.iloc[-1]["close"])
        current_dec = Decimal(str(current))

        if cfg.enable_live_trading:
            if state.live_position:
                poll_live_oco(client, state)
                time.sleep(cfg.poll_seconds)
                continue
            position_open = False
        else:
            position_open = paper_portfolio.positions.get(SYMBOL) is not None

        if not position_open:
            signal = score_setup(df)
            print(
                f"Price={current:.2f} score={signal.get('score', 0)} "
                f"reasons={', '.join(signal.get('reasons', []))}"
            )

            if signal.get("score", 0) >= cfg.min_score:
                equity = (
                    client.usd_balance()
                    if cfg.enable_live_trading
                    else float(paper_portfolio.equity({SYMBOL: current_dec}))
                )
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
                        entry_order = client.market_buy_usd(SYMBOL, decision.position_usd)
                        try:
                            live_position = install_live_protection(
                                client, entry_order, current, cfg
                            )
                        except Exception as exc:
                            try:
                                emergency_flatten(client, entry_order, str(exc))
                            finally:
                                raise RuntimeError(
                                    f"Entry filled but OCO installation failed: {exc}"
                                ) from exc

                        state.live_position = live_position
                        state.save()
                        entry = live_position["entry"]
                        executed_qty = live_position["qty"]
                        stop = live_position["stop"]
                        target = live_position["target"]

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
            pos = paper_portfolio.positions.get(SYMBOL)
            exit_reason = None
            if pos and current_dec <= pos.stop_loss:
                exit_reason = "STOP"
            elif pos and current_dec >= pos.take_profit:
                exit_reason = "TARGET"

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

        time.sleep(cfg.poll_seconds)


if __name__ == "__main__":
    run()
