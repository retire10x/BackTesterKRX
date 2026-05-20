"""
익봉 시가 체결 시뮬레이션. GUI 비의존.
v4.0: 선택적 매수 진입 필터 — 120일선 선형회귀 기울기·돌파 강도·시간 버퍼.
v4.4: 수익률 구간별 가변 고점 대비 낙폭 매도(트레일링); 종가 확정 후 다음 봉 시가 청산·`reason='trail_stop'` 타점 차트 색 구분).
v4.6: 매도 분기에서 가변 낙폭(우선)·데드 크로스(옵션) OR; `dead_cross_sell_enabled` 가 False 면 신호 매도 실행 안 함(strategy 와 결합).
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


def _pass_time_buffer(d: pd.DataFrame, sig_bar: int) -> bool:
    """골든크로스 신호일(sig_bar) 이후 +1, +2 영업일 종가가 모두 MA20 위에 안착했는지 확인."""
    if "MA20" not in d.columns:
        return False
    if sig_bar + 2 >= len(d):
        return False
    
    cl1 = float(d["Close"].iloc[sig_bar + 1])
    ma20_1 = float(d["MA20"].iloc[sig_bar + 1])
    cl2 = float(d["Close"].iloc[sig_bar + 2])
    ma20_2 = float(d["MA20"].iloc[sig_bar + 2])
    
    return (
        np.isfinite(cl1) and np.isfinite(ma20_1) and cl1 > ma20_1
        and np.isfinite(cl2) and np.isfinite(ma20_2) and cl2 > ma20_2
    )


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
    if bool(ef.get("filter_time_buffer", False)):
        if not _pass_time_buffer(d, sig_bar):
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
    trailing_stop: dict[str, Any] | None = None,
    dead_cross_sell_enabled: bool = True,
):
    """봉 종가에서 신호 확정 → 다음 봉 시가 체결. 전액 매수/전액 매도.

    entry_filters (선택): filter_trend_slope, slope_threshold, filter_breakout_strength,
    filter_time_buffer — 모두 False 기본.

    trailing_stop (v4.4, 선택): enabled, trailing_reference_pct(기준 피크 수익률 %),
    trailing_drop_below_pct(미만 구간 적용 고점 대비 하락 %),
    trailing_drop_above_pct(도달 후 적용 고점 대비 하락 %) — 활성 시 보유 중
    매수 체결가 대비 장중 최고가 워터마크 기준 피크 수익률로 분기한 뒤 종가 확정 분기별
    임계로 트레일 청산(다음 봉 시가 체결, trade reason ``trail_stop``).

    dead_cross_sell_enabled (v4.6): False 이면 `pending==-1`(데드 크로스) 시가 매도 실행을 건너뜁니다.
    가변 낙폭 매도만 켠 경우에는 이 플래그를 끌고 전략에서도 매도 신호를 막거나, 신호 매도 없이 트레일만 사용 가능합니다.
    """
    start_ts = pd.Timestamp(start_date)
    d = df.loc[df.index >= start_ts].copy()
    if d.empty or len(d) < 2:
        return None

    ef = dict(entry_filters) if entry_filters else {}
    ftbuf = bool(ef.get("filter_time_buffer", False))

    ts = dict(trailing_stop) if trailing_stop else {}
    ts_en = bool(ts.get("enabled", False))
    ref_pct = float(ts.get("trailing_reference_pct", 10.0))
    drop_below = float(ts.get("trailing_drop_below_pct", 3.0))
    drop_above = float(ts.get("trailing_drop_above_pct", 5.0))

    past = df.loc[df.index < start_ts]
    pending = int(past["Signal"].iloc[-1]) if len(past) else 0

    cash = float(initial)
    shares = 0
    position = 0
    equity = []
    trades: list[dict] = []

    buf_exec_bar: int | None = None
    buf_sig_bar: int | None = None

    trail_buy_px = 0.0
    trail_max_high = 0.0
    trail_exec_next = False

    def _sell_at_open_ma_cross() -> None:
        """데드크로스 등 신호 매도(다음 봉 시가)."""
        nonlocal cash, shares, position, trail_buy_px, trail_max_high, trail_exec_next
        if pd.notna(o) and o > 0 and shares > 0:
            cash += shares * o * (1 - sell_cost)
            trades.append(
                {"date": d.index[i], "side": "SELL", "price": float(o)}
            )
        shares = 0
        position = 0
        trail_buy_px = 0.0
        trail_max_high = 0.0
        trail_exec_next = False

    def _sell_at_open_trail_stop() -> None:
        """v4.4 가변 낙폭 매도 확정 행."""
        nonlocal cash, shares, position, trail_buy_px, trail_max_high, trail_exec_next
        if pd.notna(o) and o > 0 and shares > 0:
            cash += shares * o * (1 - sell_cost)
            trades.append(
                {
                    "date": d.index[i],
                    "side": "SELL",
                    "price": float(o),
                    "reason": "trail_stop",
                }
            )
        shares = 0
        position = 0
        trail_buy_px = 0.0
        trail_max_high = 0.0
        trail_exec_next = False

    def _buy_at_open(price_f: float) -> None:
        nonlocal cash, shares, position, trail_buy_px, trail_max_high, trail_exec_next
        if not (pd.notna(price_f) and price_f > 0 and cash > 0):
            return
        sh_qty = math.floor(cash / (price_f * (1 + buy_cost)))
        if sh_qty <= 0:
            return
        cash -= sh_qty * price_f * (1 + buy_cost)
        position = 1
        shares = sh_qty
        trail_buy_px = float(price_f)
        hip = float(d["High"].iloc[i])
        trail_max_high = max(trail_buy_px, hip) if np.isfinite(hip) else trail_buy_px
        trail_exec_next = False
        trades.append(
            {"date": d.index[i], "side": "BUY", "price": float(price_f)}
        )

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
                _buy_at_open(float(o))
            buf_exec_bar = None
            buf_sig_bar = None

        if trail_exec_next and position == 1:
            _sell_at_open_trail_stop()

        elif pending == -1 and position == 1 and dead_cross_sell_enabled:
            _sell_at_open_ma_cross()

        # ftbuf 시에는 통상 pending 매수 대신 버퍼만 사용; 시뮬 첫 봉(i==0) 워밍업 pending==1 만 예외
        if pending == 1 and position == 0 and (not ftbuf or i == 0):
            sig_bar = (i - 1) if i > 0 else 0
            if pd.notna(o) and o > 0 and cash > 0 and _buy_filters_pass(
                d, sig_bar, ef
            ):
                _buy_at_open(float(o))

        eq = cash + shares * (cl if pd.notna(cl) else 0)
        equity.append(eq)

        # --- v4.4 가변 낙폭: 종가 확정 분기별 트레일(다음 봉 시가 청산) ---
        if (
            ts_en
            and position == 1
            and shares > 0
            and trail_buy_px > 0
            and np.isfinite(trail_max_high)
        ):
            hp = float(d["High"].iloc[i])
            if np.isfinite(hp):
                trail_max_high = max(trail_max_high, hp)
            peak_ret_pct = (
                (trail_max_high - trail_buy_px) / trail_buy_px * 100.0
                if trail_buy_px > 0
                else 0.0
            )
            use_drop_pct = drop_above if peak_ret_pct >= ref_pct else drop_below
            thresh_px = trail_max_high * (1.0 - use_drop_pct / 100.0)
            if np.isfinite(cl) and np.isfinite(thresh_px) and cl < thresh_px:
                trail_exec_next = True

        if sig == -1:
            pending = -1
            buf_exec_bar = None
            buf_sig_bar = None
        elif sig == 1:
            if ftbuf:
                buf_exec_bar = i + 3
                buf_sig_bar = i
                pending = 0
            else:
                pending = 1
        else:
            pending = 0

    out = d.copy()
    out["Equity"] = equity
    return out, trades
