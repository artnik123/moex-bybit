"""Настройки проекта: переменные окружения / .env + список пар из config/pairs.json."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Исходная стратегия: пороги сужаются по мере приближения экспирации.
DEFAULT_THRESHOLDS = "0:4:2,21:3:2,41:2.5:1"


def load_dotenv(path: Path | None = None) -> None:
    """Минимальный разбор .env (чтобы не тянуть лишнюю зависимость)."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.split(" #")[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


@dataclass(frozen=True)
class Stage:
    """С `from_days`-го дня жизни пары: открывать при ≥ open_pct %, закрывать при < close_pct %."""

    from_days: int
    open_pct: float
    close_pct: float


def parse_thresholds(text: str) -> list[Stage]:
    stages = []
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        d, o, c = chunk.split(":")
        stages.append(Stage(int(d), float(o), float(c)))
    if not stages:
        raise ValueError("THRESHOLDS не должен быть пустым")
    return sorted(stages, key=lambda s: s.from_days)


@dataclass(frozen=True)
class PairConfig:
    id: str
    name: str
    moex_ticker: str
    bybit_symbol: str
    bybit_category: str = "inverse"
    # цена MOEX × multiplier = USD за 1 BTC
    # (для IBIT-фьючерса: акций IBIT на 1 BTC = shares_outstanding / btc_holdings)
    moex_multiplier: float = 1.0
    # сколько лотов MOEX соответствует 1 BTC номинала
    moex_lots_per_btc: float = 1.0
    # размер сделки в BTC для расчёта средневзвешенной цены по стакану
    size_btc: float = 1.0
    start_date: str | None = None


@dataclass
class Settings:
    mode: str = "demo"  # demo | live
    moex_token: str = ""
    poll_interval: float = 2.0
    store_every: float = 5.0
    retention_days: int = 30
    bybit_fee: float = 0.0001
    moex_fee: float = 0.0000066
    stages: list[Stage] = field(default_factory=lambda: parse_thresholds(DEFAULT_THRESHOLDS))
    default_start_date: date = date(2025, 11, 18)
    db_path: Path = ROOT / "data" / "stakanmoex.db"
    host: str = "127.0.0.1"
    port: int = 8000
    pairs: list[PairConfig] = field(default_factory=list)

    @property
    def fee_pct(self) -> float:
        """Суммарная комиссия двух ног, в процентах."""
        return (self.bybit_fee + self.moex_fee) * 100

    def pair(self, pair_id: str) -> PairConfig | None:
        return next((p for p in self.pairs if p.id == pair_id), None)

    def start_date_for(self, pair: PairConfig) -> date:
        if pair.start_date:
            return date.fromisoformat(pair.start_date)
        return self.default_start_date


def load_pairs(path: Path) -> list[PairConfig]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [PairConfig(**item) for item in raw]


def load_settings(env: dict | None = None) -> Settings:
    load_dotenv()
    e = env if env is not None else os.environ

    token = e.get("MOEX_TOKEN", "").strip()
    mode = e.get("DATA_MODE", "auto").lower()
    if mode == "auto":
        mode = "live" if token else "demo"
    if mode not in ("demo", "live"):
        raise ValueError("DATA_MODE должен быть auto, demo или live")
    if mode == "live" and not token:
        raise ValueError("Для DATA_MODE=live нужен MOEX_TOKEN (токен MOEX Algopack)")

    pairs_file = Path(e.get("PAIRS_FILE", ROOT / "config" / "pairs.json"))
    db_path = Path(e.get("DB_PATH", ROOT / "data" / "stakanmoex.db"))

    return Settings(
        mode=mode,
        moex_token=token,
        poll_interval=float(e.get("POLL_INTERVAL", 2)),
        store_every=float(e.get("STORE_EVERY", 5)),
        retention_days=int(e.get("RETENTION_DAYS", 30)),
        bybit_fee=float(e.get("BYBIT_FEE", 0.0001)),
        moex_fee=float(e.get("MOEX_FEE", 0.0000066)),
        stages=parse_thresholds(e.get("THRESHOLDS", DEFAULT_THRESHOLDS)),
        default_start_date=date.fromisoformat(e.get("STRATEGY_START_DATE", "2025-11-18")),
        db_path=db_path,
        host=e.get("HOST", "127.0.0.1"),
        port=int(e.get("PORT", 8000)),
        pairs=load_pairs(pairs_file),
    )
