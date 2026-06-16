"""v10.0 Momentum (High Tight Flag) 순수 로직 유닛 테스트."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.engine.high_tight_flag_strategy import (
    detect_momentum_entry,
    evaluate_momentum_exit,
    made_52w_high_within,
)


def _uptrend_with_new_high(n: int = 280) -> pd.DataFrame:
    idx = pd.bdate_range("2023-01-01", periods=n)
    close = pd.Series(np.linspace(50_000, 90_000, n), index=idx)
    high = close * 1.005
    low = close * 0.995
    high.iloc[-3] = close.iloc[-3] * 1.08
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close, "volume": 1e9},
        index=idx,
    )


def test_made_52w_high_within():
    df = _uptrend_with_new_high()
    assert made_52w_high_within(df["high"]) is True


def test_momentum_entry_near_ma20():
    df = _uptrend_with_new_high()
    ma20 = df["close"].rolling(20).mean().iloc[-1]
    df.iloc[-1, df.columns.get_loc("close")] = ma20
    ok, msg = detect_momentum_entry(df)
    assert ok, msg


def test_momentum_partial_then_ma10():
    partial = evaluate_momentum_exit(
        close=101_000,
        high=101_500,
        low=100_000,
        avg_entry=95_000,
        prior_high=100_000,
        partial_tp_done=False,
        risk_free=False,
        breakeven_stop=0,
        ma10=98_000,
    )
    assert partial == ("MOMENTUM_PARTIAL_TP_50", 101_000, 0.5)

    ma_break = evaluate_momentum_exit(
        close=97_000,
        high=98_000,
        low=96_500,
        avg_entry=95_000,
        prior_high=100_000,
        partial_tp_done=True,
        risk_free=True,
        breakeven_stop=95_000,
        ma10=98_000,
    )
    assert ma_break == ("MOMENTUM_MA10_BREAK", 97_000, 1.0)


def run_unit_tests() -> None:
    test_made_52w_high_within()
    test_momentum_entry_near_ma20()
    test_momentum_partial_then_ma10()
    print("  high_tight_flag_strategy unit tests OK", flush=True)


if __name__ == "__main__":
    run_unit_tests()
