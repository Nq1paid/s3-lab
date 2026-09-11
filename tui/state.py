"""Application state, persisted automatically.

A config file exists; you should never need to open it. Every value here is
reachable from a screen, and anything set in the UI is written back on change
so the app comes up where you left it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

CONFIG = Path("config.json")


@dataclass
class TestConfig:
    strategy: str = "strategies.reference.orb:generate_orders"
    strategy_name: str = "orb_breakout"
    instrument: str = "NQ"
    bar_minutes: int = 1
    session: str = "ETH"
    mode: str = "rolling"                 # rolling | anchored
    is_sessions: int = 500
    oos_sessions: int = 125
    step_sessions: int = 125
    min_trades: int = 30
    objective: str = "mar"
    grid: dict[str, list] = field(default_factory=lambda: {
        "or_minutes": [15, 30, 45, 60],
        "stop_ticks": [20, 40, 60, 80],
        "target_r": [1.0, 1.5, 2.0, 3.0],
    })
    mc_methods: list[str] = field(default_factory=lambda: [
        "trade_bootstrap", "perturbation",
    ])
    mc_iterations: int = 10_000

    @property
    def grid_size(self) -> int:
        n = 1
        for values in self.grid.values():
            n *= max(1, len(values))
        return n


@dataclass
class AppState:
    test: TestConfig = field(default_factory=TestConfig)
    slippage_ticks: float = 1.0
    starting_equity: float = 100_000.0
    classic_pnl_colours: bool = False      # green/red toggle, default OFF
    data_dir: str = "data/raw"
    cache_dir: str = "data/cache"

    # ---- persistence

    @classmethod
    def load(cls, path: Path = CONFIG) -> "AppState":
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A corrupt config must not stop the app from starting. Defaults
            # are always usable, and the user never opened this file anyway.
            return cls()
        state = cls()
        for f in fields(cls):
            if f.name not in raw:
                continue
            if f.name == "test":
                state.test = TestConfig(**{k: v for k, v in raw["test"].items()
                                           if k in {x.name for x in fields(TestConfig)}})
            else:
                setattr(state, f.name, raw[f.name])
        return state

    def save(self, path: Path = CONFIG) -> None:
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    # ---- derived

    def instrument(self):
        from engine.data.canonical import DEFAULT_INSTRUMENTS

        return DEFAULT_INSTRUMENTS[self.test.instrument]

    def parquet(self) -> str:
        t = self.test
        return f"{self.cache_dir}/{t.instrument}_{t.bar_minutes}m_{t.session.lower()}.parquet"

    def available_series(self) -> list[str]:
        return sorted(p.stem for p in Path(self.cache_dir).glob("*.parquet"))
