"""v11.2 공통 1분봉 OHLCV 모델."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class MinuteBar:
    code: str
    dt: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int

    @property
    def hm(self) -> str:
        return self.dt.strftime("%H:%M")
