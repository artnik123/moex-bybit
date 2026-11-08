import io
import unittest
from datetime import date, datetime

import pandas as pd

from stakanmoex.backtest import BacktestParams, load_prices, run_backtest
from stakanmoex.config import Stage, load_settings, parse_thresholds
from stakanmoex.history import merge_prices
from stakanmoex.models import Level, OrderBook
from stakanmoex.orderbook import parse_bybit, parse_moex, vwap
from stakanmoex.signals import SignalEngine, ThresholdSchedule
from stakanmoex.spread import compute_metrics


def book(bids, asks, **kw):
    return OrderBook([Level(*b) for b in bids], [Level(*a) for a in asks], **kw)


class VwapTests(unittest.TestCase):
    def test_single_level(self):
        f = vwap([Level(100, 5)], 2)
        self.assertEqual((f.avg_price, f.filled_qty, f.complete), (100, 2, True))

    def test_walks_levels(self):
        f = vwap([Level(100, 1), Level(102, 3)], 2)  # 1@100 + 1@102
        self.assertAlmostEqual(f.avg_price, 101)
        self.assertTrue(f.complete)

    def test_insufficient_depth_is_flagged_not_diluted(self):
        f = vwap([Level(100, 1)], 4)
        self.assertEqual(f.avg_price, 100)  # а не 25, как при делении на целевой объём
        self.assertEqual(f.filled_qty, 1)
        self.assertFalse(f.complete)

    def test_empty(self):
        self.assertIsNone(vwap([], 1).avg_price)

    def test_bad_target(self):
        with self.assertRaises(ValueError):
            vwap([Level(1, 1)], 0)


class ParseTests(unittest.TestCase):
    def test_bybit(self):
        ob = parse_bybit({"b": [["99", "1"], ["100", "2"]], "a": [["102", "1"], ["101", "0"]], "ts": 1700000000000})
        self.assertEqual(ob.best_bid, 100)  # отсортировано
        self.assertEqual(ob.best_ask, 102)  # нулевой объём отброшен
        self.assertEqual(ob.ts, 1700000000.0)

    def test_moex_uses_buysell_not_row_position(self):
        raw = [
            {"buysell": "S", "price": 105, "quantity": 3},
            {"buysell": "B", "price": 100, "quantity": 2},
            {"buysell": "B", "price": 101, "quantity": 1},
            {"buysell": "S", "price": 104, "quantity": 4},
        ]
        ob = parse_moex(raw)
        self.assertEqual([l.price for l in ob.bids], [101, 100])
        self.assertEqual([l.price for l in ob.asks], [104, 105])

    def test_moex_missing_columns(self):
        with self.assertRaises(ValueError):
            parse_moex([{"price": 1, "quantity": 1}])

    def test_moex_empty(self):
        ob = parse_moex(pd.DataFrame())
        self.assertEqual((ob.bids, ob.asks), ([], []))

    def test_scaling(self):
        ob = book([(10, 100)], [(11, 200)]).scaled(price_mult=1000, qty_div=100)
        self.assertEqual((ob.best_bid, ob.bids[0].qty), (10000, 1.0))
        self.assertEqual((ob.best_ask, ob.asks[0].qty), (11000, 2.0))


class SpreadTests(unittest.TestCase):
    def test_metrics(self):
        moex = book([(102_000, 5)], [(102_100, 5)])
        bybit = book([(100_000, 5)], [(100_100, 5)])
        m = compute_metrics(moex, bybit, 1.0, fee_pct=0.01)
        self.assertAlmostEqual(m.bid_basis_pct, 2.0)
        self.assertAlmostEqual(m.ask_basis_pct, (102_100 - 100_100) / 100_100 * 100)
        self.assertAlmostEqual(m.net_pct, m.premium_pct - 0.01)
        self.assertEqual(m.direction, "short_moex_long_bybit")
        # купить Bybit по ask и продать MOEX по bid — выгодно, обратное — нет
        self.assertGreater(m.edge_buy_bybit_sell_moex_pct, 0)
        self.assertLess(m.edge_buy_moex_sell_bybit_pct, 0)

    def test_negative_premium_direction(self):
        m = compute_metrics(book([(99_000, 5)], [(99_100, 5)]), book([(100_000, 5)], [(100_100, 5)]), 1, 0.0)
        self.assertEqual(m.direction, "long_moex_short_bybit")
        self.assertLess(m.premium_pct, 0)
        self.assertGreater(m.net_pct, 0)  # net считается по модулю

    def test_empty_book_returns_none(self):
        self.assertIsNone(compute_metrics(book([], []), book([(1, 1)], [(2, 1)]), 1, 0))

    def test_depth_flag(self):
        m = compute_metrics(book([(100, 0.5)], [(101, 0.5)]), book([(100, 5)], [(101, 5)]), 1.0, 0)
        self.assertFalse(m.depth_ok)


