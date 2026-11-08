"""Бэктест пороговой стратегии на исторических ценах.

Логика совпадает с исходным test.py / kline_hist_scan.py:
  * премия = (MOEX − Bybit) / min(MOEX, Bybit); чистая премия = |премия| − комиссии;
  * открытие при чистой премии > порога открытия, закрытие при < порога закрытия;
  * капитал делится пополам: торгует «рабочая» часть, после каждой закрытой сделки
    резерв и рабочая часть выравниваются (50/50);
  * прибыль сделки ≈ (премия на открытии − премия на закрытии) × рабочая часть.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import IO

import pandas as pd

from .signals import ThresholdSchedule


@dataclass
class BacktestParams:
    initial_capital: float = 100_000.0
    working_share: float = 0.5
    fee_pct: float = 0.0107  # (0.0001 + 0.0000066) × 100
    max_points: int = 1500  # сколько точек отдавать для графиков
    outlier_pct: float | None = None  # строки с |премией| выше этого значения считаем битыми свечами и пропускаем


@dataclass
class Trade:
    open_ts: float
    close_ts: float
    direction: str
    open_net_pct: float
    close_net_pct: float
    pnl: float
    pnl_pct: float  # от рабочей части капитала
    equity_after: float


@dataclass
class BacktestResult:
    stats: dict
    trades: list[dict]
    equity: list[list[float]]  # [[ts, equity], ...]
    net_series: list[list[float]]  # [[ts, net_pct], ...]
    open_at_end: dict | None = None
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


PRICE_ALIASES = {
    "moex": ["price_moex", "price_moex1", "moex", "moex_price"],
    "bybit": ["price_bybit", "price_byb1", "price_bybit1", "bybit", "bybit_price"],
}


def load_prices(
    source: str | IO,
    moex_col: str | None = None,
    bybit_col: str | None = None,
    time_col: str = "time",
) -> pd.DataFrame:
    """Читает CSV и возвращает DataFrame[time, price_moex, price_bybit], отсортированный по времени."""
    df = pd.read_csv(source)
    if time_col not in df.columns:
        raise ValueError(f"В файле нет столбца времени '{time_col}'. Есть: {list(df.columns)}")
    moex_col = moex_col or next((c for c in PRICE_ALIASES["moex"] if c in df.columns), None)
    bybit_col = bybit_col or next((c for c in PRICE_ALIASES["bybit"] if c in df.columns), None)
    for name, col in (("MOEX", moex_col), ("Bybit", bybit_col)):
        if col is None or col not in df.columns:
            raise ValueError(f"Не найден столбец цены {name}. Есть: {list(df.columns)}")

    out = pd.DataFrame(
        {
            "time": pd.to_datetime(df[time_col], errors="coerce"),
            "price_moex": pd.to_numeric(df[moex_col], errors="coerce"),
            "price_bybit": pd.to_numeric(df[bybit_col], errors="coerce"),
        }
    )
    out = out.dropna()
    out = out[(out.price_moex > 0) & (out.price_bybit > 0)]
    out = out.sort_values("time").drop_duplicates("time").reset_index(drop=True)
    if out.empty:
        raise ValueError("После очистки в данных не осталось строк")
    return out


def _epoch(ts: pd.Timestamp) -> float:
    # время в CSV — московское «наивное»; для графиков отдаём его как UTC-метку
    return float(ts.tz_localize("UTC").timestamp()) if ts.tzinfo is None else float(ts.timestamp())


def _downsample(points: list, limit: int) -> list:
    if len(points) <= limit:
        return points
    step = len(points) / limit
    idx = sorted({int(i * step) for i in range(limit)} | {len(points) - 1})
    return [points[i] for i in idx]


def run_backtest(df: pd.DataFrame, schedule: ThresholdSchedule, params: BacktestParams | None = None) -> BacktestResult:
    p = params or BacktestParams()
    if not 0 < p.working_share <= 1:
        raise ValueError("working_share должен быть в (0, 1]")

    reserve = p.initial_capital * (1 - p.working_share)
    working = p.initial_capital * p.working_share

    in_pos = False
    direction = ""
    open_net = 0.0
    open_ts = 0.0

    trades: list[Trade] = []
    equity: list[list[float]] = []
    net_series: list[list[float]] = []
    peak = p.initial_capital
    max_dd = 0.0

    times = df["time"].tolist()
    moex = df["price_moex"].astype(float).tolist()
    bybit = df["price_bybit"].astype(float).tolist()

    skipped = 0
    for t, m, b in zip(times, moex, bybit):
        ts = _epoch(t)
        prem = (m - b) / min(m, b) * 100
        if p.outlier_pct is not None and abs(prem) > p.outlier_pct:
            skipped += 1
            continue
        net = abs(prem) - p.fee_pct
        open_pct, close_pct = schedule.at(t.to_pydatetime() if isinstance(t, pd.Timestamp) else t)

        if not in_pos and net > open_pct:
            in_pos, direction, open_net, open_ts = True, ("short_moex_long_bybit" if prem >= 0 else "long_moex_short_bybit"), net, ts
        elif in_pos and net < close_pct:
            pnl_pct = (open_net - net) / 100
            new_working = working * (1 + pnl_pct)
            pnl = new_working - working
            total = reserve + new_working
            reserve = total * (1 - p.working_share)
            working = total - reserve
            trades.append(Trade(open_ts, ts, direction, open_net, net, pnl, pnl_pct * 100, total))
            in_pos = False

        total_now = reserve + working
        if in_pos:  # учитываем нереализованный результат, чтобы просадка была честной
            total_now = reserve + working * (1 + (open_net - net) / 100)
        peak = max(peak, total_now)
        max_dd = max(max_dd, (peak - total_now) / peak * 100)
        equity.append([ts, total_now])
        net_series.append([ts, net])

    final = equity[-1][1] if equity else p.initial_capital
    wins = sum(1 for t in trades if t.pnl > 0)
    holds = [(t.close_ts - t.open_ts) / 3600 for t in trades]
    stats = {
        "rows": len(df),
        "skipped_outliers": skipped,
        "from": df["time"].iloc[0].strftime("%Y-%m-%d %H:%M"),
        "to": df["time"].iloc[-1].strftime("%Y-%m-%d %H:%M"),
        "trades": len(trades),
        "wins": wins,
        "win_rate_pct": (wins / len(trades) * 100) if trades else 0.0,
        "initial_capital": p.initial_capital,
        "final_equity": final,
        "pnl": final - p.initial_capital,
        "return_pct": (final / p.initial_capital - 1) * 100,
        "realized_pnl": sum(t.pnl for t in trades),
        "max_drawdown_pct": max_dd,
        "avg_hold_hours": (sum(holds) / len(holds)) if holds else 0.0,
        "avg_trade_pct": (sum(t.pnl_pct for t in trades) / len(trades)) if trades else 0.0,
    }
    return BacktestResult(
        stats=stats,
        trades=[asdict(t) for t in trades],
        equity=_downsample(equity, p.max_points),
        net_series=_downsample(net_series, p.max_points),
        open_at_end={"direction": direction, "open_ts": open_ts, "open_net_pct": open_net} if in_pos else None,
        params=asdict(p),
    )
