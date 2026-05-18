"""
익봉 시가 체결 시뮬레이션. GUI 비의존.
"""
from __future__ import annotations

import math

import pandas as pd


def simulate_single(
    df: pd.DataFrame,
    start_date: str,
    initial: float,
    buy_cost: float,
    sell_cost: float,
):
    """봉 종가에서 신호 확정 → 다음 봉 시가 체결. 전액 매수/전액 매도.
    반환: (결과 DF, 체결 목록) 또는 None."""
    start_ts = pd.Timestamp(start_date)
    d = df.loc[df.index >= start_ts].copy()
    if d.empty or len(d) < 2:
        return None

    past = df.loc[df.index < start_ts]
    pending = int(past["Signal"].iloc[-1]) if len(past) else 0

    cash = float(initial)
    shares = 0
    position = 0
    equity = []
    trades: list[dict] = []

    for i in range(len(d)):
        o = d["Open"].iloc[i]
        cl = d["Close"].iloc[i]
        sig = int(d["Signal"].iloc[i])

        if pending == 1 and position == 0:
            if pd.notna(o) and o > 0 and cash > 0:
                sh = math.floor(cash / (o * (1 + buy_cost)))
                if sh > 0:
                    cash -= sh * o * (1 + buy_cost)
                    position = 1
                    shares = sh
                    trades.append(
                        {"date": d.index[i], "side": "BUY", "price": float(o)}
                    )
        elif pending == -1 and position == 1:
            if pd.notna(o) and o > 0 and shares > 0:
                cash += shares * o * (1 - sell_cost)
                trades.append(
                    {"date": d.index[i], "side": "SELL", "price": float(o)}
                )
                shares = 0
                position = 0

        eq = cash + shares * (cl if pd.notna(cl) else 0)
        equity.append(eq)
        pending = sig

    out = d.copy()
    out["Equity"] = equity
    return out, trades
