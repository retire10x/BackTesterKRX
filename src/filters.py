"""
v3.95 주도주 눌림목 스캔·백테스트 필터 (장기 대세선·단기 모멘텀).

Pass 4: 종가 > MA60 AND 종가 > MA120 AND MA60 > MA120 (Perfect Trend Lock).
Pass 5(기본 ON): MA5 >= MA10.
Pass 2(v4.15): MA20 터치 회복 OR (MA20 위 + t-1 중심선) + v4.25 이격도5≤105%·20≤110%.
Pass 0(v4.00): 시총·당일 거래대금 유동성 게이트.
Pass 0(v4.40): 당일 거래량·거래대금 0(거래정지·락업) 원천 제거.
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
# v4.25 Pass2 이격도 과열 락 (근거 Excel·스캔 게이트 SSOT)
PULLBACK_DISPARITY5_LOCK_PCT = 105.0
PULLBACK_DISPARITY20_LOCK_PCT = 110.0


def resolve_pullback_universe_head(universe_limit: int) -> int | None:
    """0(ALL)=None(슬라이스 없음), 그 외 시총 상위 20~1000."""
    ul = int(universe_limit)
    if ul <= 0:
        return None
    return max(20, min(1000, ul))


def normalize_pullback_scan_market(raw: object) -> str:
    """스캔 파이프라인 시장 인자 — KOSPI / KOSDAQ / ALL."""
    m = str(raw or "KOSPI").strip().upper()
    if m == "ALL":
        return "ALL"
    if m not in ("KOSPI", "KOSDAQ"):
        return "KOSPI"
    return m


def pullback_scan_is_dual_market(market: str, universe_limit: int = 0) -> bool:
    """v4.10: 시장=ALL — 코스피·코스닥 통합 풀 (Top 유니버스와 분리)."""
    _ = universe_limit  # 하위 호환·명시적 무관
    return normalize_pullback_scan_market(market) == "ALL"


def pullback_bulk_markets_for_scan(market: str, universe_limit: int) -> tuple[str, ...]:
    """
    v4.10:
    - 시장=ALL → KOSPI+KOSDAQ (Top 설정과 무관)
    - 그 외 → 선택 시장 단일 (Top=ALL이면 해당 시장 전종목, cap=None)
    """
    _ = universe_limit
    m = normalize_pullback_scan_market(market)
    if m == "ALL":
        return ("KOSPI", "KOSDAQ")
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


def leader_pullback_center_defense(
    prev_high: float, prev_low: float, close_t: float
) -> bool:
    """Reference 봉 중심선 (고+저)/2 — t 종가가 위에 있는지."""
    if not all(np.isfinite(v) for v in (prev_high, prev_low, close_t)):
        return False
    return float(close_t) >= (float(prev_high) + float(prev_low)) / 2.0


def leader_pullback_pass2_ma20_or_center(
    *,
    low_t: float,
    close_t: float,
    ma20: float,
    prev_high: float,
    prev_low: float,
) -> bool:
    """
    v4.15 Pass 2 — MA20 터치 회복(핵심) OR (MA20 위 안착 + t-1 중심선 수호).

    cond_ma20_protect = low < MA20 & close >= MA20
    pass_2 = cond_ma20_protect | (close >= MA20 & close >= t-1 center)
    """
    if not all(
        np.isfinite(v) for v in (low_t, close_t, ma20, prev_high, prev_low)
    ):
        return False
    lt, ct, m20 = float(low_t), float(close_t), float(ma20)
    cond_ma20_protect = (lt < m20) and (ct >= m20)
    cond_center_protect = leader_pullback_center_defense(prev_high, prev_low, ct)
    return cond_ma20_protect or (ct >= m20 and cond_center_protect)


def pass_disparity_lock(
    close_t: float,
    ma5: float,
    ma20: float,
) -> bool:
    """v4.25: 5·20일 이격도 과열 락 — 초과 시 Pass2 FAIL."""
    try:
        c, m5, m20 = float(close_t), float(ma5), float(ma20)
    except (TypeError, ValueError):
        return False
    if not all(np.isfinite(v) and v > 0 for v in (c, m5, m20)):
        return False
    d5 = c / m5 * 100.0
    d20 = c / m20 * 100.0
    return (
        d5 <= float(PULLBACK_DISPARITY5_LOCK_PCT)
        and d20 <= float(PULLBACK_DISPARITY20_LOCK_PCT)
    )


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


def pass_active_trading_gate(
    volume_t0: float | None,
    trade_amount_krw: float | None,
) -> bool:
    """v4.40: 당일 거래량·거래대금 모두 0 초과(거래정지·액면분할 락업 등 제외)."""
    for v in (volume_t0, trade_amount_krw):
        if v is None:
            return False
        fv = float(v)
        if not (np.isfinite(fv) and fv > 0):
            return False
    return True


def log_pass0_v440_halt_drop(dropped_halt_count: int) -> None:
    print(
        f"[DEBUG] v4.40 유동성 스캔: 거래정지(Volume=0) 종목 {int(dropped_halt_count)}개 "
        "감지 및 즉시 제거 완료."
    )


def pass0_active_trading_mask(
    df_universe: pd.DataFrame,
    *,
    volume_col: str = "today_vol",
    trade_col: str = "_trade_krw",
) -> pd.Series:
    """벌크 유니버스 — 당일 거래량·거래대금 양수."""
    vol = pd.to_numeric(df_universe[volume_col], errors="coerce")
    trd = pd.to_numeric(df_universe[trade_col], errors="coerce")
    return vol.notna() & (vol > 0) & trd.notna() & (trd > 0)


def apply_pass0_liquidity_filter(
    df_universe: pd.DataFrame,
    *,
    min_market_cap_krw: float = 0.0,
    min_trading_value_krw: float = 0.0,
    volume_col: str = "today_vol",
    trade_col: str = "_trade_krw",
    mcap_col: str = "_mcap_krw",
    halted_codes: frozenset[str] | None = None,
    log_halt_drop: bool = True,
) -> pd.DataFrame:
    """
    v4.40 Pass 0: 시총·거래대금 하한 + 거래정지(Volume/Amount=0) + pykrx 정지 목록 차집합.
    """
    if df_universe is None or df_universe.empty:
        return df_universe

    n0 = int(len(df_universe))
    cond_active = pass0_active_trading_mask(
        df_universe, volume_col=volume_col, trade_col=trade_col
    )
    if log_halt_drop:
        log_pass0_v440_halt_drop(n0 - int(cond_active.sum()))

    min_cap = float(min_market_cap_krw)
    min_trd = float(min_trading_value_krw)
    cond_mcap = pd.Series(True, index=df_universe.index)
    if min_cap > 0:
        mc = pd.to_numeric(df_universe[mcap_col], errors="coerce")
        cond_mcap = mc.notna() & (mc >= min_cap)
    cond_trd = pd.Series(True, index=df_universe.index)
    if min_trd > 0:
        tr = pd.to_numeric(df_universe[trade_col], errors="coerce")
        cond_trd = tr.notna() & (tr >= min_trd)

    cond_halt = pd.Series(True, index=df_universe.index)
    if halted_codes:
        halted = {str(c).zfill(6) for c in halted_codes}
        code_ser = df_universe.index.astype(str).str.zfill(6)
        cond_halt = ~code_ser.isin(halted)

    pass0_mask = cond_active & cond_mcap & cond_trd & cond_halt
    return df_universe.loc[pass0_mask].copy()


def pass_liquidity_gate(
    market_cap_krw: float | None,
    trade_amount_krw: float | None,
    *,
    min_market_cap_krw: float,
    min_trade_amount_krw: float,
    volume_t0: float | None = None,
) -> bool:
    """v4.00 Pass 0: 최소 시총·당일 거래대금(원) + v4.40 활성 거래(Volume/Amount>0)."""
    if not pass_active_trading_gate(volume_t0, trade_amount_krw):
        return False
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
