from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from ..config import PairConfig
from ..models import OrderBook
from .bybit import BybitClient
from .moex import MoexClient


class LiveProvider:
    name = "live"

    def __init__(self, moex_token: str, bybit: BybitClient | None = None, moex: MoexClient | None = None):
        self.bybit = bybit or BybitClient()
        self.moex = moex or MoexClient(moex_token)
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="books")

    def books(self, pair: PairConfig) -> tuple[OrderBook, OrderBook]:
        f_moex = self._pool.submit(self.moex.orderbook, pair.moex_ticker)
        f_bybit = self._pool.submit(self.bybit.orderbook, pair.bybit_category, pair.bybit_symbol)
        # оба запроса идут параллельно; ошибка любого из них пробрасывается наверх
        return f_moex.result(timeout=15), f_bybit.result(timeout=15)
