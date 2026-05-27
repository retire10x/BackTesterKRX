from __future__ import annotations

import pandas as pd

# harness.md 보수 기준 고정 (v3.0 인수 조건)
BUY_COST = 0.00015
SELL_COST = 0.0020


def execute_v3_overnight_backtest(df: pd.DataFrame) -> pd.DataFrame:
    """
    v3.0 오버나이트 체결: 당일 종가 매수 → 익일 시가 매도.

    - real_buy  = Close * (1 + BUY_COST)
    - real_sell = next Open * (1 - SELL_COST)
    - trade_return = real_sell / real_buy - 1  (buy_signal == 1, 익일 봉 존재 시)
    """
    if df is None:
        raise ValueError("df is None")

    required = {"Open", "Close", "buy_signal"}
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"필수 컬럼 누락: {missing}")

    engine_df = df.copy()
    engine_df["trade_return"] = float("nan")

    signal_mask = engine_df["buy_signal"] == 1
    if not signal_mask.any():
        return engine_df

    close_px = pd.to_numeric(engine_df["Close"], errors="coerce")
    next_open = pd.to_numeric(engine_df["Open"].shift(-1), errors="coerce")

    real_buy = close_px * (1.0 + BUY_COST)
    real_sell = next_open * (1.0 - SELL_COST)

    valid = (
        signal_mask
        & (real_buy > 0)
        & real_buy.notna()
        & next_open.notna()
        & (next_open > 0)
    )

    if valid.any():
        engine_df.loc[valid, "trade_return"] = (real_sell.loc[valid] / real_buy.loc[valid]) - 1.0

    return engine_df
