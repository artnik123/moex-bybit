from __future__ import annotations

import time
from datetime import date

from flask import Flask, Response, jsonify, render_template, request

from .. import __version__
from ..backtest import BacktestParams, load_prices, run_backtest
from ..config import Settings, load_settings
from ..datasets import SAMPLES
from ..monitor import Monitor
from ..providers.base import Provider
from ..providers.demo import DemoProvider
from ..signals import ThresholdSchedule
from ..storage import Storage


def build_provider(settings: Settings) -> Provider:
    if settings.mode == "live":
        from ..providers.live import LiveProvider

        return LiveProvider(settings.moex_token)
    return DemoProvider()


def _num(value, default: float, name: str, lo: float | None = None, hi: float | None = None) -> float:
    if value in (None, ""):
        return default
    try:
        x = float(str(value).replace(",", "."))
    except ValueError:
        raise ValueError(f"Поле «{name}» должно быть числом")
    if (lo is not None and x < lo) or (hi is not None and x > hi):
        raise ValueError(f"Поле «{name}» вне допустимого диапазона")
    return x


def create_app(settings: Settings | None = None, provider: Provider | None = None,
               start_monitor: bool = True) -> Flask:
    settings = settings or load_settings()
    storage = Storage(settings.db_path)
    monitor = Monitor(settings, provider or build_provider(settings), storage)

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # загрузка CSV до 5 МБ
    app.extensions["monitor"] = monitor
    app.extensions["storage"] = storage
    app.extensions["settings"] = settings

    @app.get("/")
    def index():
        return render_template("index.html", version=__version__)

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok", mode=monitor.provider.name)

    @app.get("/api/config")
    def api_config():
        return jsonify(
            mode=monitor.provider.name,
            version=__version__,
            fee_pct=settings.fee_pct,
            pairs=[{"id": p.id, "name": p.name} for p in settings.pairs],
            stages=[{"from_days": s.from_days, "open_pct": s.open_pct, "close_pct": s.close_pct} for s in settings.stages],
            datasets=[{"id": s.id, "title": s.title, "start_date": s.start_date} for s in SAMPLES.values()],
        )

    @app.get("/api/state")
    def api_state():
        return jsonify(monitor.snapshot())

    @app.get("/api/history/<pair_id>")
    def api_history(pair_id: str):
        if not settings.pair(pair_id):
            return jsonify(error="неизвестная пара"), 404
        try:
            minutes = _num(request.args.get("minutes"), 60, "minutes", 1, 60 * 24 * 30)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(points=storage.history(pair_id, time.time() - minutes * 60))

    @app.get("/api/events")
    def api_events():
        return jsonify(events=storage.events(limit=50, pair_id=request.args.get("pair")))

    @app.get("/api/export/<pair_id>.csv")
    def api_export(pair_id: str):
        if not settings.pair(pair_id):
            return jsonify(error="неизвестная пара"), 404
        return Response(
            storage.export_csv(pair_id), mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={pair_id}_ticks.csv"},
        )

    @app.post("/api/backtest")
    def api_backtest():
        form = request.form if request.files or request.form else (request.get_json(silent=True) or {})
        try:
            capital = _num(form.get("capital"), 100_000, "Капитал", 1)
            share = _num(form.get("working_share"), 0.5, "Доля рабочего капитала", 0.01, 1)
            fee_pct = _num(form.get("fee_pct"), settings.fee_pct, "Комиссия, %", 0, 5)
            outlier = _num(form.get("outlier_pct"), -1, "Фильтр выбросов, %", 0, 1000)
            open_pct = _num(form.get("open_pct"), -1, "Порог открытия", 0, 100)
            close_pct = _num(form.get("close_pct"), -1, "Порог закрытия", 0, 100)
            if (open_pct < 0) != (close_pct < 0):
                raise ValueError("Задайте оба порога (открытия и закрытия) или оставьте оба пустыми")
            if open_pct >= 0 and close_pct >= open_pct:
                raise ValueError("Порог закрытия должен быть меньше порога открытия")

            upload = request.files.get("file")
            if upload and upload.filename:
                df = load_prices(upload.stream)
                start = date.fromisoformat(form.get("start_date") or str(df["time"].iloc[0].date()))
            else:
                sample = SAMPLES.get(form.get("dataset") or "single")
                if not sample:
                    raise ValueError("Неизвестный набор данных")
                df = load_prices(str(sample.path), sample.moex_col, sample.bybit_col)
                start = date.fromisoformat(form.get("start_date") or sample.start_date)

            schedule = (ThresholdSchedule.constant(open_pct, close_pct) if open_pct >= 0
                        else ThresholdSchedule(settings.stages, start))
            result = run_backtest(df, schedule, BacktestParams(capital, share, fee_pct, outlier_pct=outlier if outlier >= 0 else None))
        except (ValueError, KeyError) as exc:
            return jsonify(error=str(exc)), 400
        payload = result.to_dict()
        payload["thresholds"] = [{"from_days": s.from_days, "open_pct": s.open_pct, "close_pct": s.close_pct}
                                 for s in schedule.stages]
        payload["start_date"] = schedule.start_date.isoformat()
        return jsonify(payload)

    @app.errorhandler(413)
    def too_large(_):
        return jsonify(error="Файл больше 5 МБ"), 413

    if start_monitor:
        monitor.start()
    return app
