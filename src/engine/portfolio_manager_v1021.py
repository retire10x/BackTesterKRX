"""
v10.2.1-Rebuild — v9.0 피보나치 스윙 + Safe Vault 자본 수확·수혈.

MarketClassifier·Momentum 제거. 순수 Fib 3단 그리드 + 15:30 CapitalBufferManager.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.engine.capital_buffer_manager import CapitalBufferManager
from src.engine.portfolio_manager import PortfolioResult, TRADES_DETAIL_COLUMNS
from src.engine.portfolio_manager_v900 import Alpha900Config, PortfolioManagerV900

SELL_COST_RATIO_V1021 = 0.0020


@dataclass
class Alpha1021Config(Alpha900Config):
    """v10.2.1 — v9.0 Fib + Safe Vault."""


class PortfolioManagerV1021(PortfolioManagerV900):
    """v9.0.0 Fib Swing + v10.2 CapitalBufferManager (수확·수혈)."""

    def __init__(
        self,
        *args,
        alpha: Alpha1021Config | None = None,
        capital_buffer: CapitalBufferManager | None = None,
        **kwargs,
    ):
        super().__init__(*args, alpha=alpha if alpha is not None else Alpha1021Config(), **kwargs)
        self.sell_cost_ratio = SELL_COST_RATIO_V1021
        self.capital_buffer = capital_buffer or CapitalBufferManager(
            target_capital=float(self.initial_equity)
        )
        self.has_realized_pnl_today = False

    def _execute_partial_sell(
        self,
        code: str,
        exit_price: float,
        exit_type: str,
        day_idx: int,
        sell_ratio: float = 0.5,
    ) -> None:
        super()._execute_partial_sell(code, exit_price, exit_type, day_idx, sell_ratio=sell_ratio)
        self.has_realized_pnl_today = True

    def _execute_sell(self, code: str, exit_price: float, exit_type: str, day_idx: int) -> None:
        super()._execute_sell(code, exit_price, exit_type, day_idx)
        self.has_realized_pnl_today = True

    def _realize_cash_from_positions(self, amount: float, day_idx: int) -> float:
        if amount <= 0 or not self.positions:
            return 0.0
        total_mv = sum(self._position_market_value(c, day_idx) for c in self.positions)
        if total_mv <= 0:
            return 0.0
        remaining = amount
        for code in list(self.positions.keys()):
            if remaining <= 0:
                break
            pos = self.positions.get(code)
            if pos is None:
                continue
            bar = self._get_daily_bar(code, day_idx)
            if bar is None:
                continue
            px = float(bar["close"])
            mv = self._position_market_value(code, day_idx)
            share = mv / total_mv
            need = min(remaining * share, mv)
            sell_qty = min(pos.qty, max(1, int(need // px)) if px > 0 else 0)
            if sell_qty < 1:
                continue
            gross = sell_qty * px
            proceeds = gross * (1.0 - self.sell_cost_ratio)
            self.cash += proceeds
            cost_frac = sell_qty / pos.qty
            pos.qty -= sell_qty
            pos.invest_amount *= (1.0 - cost_frac)
            pos.buy_cost_paid *= (1.0 - cost_frac)
            remaining -= proceeds
            if pos.qty <= 0:
                del self.positions[code]
        return amount - remaining

    def _apply_capital_rebalance(self, day_idx: int) -> dict[str, float | str]:
        total_equity = self._total_equity(day_idx)
        result = self.capital_buffer.rebalance(
            total_equity,
            has_realized_pnl=self.has_realized_pnl_today,
        )
        if result.cash_delta > 0:
            self.cash += result.cash_delta
        elif result.cash_delta < 0:
            withdraw = -result.cash_delta
            from_cash = min(self.cash, withdraw)
            self.cash -= from_cash
            left = withdraw - from_cash
            if left > 0:
                self._realize_cash_from_positions(left, day_idx)
        self.has_realized_pnl_today = False
        return {
            "event": result.event,
            "amount_moved": result.amount_moved,
            "available_capital": result.available_capital,
            "safe_vault": self.capital_buffer.safe_vault,
        }

    def run(self) -> PortfolioResult:
        if self.enable_prewarm and self.target_universe is not None:
            start = max(0, self._sim_start_idx - self.prewarm_bars)
            for di in range(start, self._sim_start_idx):
                for code in self.target_universe:
                    self._append_history_bar(code, di)

        for day_idx in range(self._sim_start_idx, self._sim_end_idx + 1):
            self.evaluate_daily_trades_v5(day_idx)
            reb = self._apply_capital_rebalance(day_idx)
            total_equity = self._total_equity(day_idx)
            trade_date = pd.Timestamp(self.bdays[day_idx]).normalize()
            self.equity_rows.append({
                "date": trade_date.strftime("%Y-%m-%d"),
                "cash": self.cash,
                "positions_value": total_equity - self.cash,
                "total_equity": total_equity,
                "open_slots": self.open_slot_count,
                "safe_vault": reb["safe_vault"],
                "rebalance_event": reb["event"],
            })

        equity_curve = pd.DataFrame(self.equity_rows)
        trades = pd.DataFrame(self.trade_rows)
        if self.trade_detail_rows:
            trades_detail = pd.DataFrame(self.trade_detail_rows)[TRADES_DETAIL_COLUMNS]
        else:
            trades_detail = pd.DataFrame(columns=TRADES_DETAIL_COLUMNS)

        from src.engine.portfolio_manager import PortfolioManager as _PM4

        metrics = _PM4._compute_metrics(equity_curve, trades, self.initial_equity)
        return PortfolioResult(
            metrics=metrics,
            equity_curve=equity_curve,
            trades=trades,
            trades_detail=trades_detail,
            pass_logs=self.pass_logs,
        )
