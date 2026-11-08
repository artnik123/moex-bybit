"""Стакан и свечи MOEX через moexalgo (нужен токен MOEX Algopack)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from ..models import OrderBook
from ..orderbook import parse_moex


class MoexClient:
    def __init__(self, token: str):
        if not token:
            raise ValueError("Нужен MOEX_TOKEN")
        try:
            from moexalgo import session
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Для live-режима установите moexalgo: pip install moexalgo") from exc
        session.TOKEN = token

    def orderbook(self, ticker: str) -> OrderBook:
        from moexalgo import Ticker

        return parse_moex(Ticker(ticker).orderbook())

    def close_prices(self, ticker: str, days: int, period: str = "10min") -> pd.DataFrame:
        """Цены закрытия свечей. Возвращает DataFrame[time_msk, price] (время московское, без tz)."""
        from moexalgo import Ticker

        end = datetime.now()
        start = end - timedelta(days=days)
        df = Ticker(ticker).candles(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"), period=period)
        df = pd.DataFrame(df)
        if df.empty:
            return pd.DataFrame(columns=["time_msk", "price"])
        time_col = next((c for c in ("begin", "date", "ts", "time", "datetime", "tradedate") if c in df.columns), None)
        price_col = next((c for c in ("close", "last", "price", "closeprice") if c in df.columns), None)
        if not time_col or not price_col:
            raise ValueError(f"Не нашёл столбцы времени/цены в свечах MOEX: {list(df.columns)}")
        out = pd.DataFrame(
            {"time_msk": pd.to_datetime(df[time_col]), "price": pd.to_numeric(df[price_col], errors="coerce")}
        )
        return out.dropna().drop_duplicates("time_msk").sort_values("time_msk").reset_index(drop=True)
