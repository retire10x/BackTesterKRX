"""
v9.0.0 대형주 피보나치 스윙 (Risk-Free Swing) — 순수 신호 로직.

15:20 일봉 종가 확정 기준. 분봉/틱 불필요.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

MA_SHORT = 60
MA_LONG = 200
GC_MIN_BARS = 63   # 약 3개월
GC_MAX_BARS = 126  # 약 6개월
FIB_RATIOS = (0.382, 0.500, 0.618)
TRANCHE_AMOUNTS_KRW = (125_000, 125_000, 250_000)
SLOT_BUDGET_KRW = 500_000
FIB_TOLERANCE = 0.015  # 레vel 대비 ±1.5%
MIN_MCAP_KRW = 500_000_000_000       # 5,000억
PREFERRED_MCAP_KRW = 1_000_000_000_000  # 1조

KOSPI200_INDEX = "1028"
KOSDAQ150_INDEX = "2203"


@dataclass(frozen=True)
class FibSwingSetup:
    gc_date: pd.Timestamp
    swing_high: float
    swing_low: float
    fib_prices: tuple[float, float, float]


@dataclass(frozen=True)
class FibTrancheSignal:
    tranche_index: int  # 0, 1, 2
    fib_ratio: float
    fib_price: float
    amount_krw: float


def find_golden_cross_index(
    close: pd.Series,
    *,
    short: int = MA_SHORT,
    long: int = MA_LONG,
    min_bars_ago: int = GC_MIN_BARS,
    max_bars_ago: int = GC_MAX_BARS,
) -> int | None:
    """최근 min~max 영업일 내 MA60×MA200 골든크로스 바 인덱스(iloc). 없으면 None."""
    if len(close) < long + 2:
        return None
    ma_s = close.rolling(window=short).mean()
    ma_l = close.rolling(window=long).mean()
    cross = (ma_s > ma_l) & (ma_s.shift(1) <= ma_l.shift(1))
    idxs = np.flatnonzero(cross.fillna(False).to_numpy())
    if len(idxs) == 0:
        return None
    last = int(idxs[-1])
    bars_ago = len(close) - 1 - last
    if bars_ago < min_bars_ago or bars_ago > max_bars_ago:
        return None
    return last


def compute_fib_setup(
    ohlcv: pd.DataFrame,
    gc_idx: int,
) -> FibSwingSetup | None:
    """GC 이후 스윙 고/저 기준 피보나치 되돌림 타점 계산."""
    if gc_idx < 0 or gc_idx >= len(ohlcv):
        return None
    high_s = pd.to_numeric(ohlcv["high"], errors="coerce")
    low_s = pd.to_numeric(ohlcv["low"], errors="coerce")
    seg_high = high_s.iloc[gc_idx:]
    seg_low = low_s.iloc[gc_idx:]
    if seg_high.dropna().empty or seg_low.dropna().empty:
        return None

    rel_peak = int(seg_high.to_numpy().argmax())
    peak_idx = gc_idx + rel_peak
    swing_high = float(high_s.iloc[peak_idx])
    swing_low = float(low_s.iloc[gc_idx : peak_idx + 1].min())
    if not np.isfinite(swing_high) or not np.isfinite(swing_low):
        return None
    if swing_high <= swing_low:
        return None

    span = swing_high - swing_low
    fib_prices = tuple(swing_high - span * r for r in FIB_RATIOS)
    gc_ts = pd.Timestamp(ohlcv.index[gc_idx]).normalize()
    return FibSwingSetup(
        gc_date=gc_ts,
        swing_high=swing_high,
        swing_low=swing_low,
        fib_prices=fib_prices,  # type: ignore[arg-type]
    )


def _near_fib_level(close: float, fib_price: float, tolerance: float = FIB_TOLERANCE) -> bool:
    if not np.isfinite(close) or not np.isfinite(fib_price) or fib_price <= 0:
        return False
    return abs(close - fib_price) / fib_price <= tolerance


def detect_tranche_signal(
    close: float,
    tranches_filled: int,
    setup: FibSwingSetup,
) -> FibTrancheSignal | None:
    """분할 매수 격발 여부. tranches_filled=0이면 1차(0.382)만."""
    if tranches_filled < 0 or tranches_filled >= len(FIB_RATIOS):
        return None
    ratio = FIB_RATIOS[tranches_filled]
    fib_price = setup.fib_prices[tranches_filled]
    if not _near_fib_level(close, fib_price):
        return None
    return FibTrancheSignal(
        tranche_index=tranches_filled,
        fib_ratio=ratio,
        fib_price=fib_price,
        amount_krw=float(TRANCHE_AMOUNTS_KRW[tranches_filled]),
    )


def evaluate_exit(
    *,
    close: float,
    high: float,
    low: float,
    avg_entry: float,
    swing_high: float,
    swing_low: float,
    tranches_filled: int,
    partial_tp_done: bool,
    risk_free: bool,
    breakeven_stop: float,
) -> tuple[str, float] | None:
    """
    청산 판정. (exit_type, exit_price) 또는 None.
    우선순위: 부분익절 → 본전손절 → 전량손절
    """
    if not np.isfinite(close) or close <= 0:
        return None

    if not partial_tp_done and tranches_filled >= 1 and np.isfinite(swing_high):
        if close > swing_high:
            return ("PARTIAL_TP_50", close)

    if risk_free and np.isfinite(breakeven_stop) and breakeven_stop > 0:
        if low <= breakeven_stop:
            return ("RISK_FREE_BREAKEVEN", breakeven_stop)

    if tranches_filled >= len(FIB_RATIOS) and np.isfinite(avg_entry) and np.isfinite(swing_low):
        if close < swing_low or low < swing_low:
            return ("STOP_SWING_LOW", min(close, swing_low))
        risk = avg_entry - swing_low
        if risk > 0:
            stop_1_2 = avg_entry - 2.0 * risk
            if close <= stop_1_2 or low <= stop_1_2:
                return ("STOP_RR_1_2", min(close, stop_1_2))

    return None


def build_fib_setup_from_history(ohlcv: pd.DataFrame) -> FibSwingSetup | None:
    """일봉 OHLCV에서 GC + 피보나치 셋업 일괄 산출."""
    if ohlcv is None or ohlcv.empty:
        return None
    close_s = pd.to_numeric(ohlcv["close"], errors="coerce")
    gc_idx = find_golden_cross_index(close_s)
    if gc_idx is None:
        return None
    return compute_fib_setup(ohlcv, gc_idx)


def load_index_members(as_of_date: str) -> frozenset[str]:
    """KOSPI200·KOSDAQ150 편입 종목 (pykrx). 실패 시 빈 집합."""
    try:
        from pykrx import stock as pykrx_stock  # type: ignore
    except Exception:
        return frozenset()

    ymd = pd.Timestamp(str(as_of_date).strip()[:10]).strftime("%Y%m%d")
    codes: set[str] = set()
    for ticker in (KOSPI200_INDEX, KOSDAQ150_INDEX):
        try:
            df = pykrx_stock.get_index_portfolio_deposit_file(ymd, ticker)
            if df is None or getattr(df, "empty", True):
                continue
            col = "종목코드" if "종목코드" in df.columns else df.columns[0]
            for raw in df[col].astype(str):
                codes.add(str(raw).zfill(6))
        except Exception:
            continue
    return frozenset(codes)
