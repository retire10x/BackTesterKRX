from __future__ import annotations

import pandas as pd


def execute_v2_backtest(df: pd.DataFrame, sell_cost: float = 0.0020) -> pd.DataFrame:
    """
    v2.0 인트라데이 갭 스캘퍼 매매 체결 엔진입니다.

    당일 시가(Open) 매수 → 당일 종가(Close) 매도 청산을 시뮬레이션합니다.
    장중 High/Low를 사용하지 않아 봉 내부 시간 순서에 따른 look-ahead 왜곡이 없습니다.

    [수익률]
    - buy_signal == 1: trade_return = (Close / Open) * (1 - sell_cost) - 1
    - buy_signal == 0: trade_return = 0.0
    """
    if df is None:
        raise ValueError("df is None")

    required = {"Open", "Close", "buy_signal"}
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"필수 컬럼 누락: {missing}")

    engine_df = df.copy()
    engine_df["trade_return"] = 0.0

    signal_mask = engine_df["buy_signal"] == 1
    if not signal_mask.any():
        return engine_df

    open_px = pd.to_numeric(engine_df.loc[signal_mask, "Open"], errors="coerce")
    close_px = pd.to_numeric(engine_df.loc[signal_mask, "Close"], errors="coerce")
    valid = (open_px > 0) & close_px.notna() & open_px.notna()

    if valid.any():
        engine_df.loc[signal_mask, "trade_return"] = 0.0
        idx = open_px.index[valid]
        engine_df.loc[idx, "trade_return"] = (
            (close_px.loc[idx] / open_px.loc[idx]) * (1.0 - sell_cost) - 1.0
        )

    return engine_df
