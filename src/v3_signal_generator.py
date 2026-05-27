from __future__ import annotations

import numpy as np
import pandas as pd


def generate_v3_overnight_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    v3.0 오버나이트 스캘퍼 종가 진입 필터.

    [진입 조건 A] 당일 거래량 / 전일 거래량 >= 1.5
    [진입 조건 B] 당일 시가 대비 종가 상승률 >= 4.0%
                  위꼬리 비율 (고가-종가)/(고가-시가) <= 0.2

    조건 충족 시 당일 종가(Close) 매수 시그널(buy_signal=1).
    """
    if df is None:
        raise ValueError("df is None")

    required = {"Open", "High", "Low", "Close", "Volume"}
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"필수 컬럼 누락: {missing}")

    signal_df = df.copy()
    signal_df["buy_signal"] = 0

    prev_vol = pd.to_numeric(signal_df["Volume"].shift(1), errors="coerce")
    today_vol = pd.to_numeric(signal_df["Volume"], errors="coerce")
    vol_growth = np.where(prev_vol > 0, today_vol / prev_vol, 0.0)

    open_px = pd.to_numeric(signal_df["Open"], errors="coerce")
    close_px = pd.to_numeric(signal_df["Close"], errors="coerce")
    high_px = pd.to_numeric(signal_df["High"], errors="coerce")

    return_pct = np.where(open_px > 0, (close_px - open_px) / open_px * 100.0, 0.0)

    total_range = high_px - open_px
    tail_range = high_px - close_px
    tail_ratio = np.where(total_range > 0, tail_range / total_range, 1.0)

    entry = (vol_growth >= 1.5) & (return_pct >= 4.0) & (tail_ratio <= 0.2)
    signal_df.loc[entry, "buy_signal"] = 1

    return signal_df
