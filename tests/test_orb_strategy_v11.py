"""v11.0 ORB 전략 유닛 테스트."""
from __future__ import annotations

import pandas as pd

from src.engine.orb_strategy_v11 import (
    detect_orb_breakout,
    estimate_orb_setup,
    evaluate_orb_exit,
    passes_ma5_alignment,
)


def test_estimate_orb_setup():
    setup = estimate_orb_setup(10_000, 10_500, 9_900)
    assert setup is not None
    assert setup.orb_high == 10_000 + 500 * 0.35
    assert setup.orb_low == 10_000


def test_ma5_alignment():
    closes = pd.Series([100, 101, 102, 103, 104, 106])
    assert passes_ma5_alignment(closes) is True
    falling = pd.Series([100, 99, 98, 97, 96, 94])
    assert passes_ma5_alignment(falling) is False


def test_breakout_detect():
    setup = estimate_orb_setup(10_000, 10_800, 9_950,)
    assert setup is not None
    ok = detect_orb_breakout(
        open_px=10_000,
        high_px=10_800,
        low_px=9_950,
        close_px=10_600,
        volume=2_000_000,
        avg_volume_5d=1_000_000,
        setup=setup,
    )
    assert ok is True


def test_stop_loss_exit():
    dec = evaluate_orb_exit(
        entry_price=10_000,
        open_px=10_000,
        high_px=10_200,
        low_px=9_700,
        close_px=9_800,
        partial_tp_done=False,
        risk_free=False,
        breakeven_stop=0,
    )
    assert dec is not None
    assert dec.exit_type == "STOP_LOSS"
    assert dec.sell_ratio == 1.0


def test_partial_then_breakeven():
    dec = evaluate_orb_exit(
        entry_price=10_000,
        open_px=10_300,
        high_px=10_350,
        low_px=10_000,
        close_px=10_100,
        partial_tp_done=False,
        risk_free=False,
        breakeven_stop=0,
    )
    assert dec is not None
    assert dec.exit_type == "PARTIAL_TP_50"
    assert dec.sell_ratio == 0.5

    dec2 = evaluate_orb_exit(
        entry_price=10_000,
        open_px=10_100,
        high_px=10_150,
        low_px=9_990,
        close_px=10_020,
        partial_tp_done=True,
        risk_free=True,
        breakeven_stop=10_000,
    )
    assert dec2 is not None
    assert dec2.exit_type == "RISK_FREE_BREAKEVEN"


def test_time_stop():
    dec = evaluate_orb_exit(
        entry_price=10_000,
        open_px=10_010,
        high_px=10_100,
        low_px=9_990,
        close_px=10_050,
        partial_tp_done=False,
        risk_free=False,
        breakeven_stop=0,
        force_eod=True,
    )
    assert dec is not None
    assert dec.exit_type == "TIME_STOP_1520"


def run_unit_tests() -> None:
    test_estimate_orb_setup()
    test_ma5_alignment()
    test_breakout_detect()
    test_stop_loss_exit()
    test_partial_then_breakeven()
    test_time_stop()
    print("  orb_strategy_v11 unit tests OK", flush=True)


if __name__ == "__main__":
    run_unit_tests()
