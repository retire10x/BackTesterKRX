"""
v11.2 네이버 증권 실시간 1분봉 크롤러.

소스: api.stock.naver.com/chart/domestic/item/{code}/minute
감시 종목 20~30개 · 호출 간 지연으로 부하 관리.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from queue import Empty, Queue
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger("NaverMinuteCrawler")
KST = ZoneInfo("Asia/Seoul")

NAVER_MINUTE_URL = "https://api.stock.naver.com/chart/domestic/item/{code}/minute"
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://m.stock.naver.com/",
}


@dataclass(frozen=True)
class MinuteBar:
    """1분봉 OHLCV."""

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


def _parse_bar(code: str, raw: dict) -> MinuteBar | None:
    ts = str(raw.get("localDateTime") or "")
    if len(ts) < 12:
        return None
    try:
        dt = datetime.strptime(ts[:14], "%Y%m%d%H%M%S").replace(tzinfo=KST)
    except ValueError:
        return None
    o = float(raw.get("openPrice") or 0)
    h = float(raw.get("highPrice") or 0)
    l = float(raw.get("lowPrice") or 0)
    c = float(raw.get("currentPrice") or 0)
    vol = int(raw.get("accumulatedTradingVolume") or 0)
    if not all(v > 0 for v in (o, h, l, c)):
        return None
    return MinuteBar(code=str(code).zfill(6), dt=dt, open=o, high=h, low=l, close=c, volume=vol)


def fetch_minute_bars(
    code: str,
    *,
    start_dt: datetime,
    end_dt: datetime,
    session: requests.Session | None = None,
) -> list[MinuteBar]:
    """지정 구간 1분봉 조회."""
    c6 = str(code).zfill(6)
    params = {
        "startDateTime": start_dt.strftime("%Y%m%d%H%M"),
        "endDateTime": end_dt.strftime("%Y%m%d%H%M"),
    }
    sess = session or requests.Session()
    resp = sess.get(
        NAVER_MINUTE_URL.format(code=c6),
        params=params,
        headers=DEFAULT_HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, list):
        return []
    out: list[MinuteBar] = []
    for row in payload:
        bar = _parse_bar(c6, row)
        if bar is not None:
            out.append(bar)
    return sorted(out, key=lambda b: b.dt)


def generate_mock_day_bars(
    code: str,
    *,
    base_price: float = 50_000.0,
    trade_date: datetime | None = None,
) -> list[MinuteBar]:
    """Mock 모드용 하루치 1분봉 (09:00~15:30, 391봉)."""
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
class NaverMinuteCrawler:
    """감시 종목 분봉 폴링 → Queue 적재."""

    watch_codes: list[str]
    bar_queue: Queue = field(default_factory=Queue)
    request_delay_sec: float = 0.15
    jitter_sec: float = 0.05
    max_codes: int = 30
    _session: requests.Session = field(default_factory=requests.Session, repr=False)
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
        delay = self.request_delay_sec + random.uniform(0, self.jitter_sec)
        time.sleep(delay)

    def poll_once(self, *, now: datetime | None = None) -> int:
        """전 종목 1회 폴링. 신규/갱신 봉 수 반환."""
        cur = now or datetime.now(KST)
        if cur.tzinfo is None:
            cur = cur.replace(tzinfo=KST)
        start = cur.replace(hour=9, minute=0, second=0, microsecond=0)
        end = cur.replace(second=0, microsecond=0)
        if end < start:
            return 0

        pushed = 0
        for code in self.watch_codes:
            try:
                bars = fetch_minute_bars(code, start_dt=start, end_dt=end, session=self._session)
            except Exception as exc:
                logger.warning("분봉 조회 실패 %s: %s", code, exc)
                self._sleep_between_requests()
                continue

            if bars:
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

    async def poll_loop_async(
        self,
        *,
        should_run: Callable[[], bool],
        on_tick: Callable[[datetime], None] | None = None,
    ) -> None:
        """매 분 정각+5초에 폴링 (asyncio)."""
        while should_run():
            now = datetime.now(KST)
            if on_tick:
                on_tick(now)
            if now.weekday() >= 5:
                await asyncio.sleep(60)
                continue
            self.poll_once(now=now)
            await asyncio.sleep(max(1.0, 60.0 - now.second + 5))


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

    def bars_until(self, code: str, minute_index: int) -> list[MinuteBar]:
        c6 = str(code).zfill(6)
        bars = self._all_bars.get(c6, [])
        return bars[: minute_index + 1]

    def step(self) -> bool:
        """다음 분 타임스텝. False면 종료."""
        if self._cursor >= self.max_minutes:
            return False
        for code, bars in self._all_bars.items():
            if self._cursor < len(bars):
                self.bar_queue.put(bars[self._cursor])
        self._cursor += 1
        return True

    async def stream_async(self, *, should_run: Callable[[], bool] | None = None) -> None:
        while self.step():
            if should_run and not should_run():
                break
            await asyncio.sleep(self.speed_sec)

    def current_sim_time(self) -> datetime | None:
        if self._cursor <= 0:
            return None
        any_code = next(iter(self._all_bars), None)
        if not any_code:
            return None
        bars = self._all_bars[any_code]
        idx = min(self._cursor - 1, len(bars) - 1)
        return bars[idx].dt
