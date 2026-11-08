"""SQLite-хранилище: тики спреда и события стратегии (вместо россыпи CSV в исходных скриптах)."""
from __future__ import annotations

import csv
import io
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS ticks (
    ts REAL NOT NULL,
    pair_id TEXT NOT NULL,
    moex_bid REAL, moex_ask REAL, bybit_bid REAL, bybit_ask REAL,
    premium_pct REAL, net_pct REAL, edge_a REAL, edge_b REAL
);
CREATE INDEX IF NOT EXISTS idx_ticks_pair_ts ON ticks(pair_id, ts);
CREATE TABLE IF NOT EXISTS events (
    ts REAL NOT NULL,
    pair_id TEXT NOT NULL,
    type TEXT NOT NULL,
    direction TEXT,
    net_pct REAL,
    open_net_pct REAL,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
"""


class Storage:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def add_tick(self, pair_id: str, m) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO ticks VALUES (?,?,?,?,?,?,?,?,?,?)",
                (m.ts, pair_id, m.moex_bid, m.moex_ask, m.bybit_bid, m.bybit_ask,
                 m.premium_pct, m.net_pct, m.edge_buy_moex_sell_bybit_pct, m.edge_buy_bybit_sell_moex_pct),
            )
            self._conn.commit()

    def add_event(self, ev) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?,?)",
                (ev.ts, ev.pair_id, ev.type, ev.direction, ev.net_pct, ev.open_net_pct, ev.note),
            )
            self._conn.commit()

    def history(self, pair_id: str, since_ts: float, max_points: int = 1000) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, premium_pct, net_pct, edge_a, edge_b FROM ticks "
                "WHERE pair_id=? AND ts>=? ORDER BY ts",
                (pair_id, since_ts),
            ).fetchall()
        data = [dict(r) for r in rows]
        if len(data) > max_points:
            step = len(data) / max_points
            data = [data[int(i * step)] for i in range(max_points)] + [data[-1]]
        return data

    def events(self, limit: int = 50, pair_id: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM events", []
        if pair_id:
            q += " WHERE pair_id=?"
            args.append(pair_id)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            return [dict(r) for r in self._conn.execute(q, args).fetchall()]

    def export_csv(self, pair_id: str) -> str:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM ticks WHERE pair_id=? ORDER BY ts", (pair_id,)).fetchall()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["ts", "pair_id", "moex_bid", "moex_ask", "bybit_bid", "bybit_ask",
                    "premium_pct", "net_pct", "edge_buy_moex_sell_bybit", "edge_buy_bybit_sell_moex"])
        for r in rows:
            w.writerow(list(r))
        return buf.getvalue()

    def prune(self, retention_days: int) -> None:
        cutoff = time.time() - retention_days * 86400
        with self._lock:
            self._conn.execute("DELETE FROM ticks WHERE ts<?", (cutoff,))
            self._conn.commit()
