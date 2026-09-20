"""Durable runtime state for live-trading restart recovery."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

STATE_FILE = Path("runtime_state.json")


@dataclass
class RuntimeState:
    session_day: str
    realized_today: float = 0.0
    live_position: dict[str, Any] | None = None

    @classmethod
    def fresh(cls) -> "RuntimeState":
        return cls(session_day=date.today().isoformat())

    def roll_day_if_needed(self) -> None:
        today = date.today().isoformat()
        if self.session_day != today:
            self.session_day = today
            self.realized_today = 0.0

    def save(self, path: Path = STATE_FILE) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path = STATE_FILE) -> "RuntimeState":
        if not path.exists():
            return cls.fresh()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            state = cls(
                session_day=str(data.get("session_day") or date.today().isoformat()),
                realized_today=float(data.get("realized_today", 0.0)),
                live_position=data.get("live_position"),
            )
            state.roll_day_if_needed()
            return state
        except Exception:
            # Do not silently discard a corrupt state file. Preserve it for inspection.
            bad = path.with_name(path.name + ".corrupt")
            try:
                path.replace(bad)
            except OSError:
                pass
            return cls.fresh()
