"""Примеры исторических данных, которые лежат в репозитории (data/samples)."""
from __future__ import annotations

from dataclasses import dataclass

from .config import ROOT

SAMPLES_DIR = ROOT / "data" / "samples"


@dataclass(frozen=True)
class Sample:
    id: str
    title: str
    file: str
    moex_col: str
    bybit_col: str
    start_date: str  # от какой даты считать «возраст» пары для порогов

    @property
    def path(self):
        return SAMPLES_DIR / self.file


SAMPLES: dict[str, Sample] = {
    s.id: s
    for s in [
        Sample("single", "IBH6 ↔ BTCUSDT-27MAR26 · окт 2025 – янв 2026 · 10 мин",
               "moex_bybit_single.csv", "price_moex", "price_bybit", "2025-11-18"),
        Sample("pair1", "IBZ5 ↔ BTCUSDT-26DEC25 · ноя – дек 2025 · 10 мин",
               "moex_bybit_two_pairs.csv", "price_moex1", "price_byb1", "2025-11-18"),
        Sample("pair2", "BTF6 ↔ BTCUSDT-27MAR26 · ноя – дек 2025 · 10 мин",
               "moex_bybit_two_pairs.csv", "price_moex2", "price_byb2", "2025-11-18"),
    ]
}
