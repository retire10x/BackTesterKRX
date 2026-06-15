"""v8.0.0 ORB 순수 로직 유닛 테스트."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from src.engine.orb_strategy import (
    OrbExitReason,
    compute_opening_high,
    evaluate_orb_exit,
    passes_premarket_universe,
    should_enter_breakout,
)

KST = ZoneInfo("Asia/Seoul")


def test_premarket_universe_gap_band() -> None:
    assert passes_premarket_universe(
        prior_trading_value_krw=50_000_000_000,
        today_open=10_300,
        prior_close=10_000,
    )
    assert not passes_premarket_universe(
        prior_trading_value_krw=50_000_000_000,
        today_open=10_800,
        prior_close=10_000,
    )


def test_opening_high_and_breakout_window() -> None:
    idx = pd.date_range("2026-06-16 09:00", periods=5, freq="1min", tz=KST)
    bars = pd.DataFrame({"high": [100, 102, 105, 104, 103]}, index=idx)
    assert compute_opening_high(bars) == 105.0

    inside = datetime(2026, 6, 16, 9, 10, tzinfo=KST)
    assert should_enter_breakout(current_price=106, opening_high=105, now=inside)
    outside = datetime(2026, 6, 16, 9, 31, tzinfo=KST)
    assert not should_enter_breakout(current_price=106, opening_high=105, now=outside)


def test_hit_and_run_exits() -> None:
    now = datetime(2026, 6, 16, 10, 0, tzinfo=KST)
    assert evaluate_orb_exit(entry_price=100, current_price=105.1, now=now) == OrbExitReason.TAKE_PROFIT
    assert evaluate_orb_exit(entry_price=100, current_price=97.9, now=now) == OrbExitReason.STOP_LOSS
    late = datetime(2026, 6, 16, 14, 50, tzinfo=KST)
    assert evaluate_orb_exit(entry_price=100, current_price=100.5, now=late) == OrbExitReason.TIME_STOP