class ThresholdAndSignalTests(unittest.TestCase):
    def setUp(self):
        self.sched = ThresholdSchedule(parse_thresholds("0:4:2,21:3:2,41:2.5:1"), date(2025, 11, 18))

    def test_schedule_stages(self):
        self.assertEqual(self.sched.at(datetime(2025, 11, 18)), (4, 2))
        self.assertEqual(self.sched.at(datetime(2025, 12, 8)), (4, 2))  # день 20
        self.assertEqual(self.sched.at(datetime(2025, 12, 9)), (3, 2))  # день 21
        self.assertEqual(self.sched.at(datetime(2026, 1, 1)), (2.5, 1))  # день 44
        self.assertEqual(self.sched.at(datetime(2025, 1, 1)), (4, 2))  # до старта — первый этап

    def test_constant(self):
        self.assertEqual(ThresholdSchedule.constant(1.5, 0.5).at(datetime(2030, 1, 1)), (1.5, 0.5))

    def test_engine_open_close_cycle(self):
        eng = SignalEngine("p", ThresholdSchedule.constant(2.0, 1.0))
        ts = 1_700_000_000
        self.assertIsNone(eng.update(ts, 1.5, "short_moex_long_bybit"))
        ev = eng.update(ts + 1, 2.5, "short_moex_long_bybit")
        self.assertEqual(ev.type, "open")
        self.assertIsNone(eng.update(ts + 2, 1.5, "short_moex_long_bybit"))  # гистерезис: пока > close
        self.assertIsNone(eng.update(ts + 3, 3.0, "short_moex_long_bybit"))  # повторно не открываем
        ev = eng.update(ts + 4, 0.9, "short_moex_long_bybit")
        self.assertEqual((ev.type, ev.open_net_pct), ("close", 2.5))
        self.assertFalse(eng.in_position)

    def test_bad_thresholds_text(self):
        with self.assertRaises(ValueError):
            parse_thresholds("")
        self.assertEqual(parse_thresholds("21:3:2,0:4:2")[0], Stage(0, 4, 2))


