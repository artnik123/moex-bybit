from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Level:
    price: float
    qty: float


@dataclass
class OrderBook:
    bids: list[Level]  # от лучшей (максимальной) цены вниз
    asks: list[Level]  # от лучшей (минимальной) цены вверх
    ts: float = field(default_factory=time.time)
    source: str = ""

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    def scaled(self, price_mult: float = 1.0, qty_div: float = 1.0) -> "OrderBook":
        """Пересчёт в «USD за BTC» и «BTC»: цена × price_mult, количество / qty_div."""
        return OrderBook(
            bids=[Level(l.price * price_mult, l.qty / qty_div) for l in self.bids],
            asks=[Level(l.price * price_mult, l.qty / qty_div) for l in self.asks],
            ts=self.ts,
            source=self.source,
        )

    def to_dict(self, depth: int = 15) -> dict:
        return {
            "bids": [[l.price, l.qty] for l in self.bids[:depth]],
            "asks": [[l.price, l.qty] for l in self.asks[:depth]],
            "ts": self.ts,
        }
