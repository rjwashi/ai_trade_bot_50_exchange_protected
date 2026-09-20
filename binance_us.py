import hashlib
import hmac
import time
import urllib.parse
from decimal import Decimal, ROUND_DOWN

import pandas as pd
import requests

BASE_URL = "https://api.binance.us"


class BinanceUS:
    def __init__(self, api_key="", api_secret=""):
        self.api_key = api_key
        self.api_secret = api_secret
        self.session = requests.Session()
        if api_key:
            self.session.headers.update({"X-MBX-APIKEY": api_key})

    def _signed(self, method, path, params=None):
        if not self.api_key or not self.api_secret:
            raise RuntimeError("Signed endpoint requires API credentials.")
        params = dict(params or {})
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 5000
        query = urllib.parse.urlencode(params)
        sig = hmac.new(
            self.api_secret.encode(),
            query.encode(),
            hashlib.sha256,
        ).hexdigest()
        url = f"{BASE_URL}{path}?{query}&signature={sig}"
        r = self.session.request(method, url, timeout=20)
        r.raise_for_status()
        return r.json()

    def klines(self, symbol="BTCUSD", interval="5m", limit=200):
        r = self.session.get(
            f"{BASE_URL}/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=20,
        )
        r.raise_for_status()
        return pd.DataFrame(
            r.json(),
            columns=[
                "open_time", "open", "high", "low", "close", "volume",
                "close_time", "quote_volume", "trades", "taker_base",
                "taker_quote", "ignore",
            ],
        )

    def price(self, symbol="BTCUSD"):
        r = self.session.get(
            f"{BASE_URL}/api/v3/ticker/price",
            params={"symbol": symbol},
            timeout=20,
        )
        r.raise_for_status()
        return float(r.json()["price"])

    def exchange_info(self, symbol="BTCUSD"):
        r = self.session.get(
            f"{BASE_URL}/api/v3/exchangeInfo",
            params={"symbol": symbol, "showPermissionSets": "true"},
            timeout=20,
        )
        r.raise_for_status()
        return r.json()["symbols"][0]

    def account(self):
        return self._signed("GET", "/api/v3/account")

    def asset_balance(self, asset):
        for b in self.account().get("balances", []):
            if b["asset"] == asset:
                return {
                    "free": Decimal(b["free"]),
                    "locked": Decimal(b["locked"]),
                }
        return {"free": Decimal("0"), "locked": Decimal("0")}

    def usd_balance(self):
        return float(self.asset_balance("USD")["free"])

    def symbol_rules(self, symbol):
        info = self.exchange_info(symbol)
        filters = {f["filterType"]: f for f in info["filters"]}
        lot = filters["LOT_SIZE"]
        price_filter = filters["PRICE_FILTER"]
        return {
            "base_asset": info["baseAsset"],
            "quote_asset": info["quoteAsset"],
            "step_size": Decimal(lot["stepSize"]),
            "min_qty": Decimal(lot["minQty"]),
            "tick_size": Decimal(price_filter["tickSize"]),
            "min_price": Decimal(price_filter["minPrice"]),
        }

    @staticmethod
    def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
        return (value / step).to_integral_value(rounding=ROUND_DOWN) * step

    def normalize_quantity(self, symbol, quantity):
        rules = self.symbol_rules(symbol)
        qty = self.floor_to_step(Decimal(str(quantity)), rules["step_size"])
        if qty < rules["min_qty"]:
            raise ValueError(f"Quantity {qty} is below minimum {rules['min_qty']}.")
        return qty

    def normalize_price(self, symbol, price):
        rules = self.symbol_rules(symbol)
        value = self.floor_to_step(Decimal(str(price)), rules["tick_size"])
        if value < rules["min_price"]:
            raise ValueError(f"Price {value} is below minimum {rules['min_price']}.")
        return value

    def market_buy_usd(self, symbol, usd_amount):
        return self._signed("POST", "/api/v3/order", {
            "symbol": symbol,
            "side": "BUY",
            "type": "MARKET",
            "quoteOrderQty": f"{usd_amount:.2f}",
            "newOrderRespType": "FULL",
        })

    def market_sell_quantity(self, symbol, quantity):
        qty = self.normalize_quantity(symbol, quantity)
        return self._signed("POST", "/api/v3/order", {
            "symbol": symbol,
            "side": "SELL",
            "type": "MARKET",
            "quantity": format(qty, "f"),
            "newOrderRespType": "FULL",
        })

    def place_sell_oco(self, symbol, quantity, take_profit_price, stop_price, list_client_order_id):
        """
        Place exchange-side protection:
          - LIMIT sell at take_profit_price
          - STOP_LOSS sell triggered at stop_price

        Binance.US requires SELL OCO: limit price > last price > stop price.
        """
        qty = self.normalize_quantity(symbol, quantity)
        target = self.normalize_price(symbol, take_profit_price)
        stop = self.normalize_price(symbol, stop_price)

        return self._signed("POST", "/api/v3/order/oco", {
            "symbol": symbol,
            "side": "SELL",
            "quantity": format(qty, "f"),
            "price": format(target, "f"),
            "stopPrice": format(stop, "f"),
            "listClientOrderId": list_client_order_id,
            "newOrderRespType": "FULL",
        })

    def open_orders(self, symbol):
        return self._signed("GET", "/api/v3/openOrders", {"symbol": symbol})

    def get_order(self, symbol, order_id):
        return self._signed("GET", "/api/v3/order", {
            "symbol": symbol,
            "orderId": int(order_id),
        })

    def get_order_list(self, order_list_id):
        return self._signed("GET", "/api/v3/orderList", {
            "orderListId": int(order_list_id),
        })

    def open_order_lists(self):
        return self._signed("GET", "/api/v3/openOrderList")

    def cancel_order_list(self, symbol, order_list_id):
        return self._signed("DELETE", "/api/v3/orderList", {
            "symbol": symbol,
            "orderListId": int(order_list_id),
        })
