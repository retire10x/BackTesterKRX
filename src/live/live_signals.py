"""
v5.5.2 진입·청산 신호 — portfolio_manager_v5 와 동일 수학 (실시간 OHLCV DataFrame 입력).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.live.live_config import LiveStrategyConfig


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    col_map = {c.lower(): c for c in work.columns}
    rename = {}
    for std in ("open", "high", "low", "close", "volume"):
        for k, orig in col_map.items():
            if k == std or k.startswith(std):
                rename[orig] = std
                break
    if rename:
        work = work.rename(columns=rename)
    if "close" not in work.columns and "Close" in work.columns:
        work = work.rename(columns={"Close": "close", "Open": "open", "High": "high", "Low": "low", "Volume": "volume"})
    return work.sort_index()


def min_history_bars(strat: LiveStrategyConfig) -> int:
    need = strat.lookback_window + 1
    mf = strat.macro_filter
    need = max(need, mf.price_above_ma)
    for w in mf.ma_lines:
        need = max(need, w + 1)
    return need


def passes_macro_filter(close_s: pd.Series, today_close: float, strat: LiveStrategyConfig) -> bool:
    mf = strat.macro_filter
    for w in mf.ma_lines:
        ma_s = close_s.rolling(window=w).mean()
        ma_today = float(ma_s.iloc[-1])
        ma_yesterday = float(ma_s.iloc[-2])
        if not np.isfinite(ma_today) or not np.isfinite(ma_yesterday):
            return False
        if ma_today <= ma_yesterday:
            return False
    if mf.price_above_ma > 0:
        ma_floor = float(close_s.rolling(window=mf.price_above_ma).mean().iloc[-1])
        if not np.isfinite(ma_floor) or today_close <= ma_floor:
            return False
    return True


def is_ma_inflection_entry(ohlcv_df: pd.DataFrame, strat: LiveStrategyConfig) -> bool:
    """어제≤MA20 · 오늘>20영업일전종가 · 듀얼 MA 우상향 · 종가>MA120."""
    window = strat.lookback_window
    if len(ohlcv_df) < min_history_bars(strat):
        return False

    work = _normalize_ohlcv(ohlcv_df)
    close_s = pd.to_numeric(work["close"], errors="coerce")
    today_close = float(close_s.iloc[-1])
    if not np.isfinite(today_close) or today_close <= 0:
        return False

    past_close = float(close_s.iloc[-(window + 1)])
    if not np.isfinite(past_close):
        return False

    ma_s = close_s.rolling(window=window).mean()
    yesterday_close = float(close_s.iloc[-2])
    yesterday_ma = float(ma_s.iloc[-2])
    if not np.isfinite(yesterday_close) or not np.isfinite(yesterday_ma):
        return False

    if not (yesterday_close <= yesterday_ma and today_close > past_close):
        return False
    return passes_macro_filter(close_s, today_close, strat)


def evaluate_hit_and_run_exit(
    *,
    entry_price: float,
    high: float,
    low: float,
    close: float,
    hold_days: int,
    strat: LiveStrategyConfig,
) -> tuple[float, str] | None:
    """손절 → 익절 → 타임스탑 (장중 H/L 우선)."""
    target_px = entry_price * (1.0 + strat.target_profit_ratio)
    stop_px = entry_price * (1.0 - strat.stop_loss_ratio)

    if low <= stop_px:
        return stop_px, "STOP_LOSS"
    if high >= target_px:
        return target_px, "TAKE_PROFIT"
    if hold_days >= strat.max_hold_days:
        return close, "TIME_STOP"
    return None