class BacktestTests(unittest.TestCase):
    def frame(self, prem_pcts, base=100_000.0):
        t0 = pd.Timestamp("2026-01-01 10:00")
        rows = [{"time": t0 + pd.Timedelta(minutes=10 * i), "price_moex": base * (1 + p / 100), "price_bybit": base}
                for i, p in enumerate(prem_pcts)]
        return pd.DataFrame(rows)

    def test_single_trade_pnl(self):
        # открытие при 5%, закрытие при 1% → ≈ +4% на рабочую половину капитала = +2% на весь
        df = self.frame([0.5, 5.0, 4.0, 1.0, 0.5])
        res = run_backtest(df, ThresholdSchedule.constant(4.0, 2.0), BacktestParams(100_000, 0.5, fee_pct=0.0))
        self.assertEqual(res.stats["trades"], 1)
        self.assertAlmostEqual(res.stats["pnl"], 50_000 * 0.04, delta=1)
        self.assertAlmostEqual(res.stats["return_pct"], 2.0, places=2)
        self.assertIsNone(res.open_at_end)

    def test_rebalance_after_trade(self):
        df = self.frame([5, 1, 5, 1])
        res = run_backtest(df, ThresholdSchedule.constant(4.0, 2.0), BacktestParams(100_000, 0.5, fee_pct=0.0))
        t1, t2 = res.trades
        self.assertAlmostEqual(t2["pnl"], (t1["equity_after"] / 2) * 0.04, delta=1)  # вторая сделка на 50% от нового капитала

    def test_negative_premium_trade(self):
        df = self.frame([-5.0, -1.0])
        res = run_backtest(df, ThresholdSchedule.constant(4.0, 2.0), BacktestParams(fee_pct=0.0))
        self.assertEqual(res.trades[0]["direction"], "long_moex_short_bybit")

    def test_open_position_at_end_marks_to_market(self):
        res = run_backtest(self.frame([0.0, 5.0, 6.0]), ThresholdSchedule.constant(4.0, 2.0), BacktestParams(fee_pct=0.0))
        self.assertEqual(res.stats["trades"], 0)
        self.assertIsNotNone(res.open_at_end)
        self.assertLess(res.equity[-1][1], 100_000)  # премия выросла против позиции

    def test_outlier_filter(self):
        df = self.frame([0.5, 18.0, 1.0])
        plain = run_backtest(df, ThresholdSchedule.constant(4.0, 2.0), BacktestParams(fee_pct=0.0))
        filt = run_backtest(df, ThresholdSchedule.constant(4.0, 2.0), BacktestParams(fee_pct=0.0, outlier_pct=10))
        self.assertEqual(plain.stats["trades"], 1)
        self.assertEqual((filt.stats["trades"], filt.stats["skipped_outliers"]), (0, 1))

    def test_bad_share(self):
        with self.assertRaises(ValueError):
            run_backtest(self.frame([1, 2]), ThresholdSchedule.constant(4, 2), BacktestParams(working_share=0))


class LoaderTests(unittest.TestCase):
    def test_aliases_and_cleaning(self):
        csv = "time,price_moex1,price_byb1\n2026-01-01 10:10,110,100\n2026-01-01 10:00,100,100\nbad,1,1\n2026-01-01 10:00,100,100\n2026-01-01 10:20,,100\n"
        df = load_prices(io.StringIO(csv))
        self.assertEqual(len(df), 2)
        self.assertTrue(df["time"].is_monotonic_increasing)

    def test_missing_column(self):
        with self.assertRaises(ValueError):
            load_prices(io.StringIO("time,foo\n2026-01-01,1\n"))

    def test_samples_load(self):
        from stakanmoex.datasets import SAMPLES

        for s in SAMPLES.values():
            self.assertGreater(len(load_prices(str(s.path), s.moex_col, s.bybit_col)), 1000)


class HistoryTests(unittest.TestCase):
    def test_merge_converts_utc_to_msk_and_multiplier(self):
        moex = pd.DataFrame({"time_msk": pd.to_datetime(["2026-01-01 13:00", "2026-01-01 13:10"]), "price": [60.0, 61.0]})
        bybit = pd.DataFrame({"time_utc": pd.to_datetime(["2026-01-01 10:00", "2026-01-01 10:10"]), "price": [100_000.0, 101_000.0]})
        out = merge_prices(moex, bybit, moex_multiplier=1700)
        self.assertEqual(len(out), 2)  # 10:00 UTC == 13:00 МСК
        self.assertAlmostEqual(out["price_moex"].iloc[0], 102_000)
        self.assertAlmostEqual(out["diff_pct"].iloc[0], 0.02)

    def test_merge_empty(self):
        with self.assertRaises(ValueError):
            merge_prices(pd.DataFrame(columns=["time_msk", "price"]), pd.DataFrame(columns=["time_utc", "price"]))


class SettingsTests(unittest.TestCase):
    def test_auto_mode(self):
        self.assertEqual(load_settings({"DB_PATH": ":memory:"}).mode, "demo")
        self.assertEqual(load_settings({"MOEX_TOKEN": "x", "DB_PATH": ":memory:"}).mode, "live")

    def test_live_requires_token(self):
        with self.assertRaises(ValueError):
            load_settings({"DATA_MODE": "live"})

    def test_bad_mode(self):
        with self.assertRaises(ValueError):
            load_settings({"DATA_MODE": "zzz"})

    def test_fee(self):
        self.assertAlmostEqual(load_settings({"DATA_MODE": "demo"}).fee_pct, 0.01066)


if __name__ == "__main__":
    unittest.main()
