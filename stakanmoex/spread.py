"""Метрики спреда между фьючерсом MOEX (в пересчёте на USD/BTC) и фьючерсом Bybit."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .models import OrderBook
from .orderbook import vwap


@dataclass
class Metrics:
    ts: float
    size_btc: float
    moex_bid: float
    moex_ask: float
    bybit_bid: float
    bybit_ask: float
    depth_ok: bool  # хватило ли глубины обоих стаканов на size_btc
    bid_basis_pct: float  # (MOEX bid − Bybit bid) / min × 100  — «procb» из исходных скриптов
    ask_basis_pct: float  # (MOEX ask − Bybit ask) / min × 100  — «procs» из исходных скриптов
    premium_pct: float  # среднее двух выше: премия MOEX к Bybit (знак: + MOEX дороже)
    net_pct: float  # |премия| − комиссии: именно это сравнивается с порогами
    edge_buy_moex_sell_bybit_pct: float  # реально исполнимый спред: купить на MOEX по ask, продать на Bybit по bid
    edge_buy_bybit_sell_moex_pct: float  # купить на Bybit по ask, продать на MOEX по bid
    direction: str  # 'short_moex_long_bybit' | 'long_moex_short_bybit'

    @property
    def moex_mid(self) -> float:
        return (self.moex_bid + self.moex_ask) / 2

    @property
    def bybit_mid(self) -> float:
        return (self.bybit_bid + self.bybit_ask) / 2

    def to_dict(self) -> dict:
        d = asdict(self)
        d["moex_mid"] = self.moex_mid
        d["bybit_mid"] = self.bybit_mid
        return d


def direction_for(premium_pct: float) -> str:
    return "short_moex_long_bybit" if premium_pct >= 0 else "long_moex_short_bybit"


def premium_pct(moex_price: float, bybit_price: float) -> float:
    """Премия MOEX к Bybit в % от меньшей цены (как в исходной стратегии)."""
    base = min(moex_price, bybit_price)
    return (moex_price - bybit_price) / base * 100


def compute_metrics(
    moex: OrderBook,
    bybit: OrderBook,
    size_btc: float,
    fee_pct: float,
) -> Metrics | None:
    """`moex` уже должен быть пересчитан в USD/BTC (см. OrderBook.scaled).

    Возвращает None, если в одном из стаканов нет заявок.
    """
    mb, ma = vwap(moex.bids, size_btc), vwap(moex.asks, size_btc)
    bb, ba = vwap(bybit.bids, size_btc), vwap(bybit.asks, size_btc)
    if None in (mb.avg_price, ma.avg_price, bb.avg_price, ba.avg_price):
        return None

    bid_basis = premium_pct(mb.avg_price, bb.avg_price)
    ask_basis = premium_pct(ma.avg_price, ba.avg_price)
    prem = (bid_basis + ask_basis) / 2

    return Metrics(
        ts=max(moex.ts, bybit.ts),
        size_btc=size_btc,
        moex_bid=mb.avg_price,
        moex_ask=ma.avg_price,
        bybit_bid=bb.avg_price,
        bybit_ask=ba.avg_price,
        depth_ok=all(f.complete for f in (mb, ma, bb, ba)),
        bid_basis_pct=bid_basis,
        ask_basis_pct=ask_basis,
        premium_pct=prem,
        net_pct=abs(prem) - fee_pct,
        edge_buy_moex_sell_bybit_pct=(bb.avg_price - ma.avg_price) / ma.avg_price * 100 - fee_pct,
        edge_buy_bybit_sell_moex_pct=(mb.avg_price - ba.avg_price) / ba.avg_price * 100 - fee_pct,
        direction=direction_for(prem),
    )
