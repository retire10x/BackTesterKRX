"""
이평 돌파 시그널. GUI 비의존.
"""
from __future__ import annotations

import pandas as pd


def add_signals(df: pd.DataFrame, ma_period: int) -> pd.DataFrame:
    df = df.copy()
    col_ma = f"MA{ma_period}"
    df[col_ma] = df["Close"].rolling(window=ma_period).mean()
    df["Prev_Close"] = df["Close"].shift(1)
    df[col_ma + "_prev"] = df[col_ma].shift(1)
    df["Signal"] = 0
    buy = (df["Prev_Close"] <= df[col_ma + "_prev"]) & (df["Close"] > df[col_ma])
    sell = (df["Prev_Close"] >= df[col_ma + "_prev"]) & (df["Close"] < df[col_ma])
    df.loc[buy, "Signal"] = 1
    df.loc[sell, "Signal"] = -1
    return df


def add_entry_filter_columns(df: pd.DataFrame) -> pd.DataFrame:
    """매수 진입 필터용 MA120·MA20 (선형회귀 기울기·안착 확인 등). GUI 비의존."""
    out = df.copy()
    out["MA120"] = out["Close"].rolling(window=120, min_periods=120).mean()
    out["MA20"] = out["Close"].rolling(window=20, min_periods=20).mean()
    return out
