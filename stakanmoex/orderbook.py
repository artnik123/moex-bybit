"""Разбор стаканов и расчёт средневзвешенной цены исполнения на заданный объём."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

from .models import Level, OrderBook


@dataclass(frozen=True)
class Fill:
    avg_price: float | None  # None, если в стакане вообще нет заявок
    filled_qty: float
    complete: bool  # хватило ли глубины стакана на весь объём


def vwap(levels: Iterable[Level], target_qty: float) -> Fill:
    """Средняя цена, по которой можно набрать `target_qty` по уровням стакана.

    В отличие от исходных скриптов, при нехватке глубины не делим «сумму на целевой объём»
    (это занижало цену), а возвращаем среднюю по реально доступному объёму и complete=False.
    """
    if target_qty <= 0:
        raise ValueError("target_qty должен быть > 0")
    remaining = target_qty
    cost = 0.0
    filled = 0.0
    for lvl in levels:
        take = min(lvl.qty, remaining)
        if take <= 0:
            continue
        cost += lvl.price * take
        filled += take
        remaining -= take
        if remaining <= 1e-12:
            break
    if filled == 0:
        return Fill(None, 0.0, False)
    return Fill(cost / filled, filled, remaining <= 1e-9)


def _clean(levels: list[Level]) -> list[Level]:
    return [l for l in levels if math.isfinite(l.price) and math.isfinite(l.qty) and l.qty > 0 and l.price > 0]


def parse_bybit(result: dict, source: str = "bybit") -> OrderBook:
    """`result` из ответа /v5/market/orderbook: {'b': [[price, size], ...], 'a': [...], 'ts': ms}."""
    bids = _clean([Level(float(p), float(q)) for p, q in result.get("b", [])])
    asks = _clean([Level(float(p), float(q)) for p, q in result.get("a", [])])
    bids.sort(key=lambda l: -l.price)
    asks.sort(key=lambda l: l.price)
    ts_ms = result.get("ts")
    ob = OrderBook(bids=bids, asks=asks, source=source)
    if ts_ms:
        ob.ts = float(ts_ms) / 1000.0
    return ob


def parse_moex(raw: Any, source: str = "moex") -> OrderBook:
    """Стакан из moexalgo: DataFrame или список словарей со столбцами price, quantity, buysell (B/S)."""
    import pandas as pd

    df = raw if isinstance(raw, pd.DataFrame) else pd.DataFrame(raw)
    if df.empty:
        return OrderBook(bids=[], asks=[], source=source)
    missing = {"price", "quantity", "buysell"} - set(df.columns)
    if missing:
        raise ValueError(f"В стакане MOEX нет столбцов: {sorted(missing)}; есть {list(df.columns)}")

    side = df["buysell"].astype(str).str.upper()
    price = pd.to_numeric(df["price"], errors="coerce")
    qty = pd.to_numeric(df["quantity"], errors="coerce")

    def build(mask) -> list[Level]:
        return _clean([Level(float(p), float(q)) for p, q in zip(price[mask], qty[mask])])

    bids = build(side == "B")
    asks = build(side == "S")
    bids.sort(key=lambda l: -l.price)
    asks.sort(key=lambda l: l.price)
    return OrderBook(bids=bids, asks=asks, source=source)
