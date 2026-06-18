"""
v11.2 KIS 당일 1분봉 폴링 + Mock 스트리머.

LiveAccountGateway.fetch_today_minute_bars() 기반 — 네이버 크롤링 미사용.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from queue import Empty, Queue
from zoneinfo import ZoneInfo

import requests

from src.live.live_account import LiveAccountGateway
from src.live.minute_bar import MinuteBar

logger = logging.getLogger("KisMinuteCrawler")
KST = ZoneInfo("Asia/Seoul")


def generate_mock_day_bars(
    code: str,
    *,
    base_price: float = 50_000.0,
    trade_date: datetime | None = None,
) -> list[MinuteBar]:
    """Mock 모드용 하루치 1분봉 (09:00~15:30)."""
    c6 = str(code).zfill(6)
    day = trade_date or datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    if day.tzinfo is None:
        day = day.replace(tzinfo=KST)

    bars: list[MinuteBar] = []
    px = base_price
    rng = random.Random(int(c6) + day.day)

    for minute_offset in range(391):
        dt = day.replace(hour=9, minute=0) + timedelta(minutes=minute_offset)
        if dt.hour > 15 or (dt.hour == 15 and dt.minute > 30):
            break

        drift = rng.uniform(-0.003, 0.004)
        if minute_offset < 15:
            drift = abs(drift) * 0.6
        if 16 <= minute_offset <= 90:
            drift = max(drift, 0.0015)

        o = px
        c = px * (1.0 + drift)
        h = max(o, c) * (1.0 + rng.uniform(0, 0.002))
        l = min(o, c) * (1.0 - rng.uniform(0, 0.002))
        vol = int(rng.uniform(5_000, 80_000))
        if 16 <= minute_offset <= 25:
            vol = int(vol * rng.uniform(2.0, 4.0))
        bars.append(
            MinuteBar(code=c6, dt=dt, open=o, high=h, low=l, close=c, volume=vol)
        )
        px = c
    return bars


@dataclass
class KisMinuteCrawler:
    """KIS 당일분봉 폴링 — 감시 종목 20~30개."""

    watch_codes: list[str]
    gateway: LiveAccountGateway
    bar_queue: Queue = field(default_factory=Queue)
    request_delay_sec: float = 0.55
    jitter_sec: float = 0.15
    min_request_delay_sec: float = 0.5
    max_codes: int = 30
    _last_bars: dict[str, MinuteBar] = field(default_factory=dict, repr=False)
    _day_bars: dict[str, list[MinuteBar]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.watch_codes = [str(c).zfill(6) for c in self.watch_codes[: self.max_codes]]

    @property
    def latest_bars(self) -> dict[str, MinuteBar]:
        return dict(self._last_bars)

    def bars_for(self, code: str) -> list[MinuteBar]:
        return list(self._day_bars.get(str(code).zfill(6), []))

    def _sleep_between_requests(self) -> None:
        delay = max(self.min_request_delay_sec, self.request_delay_sec + random.uniform(0, self.jitter_sec))
        time.sleep(delay)

    def poll_once(self, *, now: datetime | None = None) -> int:
        """전 종목 1회 KIS 분봉 폴링. 신규/갱신 봉 수 반환."""
        cur = now or datetime.now(KST)
        if cur.tzinfo is None:
            cur = cur.replace(tzinfo=KST)

        pushed = 0
        for code in self.watch_codes:
            try:
                bars = self.gateway.fetch_today_minute_bars(code, end_dt=cur)
            except (requests.Timeout, requests.RequestException) as exc:
                logger.warning("KIS 분봉 통신 실패 %s — %s (스킵)", code, exc)
                self._sleep_between_requests()
                continue
            except Exception as exc:
                logger.warning("KIS 분봉 조회 실패 %s — %s (스킵)", code, exc)
                self._sleep_between_requests()
                continue

            if not bars:
                self._sleep_between_requests()
                continue

            self._day_bars[code] = bars
            latest = bars[-1]
            prev = self._last_bars.get(code)
            if prev is None or latest.dt != prev.dt or latest.close != prev.close:
                self._last_bars[code] = latest
                self.bar_queue.put(latest)
                pushed += 1
            self._sleep_between_requests()
        return pushed

    def drain_queue(self, *, max_items: int = 500) -> list[MinuteBar]:
        out: list[MinuteBar] = []
        for _ in range(max_items):
            try:
                out.append(self.bar_queue.get_nowait())
            except Empty:
                break
        return out


@dataclass
class MockMinuteStreamer:
    """Mock: 하루치 분봉을 시간 순으로 Queue에 스트리밍."""

    watch_codes: list[str]
    bar_queue: Queue = field(default_factory=Queue)
    speed_sec: float = 0.05
    _all_bars: dict[str, list[MinuteBar]] = field(default_factory=dict, repr=False)
    _cursor: int = 0

    def __post_init__(self) -> None:
        bases = [50_000, 82_000, 120_000, 35_000, 18_000]
        for i, code in enumerate(self.watch_codes):
            c6 = str(code).zfill(6)
            self._all_bars[c6] = generate_mock_day_bars(c6, base_price=bases[i % len(bases)])

    @property
    def max_minutes(self) -> int:
        if not self._all_bars:
            return 0
        return max(len(v) for v in self._all_bars.values())

    def bars_for(self, code: str) -> list[MinuteBar]:
        c6 = str(code).zfill(6)
        return self._all_bars.get(c6, [])[: self._cursor]

    def step(self) -> bool:
        if self._cursor >= self.max_minutes:
            return False
        for code, bars in self._all_bars.items():
            if self._cursor < len(bars):
                self.bar_queue.put(bars[self._cursor])
        self._cursor += 1
        return True

    def current_sim_time(self) -> datetime | None:
        if self._cursor <= 0:
            return None
        any_code = next(iter(self._all_bars), None)
        if not any_code:
            return None
        bars = self._all_bars[any_code]
        idx = min(self._cursor - 1, len(bars) - 1)
        return bars[idx].dt
