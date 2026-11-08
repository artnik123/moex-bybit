"""Публичное REST API Bybit v5. Для стаканов и свечей API-ключи не нужны."""
from __future__ import annotations

import time

import requests

from ..config import PairConfig
from ..models import OrderBook
from ..orderbook import parse_bybit

BASE_URL = "https://api.bybit.com"


class BybitError(RuntimeError):
    pass


class BybitClient:
    def __init__(self, base_url: str = BASE_URL, timeout: float = 5.0, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()

    def _get(self, path: str, params: dict) -> dict:
        resp = self.session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("retCode") != 0:
            raise BybitError(f"Bybit: {data.get('retMsg')} (retCode={data.get('retCode')})")
        return data["result"]

    def orderbook(self, category: str, symbol: str, limit: int = 50) -> OrderBook:
        result = self._get("/v5/market/orderbook", {"category": category, "symbol": symbol, "limit": limit})
        return parse_bybit(result)

    def klines(self, category: str, symbol: str, interval: str = "5", start_ms: int | None = None,
               end_ms: int | None = None, limit: int = 1000) -> list[list[str]]:
        """Свечи от новых к старым: [start_ms, open, high, low, close, volume, turnover]."""
        params = {"category": category, "symbol": symbol, "interval": interval, "limit": limit}
        if start_ms is not None:
            params["start"] = start_ms
        if end_ms is not None:
            params["end"] = end_ms
        return self._get("/v5/market/kline", params).get("list", [])

    def close_prices(self, category: str, symbol: str, days: int, interval_min: int = 5):
        """Цены закрытия за `days` дней (пагинация назад по времени). Возвращает DataFrame[time_utc, price]."""
        import pandas as pd

        end_ms = int(time.time() * 1000)
        stop_ms = end_ms - days * 86_400_000
        rows: list[tuple[int, float]] = []
        while end_ms > stop_ms:
            chunk = self.klines(category, symbol, str(interval_min), end_ms=end_ms, limit=1000)
            if not chunk:
                break
            rows.extend((int(c[0]), float(c[4])) for c in chunk)
            oldest = int(chunk[-1][0])
            if oldest >= end_ms:
                break
            end_ms = oldest - 1
            if len(chunk) < 1000:
                break
        df = pd.DataFrame(rows, columns=["ts_ms", "price"]).drop_duplicates("ts_ms").sort_values("ts_ms")
        df["time_utc"] = pd.to_datetime(df["ts_ms"], unit="ms")
        return df[["time_utc", "price"]].reset_index(drop=True)
