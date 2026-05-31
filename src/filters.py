"""
v3.95 주도주 눌림목 스캔·백테스트 필터 (장기 대세선·단기 모멘텀).

Pass 4: 종가 > MA60 AND 종가 > MA120 AND MA60 > MA120 (Perfect Trend Lock).
Pass 5(기본 ON): MA5 >= MA10.
Pass 0(v4.00): 시총·당일 거래대금 유동성 게이트.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# v3.90 장기 대세선 — 60·120일 단순이평 듀얼 AND
PULLBACK_LONG_MA_DAYS = 60
PULLBACK_VERY_LONG_MA_DAYS = 120
PULLBACK_MIN_OHLCV_BARS = PULLBACK_VERY_LONG_MA_DAYS
PULLBACK_SCAN_HISTORY_BDAY = PULLBACK_VERY_LONG_MA_DAYS - 1  # t0 포함 120영업일
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
    """t 종가 > MA{N} (기본 N=60)."""
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


def pass_dual_long_trend_ma60_and_ma120(
    close: pd.Series | np.ndarray,
    *,
    at_index: int | None = None,
) -> bool:
    """v3.95 Pass 4: 종가>MA60·MA120 AND MA60>MA120 (정배열 배열성)."""
    arr = _close_array(close)
    n = len(arr)
    need120 = int(PULLBACK_VERY_LONG_MA_DAYS)
    if n < need120:
        return False
    i = int(at_index) if at_index is not None else n - 1
    if i < need120 - 1 or i >= n:
        return False
    close_t = float(arr[i])
    ma60 = float(np.nanmean(arr[i - PULLBACK_LONG_MA_DAYS + 1 : i + 1]))
    ma120 = float(np.nanmean(arr[i - need120 + 1 : i + 1]))
    if not all(np.isfinite(v) for v in (close_t, ma60, ma120)):
        return False
    return close_t > ma60 and close_t > ma120 and ma60 > ma120


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
    김직선 정배열 추세 필터 (v3.95).
    반환: (통과, 장기 Perfect Trend 통과, 단기 MA5≥MA10 통과).
    """
    long_ok = pass_dual_long_trend_ma60_and_ma120(close, at_index=at_index)
    short_ok = pass_short_momentum_ma5_ge_ma10(close, at_index=at_index)
    return bool(long_ok and short_ok), bool(long_ok), bool(short_ok)


def pass_liquidity_gate(
    market_cap_krw: float | None,
    trade_amount_krw: float | None,
    *,
    min_market_cap_krw: float,
    min_trade_amount_krw: float,
) -> bool:
    """v4.00 Pass 0: 최소 시총·당일 거래대금(원) 동시 충족."""
    min_cap = float(min_market_cap_krw)
    min_trd = float(min_trade_amount_krw)
    if min_cap <= 0 and min_trd <= 0:
        return True
    if min_cap > 0:
        if market_cap_krw is None:
            return False
        mc = float(market_cap_krw)
        if not (np.isfinite(mc) and mc >= min_cap):
            return False
    if min_trd > 0:
        if trade_amount_krw is None:
            return False
        tr = float(trade_amount_krw)
        if not (np.isfinite(tr) and tr >= min_trd):
            return False
    return True
