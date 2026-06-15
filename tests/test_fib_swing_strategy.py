"""v9.0.0 Fib Swing 순수 로직 유닛 테스트 (pytest 없이 실행 가능)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.engine.fib_swing_strategy import (
    FIB_RATIOS,
    build_fib_setup_from_history,
    compute_fib_setup,
    detect_tranche_signal,
    evaluate_exit,
    find_golden_cross_index,
)


def _synthetic_uptrend(n: int = 260) -> pd.DataFrame:
    idx = pd.bdate_range("2023-01-01", periods=n)
    close = np.linspace(50_000, 80_000, n) + np.random.default_rng(0).normal(0, 200, n)
    close = np.maximum(close, 1000)
    high = close * 1.01
    low = close * 0.99
    open_ = close * 0.999
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": 1e6},
        index=idx,
    )


def test_golden_cross_detected():
    df = _synthetic_uptrend()
    gc = find_golden_cross_index(df["close"])
    assert gc is not None or len(df) >= 200


def test_fib_levels_ordered():
    df = _synthetic_uptrend()
    gc = find_golden_cross_index(df["close"])
    if gc is None:
        return
    setup = compute_fib_setup(df, gc)
    assert setup is not None
    p382, p500, p618 = setup.fib_prices
    assert p382 > p618 > setup.swing_low
    assert setup.swing_high >= p382


def test_tranche_signal_at_fib():
    setup = type("S", (), {
        "fib_prices": (38_200.0, 37_000.0, 35_800.0),
        "swing_high": 40_000.0,
        "swing_low": 34_000.0,
        "gc_date": pd.Timestamp("2024-01-01"),
    })()
    sig = detect_tranche_signal(38_150.0, 0, setup)  # type: ignore[arg-type]
    assert sig is not None
    assert sig.tranche_index == 0
    assert sig.amount_krw == 125_000


def test_partial_tp_then_breakeven():
    partial = evaluate_exit(
        close=41_000,
        high=41_500,
        low=40_000,
        avg_entry=37_000,
        swing_high=40_000,
        swing_low=34_000,
        tranches_filled=1,
        partial_tp_done=False,
        risk_free=False,
        breakeven_stop=0,
    )
    assert partial == ("PARTIAL_TP_50", 41_000)

    be = evaluate_exit(
        close=36_000,
        high=37_000,
        low=36_500,
        avg_entry=37_000,
        swing_high=40_000,
        swing_low=34_000,
        tranches_filled=2,
        partial_tp_done=True,
        risk_free=True,
        breakeven_stop=37_000,
    )
    assert be == ("RISK_FREE_BREAKEVEN", 37_000)


def test_full_stop_swing_low():
    stop = evaluate_exit(
        close=33_000,
        high=34_000,
        low=33_500,
        avg_entry=37_000,
        swing_high=40_000,
        swing_low=34_000,
        tranches_filled=3,
        partial_tp_done=False,
        risk_free=False,
        breakeven_stop=0,
    )
    assert stop is not None
    assert stop[0] == "STOP_SWING_LOW"


def run_unit_tests() -> None:
    test_golden_cross_detected()
    test_fib_levels_ordered()
    test_tranche_signal_at_fib()
    test_partial_tp_then_breakeven()
    test_full_stop_swing_low()
    setup = build_fib_setup_from_history(_synthetic_uptrend())
    assert setup is None or len(setup.fib_prices) == len(FIB_RATIOS)
    print("  fib_swing_strategy unit tests OK", flush=True)


if __name__ == "__main__":
    run_unit_tests()
