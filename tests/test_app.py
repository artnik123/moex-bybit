import io
import unittest

from stakanmoex.config import load_settings
from stakanmoex.models import Level, OrderBook
from stakanmoex.monitor import Monitor
from stakanmoex.providers.demo import DemoProvider
from stakanmoex.storage import Storage
from stakanmoex.web.app import create_app


def settings():
    return load_settings({"DATA_MODE": "demo", "DB_PATH": ":memory:", "STORE_EVERY": "0"})


class FlakyProvider:
    """Падает на заданных вызовах, потом отдаёт нормальные стаканы."""
    name = "fake"

    def __init__(self, fail_first=2, empty=False):
        self.calls, self.fail_first, self.empty = 0, fail_first, empty

    def books(self, pair):
        self.calls += 1
        if self.calls <= self.fail_first:
            raise ConnectionError("network down")
        if self.empty:
            return OrderBook([], []), OrderBook([Level(1, 1)], [Level(2, 1)])
        moex = OrderBook([Level(60.0, 5000)], [Level(60.1, 5000)])
        bybit = OrderBook([Level(100_000, 5)], [Level(100_010, 5)])
        return moex, bybit


class MonitorTests(unittest.TestCase):
    def test_errors_do_not_kill_monitor_and_recover(self):
        s = settings()
        mon = Monitor(s, FlakyProvider(fail_first=2), Storage(":memory:"))
        mon.tick_once()  # 2 пары → оба вызова падают
        snap = mon.snapshot()
        self.assertTrue(all(not p["ok"] and "network down" in p["error"] for p in snap["pairs"]))
        mon.tick_once()
        self.assertTrue(all(p["ok"] and p["error"] is None for p in mon.snapshot()["pairs"]))

    def test_empty_book_reported(self):
        mon = Monitor(settings(), FlakyProvider(fail_first=0, empty=True), Storage(":memory:"))
        mon.tick_once()
        self.assertIn("пустой стакан", mon.snapshot()["pairs"][0]["error"])

    def test_signals_are_persisted(self):
        s = settings()
        st = Storage(":memory:")
        mon = Monitor(s, DemoProvider(seed=3, mean_premium=6.0, volatility=0.05), st)  # премия заведомо выше порога
        for _ in range(5):
            mon.tick_once()
        events = st.events()
        self.assertTrue(any(e["type"] == "open" for e in events))
        self.assertGreater(len(st.history("ibh6", 0)), 0)


class StorageTests(unittest.TestCase):
    def test_history_downsample_and_export(self):
        s = settings()
        st = Storage(":memory:")
        mon = Monitor(s, DemoProvider(seed=1), st)
        for _ in range(30):
            mon.tick_once()
        self.assertLessEqual(len(st.history("btf6", 0, max_points=10)), 11)
        self.assertTrue(st.export_csv("btf6").startswith("ts,pair_id"))


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app(settings(), DemoProvider(seed=7), start_monitor=False)
        for _ in range(10):
            cls.app.extensions["monitor"].tick_once()
        cls.c = cls.app.test_client()

    def test_pages(self):
        self.assertEqual(self.c.get("/").status_code, 200)
        self.assertEqual(self.c.get("/healthz").json["status"], "ok")

    def test_state_and_config(self):
        st = self.c.get("/api/state").json
        self.assertEqual(st["mode"], "demo")
        self.assertEqual(len(st["pairs"]), 2)
        self.assertIsNotNone(st["pairs"][0]["metrics"])
        self.assertEqual(len(self.c.get("/api/config").json["datasets"]), 3)

    def test_history_validation(self):
        self.assertEqual(self.c.get("/api/history/ibh6?minutes=60").status_code, 200)
        self.assertEqual(self.c.get("/api/history/zzz").status_code, 404)
        self.assertEqual(self.c.get("/api/history/ibh6?minutes=abc").status_code, 400)
        self.assertEqual(self.c.get("/api/history/ibh6?minutes=-5").status_code, 400)

    def test_backtest_default(self):
        r = self.c.post("/api/backtest", json={"dataset": "single"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json["stats"]["trades"], 6)

    def test_backtest_validation(self):
        for body in ({"dataset": "zzz"}, {"dataset": "single", "open_pct": "3"},
                     {"dataset": "single", "open_pct": "1", "close_pct": "2"},
                     {"dataset": "single", "capital": "-5"}, {"dataset": "single", "fee_pct": "abc"}):
            r = self.c.post("/api/backtest", json=body)
            self.assertEqual(r.status_code, 400, body)
            self.assertIn("error", r.json)

    def test_backtest_upload(self):
        rows = "\n".join(f"2026-01-01 10:{i:02d}:00,{100000 + 5000 * (i % 2)},100000" for i in range(40))
        data = {"file": (io.BytesIO(("time,price_moex,price_bybit\n" + rows).encode()), "x.csv"),
                "open_pct": "4", "close_pct": "1"}
        r = self.c.post("/api/backtest", data=data, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200)
        self.assertGreater(r.json["stats"]["trades"], 5)

    def test_backtest_bad_upload(self):
        data = {"file": (io.BytesIO(b"foo,bar\n1,2\n"), "x.csv")}
        r = self.c.post("/api/backtest", data=data, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)

    def test_export(self):
        r = self.c.get("/api/export/ibh6.csv")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r.content_type)
        self.assertEqual(self.c.get("/api/export/zzz.csv").status_code, 404)


if __name__ == "__main__":
    unittest.main()
