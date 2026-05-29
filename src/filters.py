"""
v3.85 주도주 눌림목 스캔·백테스트 필터 (장기 대세선·단기 모멘텀).

Pass 4: 종가 > MA60 (v3.50 MA120 에서 완화).
Pass 5(선택): MA5 >= MA10.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# v3.85 장기 대세선 — 60일 단순이평
PULLBACK_LONG_MA_DAYS = 60
PULLBACK_MIN_OHLCV_BARS = PULLBACK_LONG_MA_DAYS
PULLBACK_SCAN_HISTORY_BDAY = PULLBACK_LONG_MA_DAYS - 1  # t0 포함 60영업일
UNIVERSE_LIMIT_ALL = 0
PULLBACK_DUAL_MARKET_LABEL = "KOSPI+KOSDAQ"


def resolve_pullback_universe_head(universe_limit: int) -> int | None:
    """0(ALL)=None(슬라이스 없음), 그 외 시총 상위 20~1000."""
    ul = int(universe_limit)
    if ul <= 0:
        return None
    return max(20, min(1000, ul))


def pullback_scan_is_dual_market(universe_limit: int) -> bool:
    """Top ALL(0) — 코스피·코스닥 통합 풀."""
    return int(universe_limit) <= 0


def pullback_bulk_markets_for_scan(market: str, universe_limit: int) -> tuple[str, ...]:
    """
    v3.86: ALL(0)이면 GUI 시장 콤보와 무관하게 KOSPI+KOSDAQ 병렬 유니버스.
    그 외에는 단일 시장(KOSPI 또는 KOSDAQ).
    """
    if pullback_scan_is_dual_market(universe_limit):
        return ("KOSPI", "KOSDAQ")
    m = str(market or "KOSPI").strip().upper()
    if m not in ("KOSPI", "KOSDAQ"):
        m = "KOSPI"
    return (m,)


def _close_array(close: pd.Series | np.ndarray) -> np.ndarray:
    if isinstance(close, pd.Series):
        return pd.to_numeric(close, errors="coerce").to_numpy(dtype=float)
    return np.asarray(close, dtype=float)


def pass_long_trend_close_above_ma(
    close: pd.Series | np.ndarray,
    *,
    at_index: int | None = None,
    ma_days: int = PULLBACK_LONG_MA_DAYS,
) -> bool:
    """v3.85 Pass 4: t 종가 > MA60 (기본)."""
    arr = _close_array(close)
    n = len(arr)
    need = int(ma_days)
    if n < need:
        return False
    i = int(at_index) if at_index is not None else n - 1
    if i < need - 1 or i >= n:
        return False
    close_t = float(arr[i])
    ma_long = float(np.nanmean(arr[i - need + 1 : i + 1]))
    if not (np.isfinite(close_t) and np.isfinite(ma_long)):
        return False
    return close_t > ma_long


def pass_short_momentum_ma5_ge_ma10(
    close: pd.Series | np.ndarray,
    *,
    at_index: int | None = None,
) -> bool:
    """Pass 5: MA5 >= MA10."""
    arr = _close_array(close)
    n = len(arr)
    if n < 10:
        return False
    i = int(at_index) if at_index is not None else n - 1
    if i < 9 or i >= n:
        return False
    ma5 = float(np.nanmean(arr[i - 4 : i + 1]))
    ma10 = float(np.nanmean(arr[i - 9 : i + 1]))
    if not all(np.isfinite(v) for v in (ma5, ma10)):
        return False
    return ma5 >= ma10


def kim_straight_trend_pass(
    close: pd.Series | np.ndarray,
    *,
    at_index: int | None = None,
) -> tuple[bool, bool, bool]:
    """
    김직선 정배열 추세 필터 (v3.85).
    반환: (통과, 장기 MA60 통과, 단기 MA5≥MA10 통과).
    """
    long_ok = pass_long_trend_close_above_ma(close, at_index=at_index)
    short_ok = pass_short_momentum_ma5_ge_ma10(close, at_index=at_index)
    return bool(long_ok and short_ok), bool(long_ok), bool(short_ok)
