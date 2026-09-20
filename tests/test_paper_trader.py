import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portfolio import Portfolio
from paper_trader import PaperTrader


class PaperTraderTests(unittest.TestCase):
    def test_buy_reserves_principal_and_fee(self):
        p = Portfolio(Decimal("50"))
        t = PaperTrader(p, Decimal("0.0002"))
        t.buy("BTCUSD", Decimal("100"), Decimal("0.10"), Decimal("98"), Decimal("104"))
        self.assertEqual(p.cash, Decimal("39.998000"))
        self.assertIn("BTCUSD", p.positions)

    def test_take_profit_closes_and_returns_net_pnl(self):
        p = Portfolio(Decimal("50"))
        t = PaperTrader(p, Decimal("0.0002"))
        t.buy("BTCUSD", Decimal("100"), Decimal("0.10"), Decimal("98"), Decimal("104"))
        pnl = t.process_price("BTCUSD", Decimal("104"))
        self.assertEqual(pnl, Decimal("0.395920"))
        self.assertEqual(p.cash, Decimal("50.395920"))
        self.assertNotIn("BTCUSD", p.positions)

    def test_price_between_stop_and_target_does_nothing(self):
        p = Portfolio(Decimal("50"))
        t = PaperTrader(p)
        t.buy("BTCUSD", Decimal("100"), Decimal("0.10"), Decimal("98"), Decimal("104"))
        self.assertIsNone(t.process_price("BTCUSD", Decimal("101")))
        self.assertIn("BTCUSD", p.positions)

    def test_insufficient_cash_rejected(self):
        p = Portfolio(Decimal("5"))
        t = PaperTrader(p)
        with self.assertRaises(ValueError):
            t.buy("BTCUSD", Decimal("100"), Decimal("0.10"), Decimal("98"), Decimal("104"))


if __name__ == "__main__":
    unittest.main()
