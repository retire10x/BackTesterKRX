"""v10.2.1 CapitalBufferManager 유닛 테스트."""
from __future__ import annotations

from src.engine.capital_buffer_manager import CapitalBufferManager


def test_harvest_to_vault_with_realized_pnl():
    mgr = CapitalBufferManager(target_capital=2_000_000.0)
    out = mgr.process_daily_rebalancing(2_300_000.0, has_realized_pnl=True)
    assert out == 2_100_000.0
    assert mgr.safe_vault == 200_000.0
    assert mgr.stats.harvest_count == 1


def test_skip_rebalance_without_realized_pnl():
    mgr = CapitalBufferManager(target_capital=2_000_000.0)
    result = mgr.rebalance(2_300_000.0, has_realized_pnl=False)
    assert result.available_capital == 2_300_000.0
    assert result.event == "none"
    assert mgr.safe_vault == 0.0


def test_refill_full_from_vault():
    mgr = CapitalBufferManager(target_capital=2_000_000.0)
    mgr.safe_vault = 500_000.0
    result = mgr.rebalance(1_700_000.0, has_realized_pnl=True)
    assert result.available_capital == 2_000_000.0
    assert result.cash_delta == 300_000.0
    assert result.event == "refill_full"
    assert mgr.safe_vault == 200_000.0
    assert mgr.stats.refill_full_count == 1


def test_refill_partial_when_vault_insufficient():
    mgr = CapitalBufferManager(target_capital=2_000_000.0)
    mgr.safe_vault = 50_000.0
    result = mgr.rebalance(1_800_000.0, has_realized_pnl=True)
    assert result.available_capital == 1_850_000.0
    assert result.cash_delta == 50_000.0
    assert result.event == "refill_partial"
    assert mgr.safe_vault == 0.0


def test_buffer_zone_no_op():
    mgr = CapitalBufferManager(target_capital=2_000_000.0)
    result = mgr.rebalance(2_050_000.0, has_realized_pnl=True)
    assert result.event == "none"
    assert result.cash_delta == 0.0
    assert result.available_capital == 2_050_000.0
    assert mgr.safe_vault == 0.0


def test_no_op_at_target():
    mgr = CapitalBufferManager(target_capital=2_000_000.0)
    result = mgr.rebalance(2_000_000.0, has_realized_pnl=True)
    assert result.event == "none"
    assert result.cash_delta == 0.0
    assert mgr.safe_vault == 0.0


def run_unit_tests() -> None:
    test_harvest_to_vault_with_realized_pnl()
    test_skip_rebalance_without_realized_pnl()
    test_refill_full_from_vault()
    test_refill_partial_when_vault_insufficient()
    test_buffer_zone_no_op()
    test_no_op_at_target()
    print("  capital_buffer_manager unit tests OK", flush=True)


if __name__ == "__main__":
    run_unit_tests()
