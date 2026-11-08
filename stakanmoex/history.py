"""Сборка исторического CSV (MOEX + Bybit) для бэктеста: замена kline_hist_scan.py."""
from __future__ import annotations

import pandas as pd

from .config import PairConfig
from .providers.bybit import BybitClient
from .providers.moex import MoexClient

MSK_OFFSET = pd.Timedelta(hours=3)  # Москва = UTC+3 круглый год


def merge_prices(moex: pd.DataFrame, bybit: pd.DataFrame, moex_multiplier: float = 1.0,
                 tolerance: str = "2min") -> pd.DataFrame:
    """moex: [time_msk, price]; bybit: [time_utc, price] → [time, price_moex, price_bybit, diff, diff_pct].

    Время Bybit (UTC) явно переводится в московское — в исходных скриптах сравнивалось локальное время
    машины со временем MOEX, что давало сдвиг при запуске не в МСК.
    """
    if moex.empty or bybit.empty:
        raise ValueError("Нет данных: одна из бирж вернула пустой ответ")
    left = pd.DataFrame({"time": moex["time_msk"], "price_moex": moex["price"].astype(float) * moex_multiplier})
    right = pd.DataFrame({"time": bybit["time_utc"] + MSK_OFFSET, "price_bybit": bybit["price"].astype(float)})
    merged = pd.merge_asof(
        left.sort_values("time"), right.sort_values("time"),
        on="time", direction="nearest", tolerance=pd.Timedelta(tolerance),
    ).dropna()
    merged["diff"] = merged["price_moex"] - merged["price_bybit"]
    merged["diff_pct"] = merged["diff"].abs() / merged[["price_moex", "price_bybit"]].min(axis=1)
    return merged.reset_index(drop=True)


def build_history(pair: PairConfig, days: int, moex: MoexClient, bybit: BybitClient) -> pd.DataFrame:
    moex_df = moex.close_prices(pair.moex_ticker, days)
    bybit_df = bybit.close_prices(pair.bybit_category, pair.bybit_symbol, days)
    return merge_prices(moex_df, bybit_df, pair.moex_multiplier)
