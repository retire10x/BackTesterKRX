"""
v8.0.0 ORB(Opening Range Breakout) 순수 전략 로직.

일봉 v7 엔진과 분리 — 분봉/틱 입력을 전제로 백테스트·라이브가 공유한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum

import numpy as np
import pandas as pd

MIN_PRIOR_TRADING_VALUE_KRW = 50_000_000_000  # 500억
GAP_MIN_RATIO = 0.02
GAP_MAX_RATIO = 0.07
OPENING_RANGE_MINUTES = 5
ENTRY_WINDOW_START = time(9, 5)
ENTRY_WINDOW_END = time(9, 30)
TARGET_PROFIT_RATIO = 0.05
STOP_LOSS_RATIO = 0.02
TIME_STOP_HM = "14:50"
MARKET_OPEN_HM = "09:00"


class OrbExitReason(str, Enum):
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    TIME_STOP = "TIME_STOP"


@dataclass(frozen=True)
class OrbConfig:
    min_prior_trading_value_krw: float = MIN_PRIOR_TRADING_VALUE_KRW
    gap_min_ratio: float = GAP_MIN_RATIO
    gap_max_ratio: float = GAP_MAX_RATIO
    opening_range_minutes: int = OPENING_RANGE_MINUTES
    target_profit_ratio: float = TARGET_PROFIT_RATIO
    stop_loss_ratio: float = STOP_LOSS_RATIO
    entry_window_start: time = ENTRY_WINDOW_START
    entry_window_end: time = ENTRY_WINDOW_END
    time_stop_hm: str = TIME_STOP_HM


def gap_ratio(today_open: float, prior_close: float) -> float | None:
    if not np.isfinite(today_open) or not np.isfinite(prior_close) or prior_close <= 0:
        return None
    return (float(today_open) / float(prior_close)) - 1.0


def passes_premarket_universe(
    *,
    prior_trading_value_krw: float,
    today_open: float,
    prior_close: float,
    cfg: OrbConfig | None = None,
) -> bool:
    """08:50~09:00 프리마켓 — 전일 500억+ & 갭 +2%~+7%."""
    c = cfg or OrbConfig()
    if not np.isfinite(prior_trading_value_krw) or prior_trading_value_krw < c.min_prior_trading_value_krw:
        return False
    gr = gap_ratio(today_open, prior_close)
    if gr is None:
        return False
    return c.gap_min_ratio <= gr <= c.gap_max_ratio


def compute_opening_high(minute_bars: pd.DataFrame, *, cfg: OrbConfig | None = None) -> float | None:
    """
    09:00~09:05 첫 5분봉 구간 고가.

    minute_bars index: DatetimeIndex (KST), columns: open/high/low/close (소문자).
    """
    c = cfg or OrbConfig()
    if minute_bars is None or minute_bars.empty:
        return None
    work = minute_bars.copy()
    if not isinstance(work.index, pd.DatetimeIndex):
        work.index = pd.to_datetime(work.index)
    high_s = pd.to_numeric(work.get("high", work.get("High")), errors="coerce")
    if high_s.dropna().empty:
        return None
    session_date = work.index[0].date()
    range_end = pd.Timestamp.combine(session_date, time(9, c.opening_range_minutes)).tz_localize(
        work.index[0].tzinfo
    )
    range_start = pd.Timestamp.combine(session_date, time(9, 0)).tz_localize(work.index[0].tzinfo)
    slice_bars = work.loc[(work.index >= range_start) & (work.index < range_end)]
    if slice_bars.empty:
        return None
    peak = float(pd.to_numeric(slice_bars["high"] if "high" in slice_bars.columns else slice_bars["High"], errors="coerce").max())
    return peak if np.isfinite(peak) and peak > 0 else None


def _as_time(dt: datetime | pd.Timestamp) -> time:
    ts = pd.Timestamp(dt)
    return time(ts.hour, ts.minute, ts.second)


def in_entry_window(now: datetime | pd.Timestamp, cfg: OrbConfig | None = None) -> bool:
    c = cfg or OrbConfig()
    t = _as_time(now)
    return c.entry_window_start <= t <= c.entry_window_end


def should_enter_breakout(
    *,
    current_price: float,
    opening_high: float,
    now: datetime | pd.Timestamp,
    cfg: OrbConfig | None = None,
) -> bool:
    """09:05~09:30, 현재가가 Opening High 상향 돌파."""
    c = cfg or OrbConfig()
    if not in_entry_window(now, c):
        return False
    if not np.isfinite(current_price) or not np.isfinite(opening_high):
        return False
    if opening_high <= 0 or current_price <= 0:
        return False
    return current_price > opening_high


def evaluate_orb_exit(
    *,
    entry_price: float,
    current_price: float,
    now: datetime | pd.Timestamp,
    cfg: OrbConfig | None = None,
) -> OrbExitReason | None:
    """+5% 익절 / -2% 손절 / 14:50 타임스탑."""
    c = cfg or OrbConfig()
    if not np.isfinite(entry_price) or entry_price <= 0:
        return None
    if not np.isfinite(current_price) or current_price <= 0:
        return None

    pnl_ratio = (float(current_price) / float(entry_price)) - 1.0
    if pnl_ratio >= c.target_profit_ratio:
        return OrbExitReason.TAKE_PROFIT
    if pnl_ratio <= -c.stop_loss_ratio:
        return OrbExitReason.STOP_LOSS

    h, m = (int(x) for x in c.time_stop_hm.split(":"))
    if _as_time(now) >= time(h, m):
        return OrbExitReason.TIME_STOP
    return None
