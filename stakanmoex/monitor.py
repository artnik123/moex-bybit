"""Фоновый опрос бирж: считает метрики, ведёт сигналы, пишет историю."""
from __future__ import annotations

import logging
import threading
import time
from collections import deque

from .config import PairConfig, Settings
from .models import OrderBook
from .providers.base import Provider
from .signals import SignalEngine, ThresholdSchedule
from .spread import Metrics, compute_metrics
from .storage import Storage

log = logging.getLogger("stakanmoex.monitor")

MAX_BACKOFF = 30.0


class _PairRuntime:
    def __init__(self, pair: PairConfig, settings: Settings):
        self.pair = pair
        self.engine = SignalEngine(pair.id, ThresholdSchedule(settings.stages, settings.start_date_for(pair)))
        self.metrics: Metrics | None = None
        self.moex_book: OrderBook | None = None
        self.bybit_book: OrderBook | None = None
        self.error: str | None = None
        self.updated: float | None = None
        self.last_stored = 0.0
        self.fail_count = 0


class Monitor:
    def __init__(self, settings: Settings, provider: Provider, storage: Storage):
        self.settings = settings
        self.provider = provider
        self.storage = storage
        self._rt = {p.id: _PairRuntime(p, settings) for p in settings.pairs}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---------- жизненный цикл ----------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.storage.prune(self.settings.retention_days)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.time()
            self.tick_once()
            worst_fail = max((rt.fail_count for rt in self._rt.values()), default=0)
            delay = min(MAX_BACKOFF, self.settings.poll_interval * (2 ** min(worst_fail, 5))) if worst_fail else self.settings.poll_interval
            self._stop.wait(max(0.1, delay - (time.time() - started)))

    # ---------- один цикл опроса ----------
    def tick_once(self) -> None:
        for rt in self._rt.values():
            self._poll_pair(rt)

    def _poll_pair(self, rt: _PairRuntime) -> None:
        pair = rt.pair
        try:
            moex_native, bybit = self.provider.books(pair)
            moex = moex_native.scaled(pair.moex_multiplier, pair.moex_lots_per_btc)
            m = compute_metrics(moex, bybit, pair.size_btc, self.settings.fee_pct)
            if m is None:
                raise RuntimeError("пустой стакан (нет заявок на одной из сторон)")
        except Exception as exc:  # сеть, лимиты, токен — монитор не должен падать
            log.warning("%s: %s", pair.id, exc)
            with self._lock:
                rt.error = f"{type(exc).__name__}: {exc}"
                rt.fail_count += 1
            return

        event = rt.engine.update(m.ts, m.net_pct, m.direction)
        if event:
            self.storage.add_event(event)
        now = time.time()
        if now - rt.last_stored >= self.settings.store_every:
            self.storage.add_tick(pair.id, m)
            rt.last_stored = now
        with self._lock:
            rt.metrics, rt.moex_book, rt.bybit_book = m, moex, bybit
            rt.error, rt.fail_count, rt.updated = None, 0, now

    # ---------- чтение состояния ----------
    def snapshot(self) -> dict:
        out = []
        with self._lock:
            for rt in self._rt.values():
                p = rt.pair
                open_pct, close_pct = rt.engine.thresholds(time.time())
                out.append({
                    "id": p.id,
                    "name": p.name,
                    "moex_ticker": p.moex_ticker,
                    "bybit_symbol": p.bybit_symbol,
                    "size_btc": p.size_btc,
                    "ok": rt.error is None and rt.metrics is not None,
                    "error": rt.error,
                    "updated": rt.updated,
                    "metrics": rt.metrics.to_dict() if rt.metrics else None,
                    "thresholds": {"open": open_pct, "close": close_pct},
                    "position": rt.engine.snapshot(),
                    "books": {
                        "moex": rt.moex_book.to_dict() if rt.moex_book else None,
                        "bybit": rt.bybit_book.to_dict() if rt.bybit_book else None,
                    },
                })
        return {
            "mode": self.provider.name,
            "server_time": time.time(),
            "fee_pct": self.settings.fee_pct,
            "poll_interval": self.settings.poll_interval,
            "pairs": out,
        }
