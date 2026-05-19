"""
익봉 시가 체결 시뮬레이션. GUI 비의존.
v4.0: 선택적 매수 진입 필터 — 120일선 선형회귀 기울기·돌파 강도·시간 버퍼.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

# 120일선 기울기: 최근 이 많은 봉의 MA120 값으로 OLS (X = 0..n-1)
MA120_SLOPE_LOOKBACK = 5


def _ols_slope_beta1(y: np.ndarray) -> float | None:
    """Ordinary least squares slope β₁ for X = 0..n-1, Y = y."""
    y = np.asarray(y, dtype=float)
    n = y.size
    if n < 2 or not np.all(np.isfinite(y)):
        return None
    x = np.arange(n, dtype=float)
    x_mean = float(x.mean())
    y_mean = float(y.mean())
    denom = float(np.sum((x - x_mean) ** 2))
    if denom <= 1e-18:
        return None
    return float(np.sum((x - x_mean) * (y - y_mean)) / denom)


def _volume_series(d: pd.DataFrame) -> pd.Series:
    if "Volume" not in d.columns:
        return pd.Series(np.nan, index=d.index)
    return pd.to_numeric(d["Volume"], errors="coerce")


def _pass_trend_slope_ma120(
    d: pd.DataFrame, sig_bar: int, threshold: float
) -> bool:
    """종가 > 당일 MA120 이고, 최근 MA120_SLOPE_LOOKBACK 봉 MA120의 OLS 기울기 >= threshold."""
    if sig_bar < MA120_SLOPE_LOOKBACK - 1 or sig_bar >= len(d):
        return False
    if "MA120" not in d.columns:
        return False
    cl = float(d["Close"].iloc[sig_bar])
    ma120 = float(d["MA120"].iloc[sig_bar])
    if not (np.isfinite(cl) and np.isfinite(ma120)) or cl <= ma120:
        return False
    ys = d["MA120"].iloc[
        sig_bar - (MA120_SLOPE_LOOKBACK - 1) : sig_bar + 1
    ].to_numpy(dtype=float)
    slope = _ols_slope_beta1(ys)
    if slope is None:
        return False
    return slope >= threshold


def _pass_breakout_strength(d: pd.DataFrame, sig_bar: int) -> bool:
    """거래량 > 직전 5봉 평균×1.5 또는 종가 > MA20×1.02."""
    if sig_bar < 5 or sig_bar >= len(d):
        return False
    vol = _volume_series(d)
    v_now = float(vol.iloc[sig_bar])
    prev = vol.iloc[sig_bar - 5 : sig_bar]
    avg_prev = float(prev.mean())
    cond_vol = np.isfinite(v_now) and np.isfinite(avg_prev) and avg_prev > 0
    cond_vol = cond_vol and (v_now > avg_prev * 1.5)

    ma20 = float(d["MA20"].iloc[sig_bar])
    cl = float(d["Close"].iloc[sig_bar])
    cond_px = (
        np.isfinite(ma20)
        and np.isfinite(cl)
        and ma20 > 0
        and (cl > ma20 * 1.02)
    )
    return bool(cond_vol or cond_px)


def _buy_filters_pass(
    d: pd.DataFrame,
    sig_bar: int,
    ef: dict[str, Any],
) -> bool:
    """활성화된 매수 필터를 모두 AND 통과."""
    if sig_bar < 0 or sig_bar >= len(d):
        return False
    if bool(ef.get("filter_trend_slope", False)):
        thr = float(ef.get("slope_threshold", 0.01))
        if not _pass_trend_slope_ma120(d, sig_bar, thr):
            return False
    if bool(ef.get("filter_breakout_strength", False)):
        if not _pass_breakout_strength(d, sig_bar):
            return False
    return True


def simulate_single(
    df: pd.DataFrame,
    start_date: str,
    initial: float,
    buy_cost: float,
    sell_cost: float,
    *,
    entry_filters: dict[str, Any] | None = None,
):
    """봉 종가에서 신호 확정 → 다음 봉 시가 체결. 전액 매수/전액 매도.

    entry_filters (선택): filter_trend_slope, slope_threshold, filter_breakout_strength,
    filter_time_buffer — 모두 False 기본.
    """
    start_ts = pd.Timestamp(start_date)
    d = df.loc[df.index >= start_ts].copy()
    if d.empty or len(d) < 2:
        return None

    ef = dict(entry_filters) if entry_filters else {}
    ftbuf = bool(ef.get("filter_time_buffer", False))

    past = df.loc[df.index < start_ts]
    pending = int(past["Signal"].iloc[-1]) if len(past) else 0

    cash = float(initial)
    shares = 0
    position = 0
    equity = []
    trades: list[dict] = []

    tb_anchor: int | None = None
    buf_exec_bar: int | None = None
    buf_sig_bar: int | None = None

    for i in range(len(d)):
        o = d["Open"].iloc[i]
        cl = d["Close"].iloc[i]
        sig = int(d["Signal"].iloc[i])

        # 시간 버퍼로 예약된 매수 (돌파일 이후 2봉 종가 안착 확인 뒤 다음 봉 시가 체결)
        if (
            ftbuf
            and buf_exec_bar is not None
            and i == buf_exec_bar
            and position == 0
        ):
            sb = buf_sig_bar if buf_sig_bar is not None else max(0, i - 1)
            if pd.notna(o) and o > 0 and cash > 0 and _buy_filters_pass(d, sb, ef):
                sh = math.floor(cash / (o * (1 + buy_cost)))
                if sh > 0:
                    cash -= sh * o * (1 + buy_cost)
                    position = 1
                    shares = sh
                    trades.append(
                        {"date": d.index[i], "side": "BUY", "price": float(o)}
                    )
            buf_exec_bar = None
            buf_sig_bar = None

        if pending == -1 and position == 1:
            if pd.notna(o) and o > 0 and shares > 0:
                cash += shares * o * (1 - sell_cost)
                trades.append(
                    {"date": d.index[i], "side": "SELL", "price": float(o)}
                )
                shares = 0
                position = 0

        # ftbuf 시에는 통상 pending 매수 대신 버퍼만 사용; 시뮬 첫 봉(i==0) 워밍업 pending==1 만 예외
        if pending == 1 and position == 0 and (not ftbuf or i == 0):
            sig_bar = (i - 1) if i > 0 else 0
            if (
                pd.notna(o)
                and o > 0
                and cash > 0
                and _buy_filters_pass(d, sig_bar, ef)
            ):
                sh = math.floor(cash / (o * (1 + buy_cost)))
                if sh > 0:
                    cash -= sh * o * (1 + buy_cost)
                    position = 1
                    shares = sh
                    trades.append(
                        {"date": d.index[i], "side": "BUY", "price": float(o)}
                    )

        eq = cash + shares * (cl if pd.notna(cl) else 0)
        equity.append(eq)

        # --- 시간 버퍼: 돌파일(tb_anchor) 이후 i+1, i+2 종가가 MA20 위 ---
        if tb_anchor is not None and "MA20" in d.columns:
            if i == tb_anchor + 1:
                m20 = float(d["MA20"].iloc[i])
                if not (np.isfinite(m20) and np.isfinite(cl) and cl > m20):
                    tb_anchor = None
            elif i == tb_anchor + 2:
                m20 = float(d["MA20"].iloc[i])
                if np.isfinite(m20) and np.isfinite(cl) and cl > m20:
                    buf_exec_bar = tb_anchor + 3
                    buf_sig_bar = tb_anchor
                tb_anchor = None

        if sig == -1:
            pending = -1
            tb_anchor = None
            buf_exec_bar = None
            buf_sig_bar = None
        elif sig == 1:
            if ftbuf:
                tb_anchor = i
                pending = 0
            else:
                pending = 1
                tb_anchor = None
        else:
            pending = 0

    out = d.copy()
    out["Equity"] = equity
    return out, trades
