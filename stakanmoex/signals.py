"""Пороговая логика стратегии: открыть позицию при большой премии, закрыть, когда премия схлопнулась."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from .config import Stage


class ThresholdSchedule:
    """Пороги зависят от возраста пары: чем ближе экспирация, тем меньше премия."""

    def __init__(self, stages: list[Stage], start_date: date):
        if not stages:
            raise ValueError("нужен хотя бы один этап порогов")
        self.stages = sorted(stages, key=lambda s: s.from_days)
        self.start_date = start_date

    @classmethod
    def constant(cls, open_pct: float, close_pct: float) -> "ThresholdSchedule":
        return cls([Stage(0, open_pct, close_pct)], date(1970, 1, 1))

    def at(self, moment: datetime | date | float) -> tuple[float, float]:
        if isinstance(moment, (int, float)):
            moment = datetime.fromtimestamp(moment, tz=timezone.utc)
        d = moment.date() if isinstance(moment, datetime) else moment
        days = max(0, (d - self.start_date).days)
        current = self.stages[0]
        for st in self.stages:
            if st.from_days <= days:
                current = st
        return current.open_pct, current.close_pct


@dataclass
class Event:
    ts: float
    pair_id: str
    type: str  # 'open' | 'close'
    direction: str
    net_pct: float
    open_net_pct: float | None = None  # при закрытии — значение на открытии
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "pair_id": self.pair_id,
            "type": self.type,
            "direction": self.direction,
            "net_pct": self.net_pct,
            "open_net_pct": self.open_net_pct,
            "note": self.note,
        }


class SignalEngine:
    """Автомат «вне рынка» ⇄ «в позиции» для одной пары. Сделок не совершает, только сигналы."""

    def __init__(self, pair_id: str, schedule: ThresholdSchedule):
        self.pair_id = pair_id
        self.schedule = schedule
        self.in_position = False
        self.direction: str | None = None
        self.opened_at: float | None = None
        self.open_net_pct: float | None = None

    def thresholds(self, ts: float) -> tuple[float, float]:
        return self.schedule.at(ts)

    def update(self, ts: float, net_pct: float, direction: str) -> Event | None:
        open_pct, close_pct = self.schedule.at(ts)
        if not self.in_position and net_pct > open_pct:
            self.in_position = True
            self.direction = direction
            self.opened_at = ts
            self.open_net_pct = net_pct
            return Event(ts, self.pair_id, "open", direction, net_pct, note=f"порог открытия {open_pct:g}%")
        if self.in_position and net_pct < close_pct:
            ev = Event(
                ts,
                self.pair_id,
                "close",
                self.direction or direction,
                net_pct,
                open_net_pct=self.open_net_pct,
                note=f"порог закрытия {close_pct:g}%",
            )
            self.in_position = False
            self.direction = None
            self.opened_at = None
            self.open_net_pct = None
            return ev
        return None

    def snapshot(self) -> dict:
        return {
            "in_position": self.in_position,
            "direction": self.direction,
            "opened_at": self.opened_at,
            "open_net_pct": self.open_net_pct,
        }
