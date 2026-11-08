"""Синтетические стаканы: чтобы показать интерфейс без токенов и без доступа к биржам."""
from __future__ import annotations

import math
import random
import time

from ..config import PairConfig
from ..models import Level, OrderBook


class DemoProvider:
    name = "demo"

    def __init__(self, seed: int | None = None, mean_premium: float = 1.75, volatility: float = 0.32,
                 reversion: float = 0.005):
        self.rng = random.Random(seed)
        self.mean_premium = mean_premium
        self.volatility = volatility
        self.reversion = reversion
        self._btc: dict[str, float] = {}
        self._prem: dict[str, float] = {}

    def books(self, pair: PairConfig) -> tuple[OrderBook, OrderBook]:
        """Шаг случайного блуждания цены BTC и процесса Орнштейна–Уленбека для премии MOEX."""
        pid = pair.id
        btc = self._btc.get(pid, 110_000.0) * math.exp(self.rng.gauss(0, 0.0004))
        prem = self._prem.get(pid, self.mean_premium)
        prem += self.reversion * (self.mean_premium - prem) + self.rng.gauss(0, self.volatility * 0.25)
        self._btc[pid], self._prem[pid] = btc, prem

        now = time.time()
        bybit = self._book(btc, now, spread_bps=1.0, step_bps=0.8, tick=0.5, qty_scale=0.8, source="demo-bybit")

        native_mid = btc * (1 + prem / 100) / pair.moex_multiplier  # цена фьючерса MOEX в родных единицах
        tick = 0.01 if pair.moex_multiplier > 10 else 1.0
        moex = self._book(native_mid, now, spread_bps=3.0, step_bps=2.0, tick=tick,
                          qty_scale=0.5 * pair.moex_lots_per_btc, source="demo-moex")
        return moex, bybit

    def _book(self, mid: float, ts: float, spread_bps: float, step_bps: float, tick: float,
              qty_scale: float, source: str, depth: int = 25) -> OrderBook:
        half = mid * spread_bps / 2e4
        bids, asks = [], []
        for i in range(depth):
            off = half + mid * step_bps * i / 1e4
            bids.append(Level(round((mid - off) / tick) * tick, qty_scale * self.rng.lognormvariate(-0.2, 0.7)))
            asks.append(Level(round((mid + off) / tick) * tick, qty_scale * self.rng.lognormvariate(-0.2, 0.7)))
        if asks[0].price <= bids[0].price:  # округление до tick не должно пересекать стакан
            asks[0] = Level(bids[0].price + tick, asks[0].qty)
        return OrderBook(bids=bids, asks=asks, ts=ts, source=source)
