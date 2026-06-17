"""v10.1 market_classifier 유닛 테스트."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.engine.market_classifier import (
    build_regime_schedule,
    classify_index_level,
    compute_rolling_mas,
    merge_index_regimes,
)


def test_classify_momentum():
    assert classify_index_level(current=110.0, ma5=105.0, ma20=100.0) == "momentum"


def test_classify_cash():
    assert classify_index_level(current=90.0, ma5=95.0, ma20=100.0) == "cash"


def test_classify_swing_mixed():
    assert classify_index_level(current=102.0, ma5=100.0, ma20=105.0) == "swing"


def test_merge_regimes():
    assert merge_index_regimes("momentum", "momentum") == "momentum"
    assert merge_index_regimes("cash", "momentum") == "cash"
    assert merge_index_regimes("swing", "momentum") == "swing"


def test_compute_rolling_mas():
    closes = pd.Series(range(100, 119), dtype=float)
    ma5, ma20 = compute_rolling_mas(closes, 125.0)
    assert pd.notna(ma5) and pd.notna(ma20)
    assert ma5 > ma20


def test_intraday_stop_4pct():
    from src.engine.v10_live_core import evaluate_intraday_stop_loss

    hit = evaluate_intraday_stop_loss(entry_price=100_000, low=95_000)
    assert hit is not None
    assert hit[0] == "V10_INTRADAY_STOP_4PCT"
    assert hit[1] == 96_000
    assert evaluate_intraday_stop_loss(entry_price=100_000, low=97_000) is None


def test_build_regime_schedule():
    idx = pd.bdate_range("2024-01-01", periods=30)
    kospi = pd.Series(np.linspace(100, 110, 30), index=idx)
    kosdaq = pd.Series(np.linspace(90, 95, 30), index=idx)
    sched = build_regime_schedule(idx, kospi, kosdaq)
    assert len(sched) == 30
    assert sched[idx[0].strftime("%Y-%m-%d")] in ("momentum", "swing", "cash")


def run_unit_tests() -> None:
    test_classify_momentum()
    test_classify_cash()
    test_classify_swing_mixed()
    test_merge_regimes()
    test_compute_rolling_mas()
    test_intraday_stop_4pct()
    test_build_regime_schedule()
    print("  market_classifier unit tests OK", flush=True)


if __name__ == "__main__":
    run_unit_tests()
