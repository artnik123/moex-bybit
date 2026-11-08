from __future__ import annotations

from typing import Protocol

from ..config import PairConfig
from ..models import OrderBook


class Provider(Protocol):
    """Источник стаканов. MOEX отдаётся в «родных» единицах (цена фьючерса, лоты) —
    пересчёт в USD/BTC делает монитор по настройкам пары."""

    name: str

    def books(self, pair: PairConfig) -> tuple[OrderBook, OrderBook]:
        """Возвращает (стакан MOEX в родных единицах, стакан Bybit). Бросает исключение при ошибке."""
        ...
