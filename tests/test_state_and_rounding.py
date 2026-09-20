import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from binance_us import BinanceUS
from state_store import RuntimeState


class StateAndRoundingTests(unittest.TestCase):
    def test_floor_to_step(self):
        self.assertEqual(
            BinanceUS.floor_to_step(Decimal("0.00123456"), Decimal("0.00001")),
            Decimal("0.00123"),
        )

    def test_runtime_state_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            state = RuntimeState(
                session_day="2026-09-11",
                realized_today=-0.37,
                live_position={
                    "entry": 60000.0,
                    "qty": 0.0002,
                    "order_list_id": 123,
                },
            )
            state.save(path)
            loaded = RuntimeState.load(path)
            self.assertEqual(loaded.live_position["order_list_id"], 123)
            # If the test runs on another date, load() rolls the daily P/L;
            # the position must still survive.
            self.assertIsNotNone(loaded.live_position)


if __name__ == "__main__":
    unittest.main()
