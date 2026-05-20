"""
이평 돌파 시그널. GUI 비의존.
v4.6: 전략 dict 와 동기화해 골든 매수·데드 매도 발생 자체를 끌 수 있음.
"""
from __future__ import annotations

import pandas as pd


def add_signals(
    df: pd.DataFrame,
    ma_period: int,
    *,
    golden_buy_enabled: bool = True,
    dead_cross_sell_enabled: bool = True,
) -> pd.DataFrame:
    """이평 돌파 시그널. v4.6: 기본 골던/데드 스위치로 신호 발생 자체를 끈다."""
    df = df.copy()
    col_ma = f"MA{ma_period}"
    df[col_ma] = df["Close"].rolling(window=ma_period).mean()
    df["Prev_Close"] = df["Close"].shift(1)
    df[col_ma + "_prev"] = df[col_ma].shift(1)
    df["Signal"] = 0
    buy_cond = (df["Prev_Close"] <= df[col_ma + "_prev"]) & (df["Close"] > df[col_ma])
    sell_cond = (df["Prev_Close"] >= df[col_ma + "_prev"]) & (df["Close"] < df[col_ma])
    if golden_buy_enabled:
        df.loc[buy_cond, "Signal"] = 1
    if dead_cross_sell_enabled:
        df.loc[sell_cond, "Signal"] = -1
    return df


def add_entry_filter_columns(df: pd.DataFrame) -> pd.DataFrame:
    """매수 진입 필터용 MA120·MA20 (선형회귀 기울기·안착 확인 등). GUI 비의존."""
    out = df.copy()
    out["MA120"] = out["Close"].rolling(window=120, min_periods=120).mean()
    out["MA20"] = out["Close"].rolling(window=20, min_periods=20).mean()
    return out
