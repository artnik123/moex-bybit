"""Точка входа:  python -m stakanmoex [web|backtest|history]"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date


def cmd_web(args) -> int:
    from .config import load_settings
    from .web.app import create_app

    settings = load_settings()
    app = create_app(settings)
    mode = app.extensions["monitor"].provider.name
    host, port = args.host or settings.host, args.port or settings.port
    print(f"stakanmoex · режим: {mode.upper()} · http://{host}:{port}")
    if mode == "demo":
        print("Демо-режим: данные синтетические. Для живых стаканов задайте MOEX_TOKEN в .env")
    app.run(host=host, port=port, threaded=True, use_reloader=False)
    return 0


def cmd_backtest(args) -> int:
    from .backtest import BacktestParams, load_prices, run_backtest
    from .config import load_settings
    from .datasets import SAMPLES
    from .signals import ThresholdSchedule

    settings = load_settings()
    if args.csv:
        df = load_prices(args.csv, args.moex_col, args.bybit_col)
        start = date.fromisoformat(args.start_date or str(df["time"].iloc[0].date()))
    else:
        sample = SAMPLES[args.dataset]
        df = load_prices(str(sample.path), sample.moex_col, sample.bybit_col)
        start = date.fromisoformat(args.start_date or sample.start_date)
    if (args.open_pct is None) != (args.close_pct is None):
        print("Задайте оба порога --open-pct и --close-pct", file=sys.stderr)
        return 2
    schedule = (ThresholdSchedule.constant(args.open_pct, args.close_pct) if args.open_pct is not None
                else ThresholdSchedule(settings.stages, start))
    res = run_backtest(df, schedule, BacktestParams(args.capital, 0.5, settings.fee_pct))
    for k, v in res.stats.items():
        print(f"{k:>18}: {v:,.2f}" if isinstance(v, float) else f"{k:>18}: {v}")
    for t in res.trades:
        print(f"  {t['direction']:<22} open {t['open_net_pct']:.2f}% → close {t['close_net_pct']:.2f}%  pnl {t['pnl']:+,.0f}")
    return 0


def cmd_history(args) -> int:
    from .config import load_settings
    from .history import build_history
    from .providers.bybit import BybitClient
    from .providers.moex import MoexClient

    settings = load_settings()
    pair = settings.pair(args.pair)
    if not pair:
        print(f"Нет пары '{args.pair}'. Доступны: {[p.id for p in settings.pairs]}", file=sys.stderr)
        return 2
    if not settings.moex_token:
        print("Для загрузки свечей MOEX нужен MOEX_TOKEN", file=sys.stderr)
        return 2
    df = build_history(pair, args.days, MoexClient(settings.moex_token), BybitClient())
    out = args.out or f"data/{pair.id}_history.csv"
    df.to_csv(out, index=False)
    print(f"Сохранено {len(df)} строк → {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="stakanmoex", description=__doc__)
    sub = parser.add_subparsers(dest="cmd")

    w = sub.add_parser("web", help="запустить веб-интерфейс (по умолчанию)")
    w.add_argument("--host")
    w.add_argument("--port", type=int)
    w.set_defaults(func=cmd_web)

    b = sub.add_parser("backtest", help="бэктест из командной строки")
    b.add_argument("--dataset", default="single", choices=["single", "pair1", "pair2"])
    b.add_argument("--csv", help="свой CSV: time, price_moex, price_bybit")
    b.add_argument("--moex-col")
    b.add_argument("--bybit-col")
    b.add_argument("--start-date")
    b.add_argument("--open-pct", type=float)
    b.add_argument("--close-pct", type=float)
    b.add_argument("--capital", type=float, default=100_000)
    b.set_defaults(func=cmd_backtest)

    h = sub.add_parser("history", help="скачать историю MOEX+Bybit в CSV для бэктеста")
    h.add_argument("--pair", required=True)
    h.add_argument("--days", type=int, default=30)
    h.add_argument("--out")
    h.set_defaults(func=cmd_history)

    args = parser.parse_args(argv)
    if not args.cmd:
        args = parser.parse_args(["web"] + (argv or []))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
