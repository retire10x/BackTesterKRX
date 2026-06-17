"""
v10.1.0 통합 포트폴리오 엔진 — 일별 MarketClassifier + Momentum/Swing/Cash 공유 슬롯.

자금: 200만 원 · 4슬롯 · 슬롯당 50만(스윙 1:1:2 분할 / 모멘텀 풀슬롯)
비용: 매수 0.015% · 매도 0.20%
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd

from src.engine.capital_buffer_manager import CapitalBufferManager
from src.engine.fib_swing_strategy import (
    MIN_MCAP_KRW,
    PREFERRED_MCAP_KRW,
    SLOT_BUDGET_KRW,
    FibSwingSetup,
    build_fib_setup_from_history,
    detect_tranche_signal,
    evaluate_exit,
)
from src.engine.high_tight_flag_strategy import (
    HIGH_LOOKBACK,
    MA_EXIT_WINDOW,
    MIN_MCAP_KRW as MOM_MIN_MCAP,
    MIN_TRADE_AMT_KRW,
    detect_momentum_entry,
    evaluate_momentum_exit,
)
from src.engine.market_classifier import Regime
from src.engine.portfolio_manager import PortfolioResult, TRADES_DETAIL_COLUMNS
from src.engine.portfolio_manager_v626 import DEFAULT_PREWARM_BARS, PortfolioManagerV626

EngineKind = Literal["swing", "momentum"]
MOMENTUM_SLOT_KRW = float(SLOT_BUDGET_KRW)
INTRADAY_STOP_RATIO = 0.04
SELL_COST_RATIO_V101 = 0.0020


@dataclass
class V101OpenPosition:
    code: str
    engine: EngineKind
    entry_date: pd.Timestamp
    entry_price: float
    qty: int
    invest_amount: float
    buy_cost_paid: float
    trade_id: int
    slot_budget_at_entry: float
    hold_days: int = 0
    tranches_filled: int = 0
    swing_high: float = 0.0
    swing_low: float = 0.0
    fib_prices: tuple[float, float, float] = (0.0, 0.0, 0.0)
    partial_tp_done: bool = False
    risk_free: bool = False
    breakeven_stop: float = 0.0
    prior_high: float = 0.0


@dataclass
class Alpha101Config:
    slot_budget_krw: float = SLOT_BUDGET_KRW
    prewarm_bars: int = DEFAULT_PREWARM_BARS
    swing_min_mcap_krw: float = MIN_MCAP_KRW
    swing_preferred_mcap_krw: float = PREFERRED_MCAP_KRW
    momentum_min_mcap_krw: float = MOM_MIN_MCAP
    momentum_min_trade_krw: float = MIN_TRADE_AMT_KRW


class PortfolioManagerV101(PortfolioManagerV626):
    """v10.1 일별 장세 스케줄 + 스윙/모멘텀 혼합 포지션."""

    def __init__(
        self,
        *args,
        alpha: Alpha101Config | None = None,
        regime_by_date: dict[str, Regime] | None = None,
        index_members: frozenset[str] | None = None,
        capital_buffer: CapitalBufferManager | None = None,
        **kwargs,
    ):
        super().__init__(*args, enable_prewarm=True, **kwargs)
        self.alpha101 = alpha if alpha is not None else Alpha101Config()
        self.prewarm_bars = max(int(self.alpha101.prewarm_bars), 260)
        self.regime_by_date = regime_by_date if regime_by_date is not None else {}
        self.index_members = index_members if index_members is not None else frozenset()
        self.capital_buffer = capital_buffer or CapitalBufferManager(
            target_capital=float(self.initial_equity)
        )
        self.max_slots = 4
        self.fixed_amount = float(self.alpha101.slot_budget_krw)
        self.sell_cost_ratio = SELL_COST_RATIO_V101
        self.macro_trend_enabled = False
        self.use_hit_and_run_exit = False
        self.positions: dict[str, V101OpenPosition] = {}

    def _macro_min_history_bars(self) -> int:
        return max(260, self.prewarm_bars)

    def _regime_for_day(self, day_idx: int) -> Regime:
        dt = pd.Timestamp(self.bdays[day_idx]).strftime("%Y-%m-%d")
        return self.regime_by_date.get(dt, "swing")

    def _passes_swing_universe(self, code: str, day_idx: int) -> bool:
        c6 = str(code).zfill(6)
        mc = self._get_market_cap_krw(c6, day_idx)
        if mc is None or mc < self.alpha101.swing_min_mcap_krw:
            return False
        if c6 in self.index_members:
            return True
        return mc >= self.alpha101.swing_preferred_mcap_krw

    def _passes_momentum_universe(self, code: str, day_idx: int) -> bool:
        c6 = str(code).zfill(6)
        mc = self._get_market_cap_krw(c6, day_idx)
        tv = self._get_trading_value_krw(c6, day_idx)
        if mc is None or mc < self.alpha101.momentum_min_mcap_krw:
            return False
        if tv is None or tv < self.alpha101.momentum_min_trade_krw:
            return False
        return True

    def _candidate_codes_ranked(self, day_idx: int, *, engine: EngineKind) -> list[str]:
        day_frame = self.day_frames[day_idx]
        index_by_c6 = {str(k).zfill(6): k for k in day_frame.index}
        if self.target_universe is not None:
            scan_codes = [c for c in self.target_universe if c in index_by_c6]
        else:
            scan_codes = list(index_by_c6.keys())

        rows: list[tuple[str, float]] = []
        for c6 in scan_codes:
            if c6 in self.positions:
                continue
            if engine == "swing":
                if not self._passes_swing_universe(c6, day_idx):
                    continue
            else:
                if not self._passes_momentum_universe(c6, day_idx):
                    continue
            key = index_by_c6[c6]
            close_px = float(day_frame.loc[key, "Close"])
            vol = float(day_frame.loc[key, "Volume"])
            if not np.isfinite(close_px) or not np.isfinite(vol):
                continue
            rows.append((c6, close_px * vol))
        rows.sort(key=lambda x: x[1], reverse=True)
        return [c for c, _ in rows]

    def _execute_buy_tranche(
        self,
        code: str,
        entry_price: float,
        day_idx: int,
        amount_krw: float,
        setup: FibSwingSetup,
        tranche_index: int,
    ) -> bool:
        c6 = str(code).zfill(6)
        if not np.isfinite(entry_price) or entry_price <= 0 or amount_krw <= 0:
            return False
        qty = int(amount_krw // entry_price)
        if qty < 1:
            return False

        invest_amount = float(qty * entry_price)
        buy_cost_paid = invest_amount * self.buy_cost_ratio
        total_debit = invest_amount + buy_cost_paid
        if self.cash < total_debit:
            return False

        trade_date = pd.Timestamp(self.bdays[day_idx]).normalize()
        pos = self.positions.get(c6)

        if pos is None:
            if self.available_slots <= 0:
                return False
            self._trade_id_counter += 1
            trade_id = self._trade_id_counter
            self.cash -= total_debit
            self.positions[c6] = V101OpenPosition(
                code=c6,
                engine="swing",
                entry_date=trade_date,
                entry_price=entry_price,
                qty=qty,
                invest_amount=invest_amount,
                buy_cost_paid=buy_cost_paid,
                trade_id=trade_id,
                slot_budget_at_entry=self.fixed_amount,
                tranches_filled=1,
                swing_high=setup.swing_high,
                swing_low=setup.swing_low,
                fib_prices=setup.fib_prices,
            )
            self._append_trade_detail(
                side="BUY",
                day_idx=day_idx,
                code=c6,
                trade_id=trade_id,
                entry_date=trade_date.strftime("%Y-%m-%d"),
                entry_price=entry_price,
                qty=float(qty),
                invest_amount=invest_amount,
                exit_type=f"SWING_T{tranche_index + 1}",
            )
            return True

        if pos.engine != "swing" or tranche_index != pos.tranches_filled:
            return False
        slot_spent = pos.invest_amount + pos.buy_cost_paid
        if slot_spent + total_debit > self.fixed_amount * 1.001:
            return False

        old_qty = pos.qty
        new_qty = old_qty + qty
        new_invest = pos.invest_amount + invest_amount
        new_cost = pos.buy_cost_paid + buy_cost_paid
        new_avg = new_invest / new_qty if new_qty > 0 else entry_price
        self.cash -= total_debit
        pos.qty = new_qty
        pos.invest_amount = new_invest
        pos.buy_cost_paid = new_cost
        pos.entry_price = new_avg
        pos.tranches_filled += 1
        pos.swing_high = setup.swing_high
        pos.swing_low = setup.swing_low
        pos.fib_prices = setup.fib_prices
        self._append_trade_detail(
            side="BUY",
            day_idx=day_idx,
            code=c6,
            trade_id=pos.trade_id,
            entry_date=pos.entry_date.strftime("%Y-%m-%d"),
            entry_price=entry_price,
            qty=float(qty),
            invest_amount=invest_amount,
            exit_type=f"SWING_ADD_T{tranche_index + 1}",
        )
        return True

    def _execute_momentum_buy(self, code: str, entry_price: float, day_idx: int) -> bool:
        c6 = str(code).zfill(6)
        if c6 in self.positions or self.available_slots <= 0:
            return False
        if not np.isfinite(entry_price) or entry_price <= 0:
            return False

        amount_krw = MOMENTUM_SLOT_KRW
        qty = int(amount_krw // entry_price)
        if qty < 1:
            return False
        invest_amount = float(qty * entry_price)
        buy_cost_paid = invest_amount * self.buy_cost_ratio
        total_debit = invest_amount + buy_cost_paid
        if self.cash < total_debit:
            return False

        self._append_history_bar(c6, day_idx)
        hist = self._history_as_of(c6, day_idx)
        prior_high = 0.0
        if hist is not None and len(hist) >= 20:
            high_s = pd.to_numeric(hist["high"], errors="coerce")
            prior_high = float(high_s.iloc[-HIGH_LOOKBACK:].max())

        trade_date = pd.Timestamp(self.bdays[day_idx]).normalize()
        self._trade_id_counter += 1
        trade_id = self._trade_id_counter
        self.cash -= total_debit
        self.positions[c6] = V101OpenPosition(
            code=c6,
            engine="momentum",
            entry_date=trade_date,
            entry_price=entry_price,
            qty=qty,
            invest_amount=invest_amount,
            buy_cost_paid=buy_cost_paid,
            trade_id=trade_id,
            slot_budget_at_entry=MOMENTUM_SLOT_KRW,
            prior_high=prior_high,
        )
        self._append_trade_detail(
            side="BUY",
            day_idx=day_idx,
            code=c6,
            trade_id=trade_id,
            entry_date=trade_date.strftime("%Y-%m-%d"),
            entry_price=entry_price,
            qty=float(qty),
            invest_amount=invest_amount,
            exit_type="MOMENTUM_ENTRY",
        )
        return True

    def _execute_partial_sell(
        self,
        code: str,
        exit_price: float,
        exit_type: str,
        day_idx: int,
        sell_ratio: float = 0.5,
    ) -> None:
        c6 = str(code).zfill(6)
        pos = self.positions.get(c6)
        if pos is None:
            return
        sell_qty = int(pos.qty * sell_ratio)
        if sell_qty < 1 or not np.isfinite(exit_price) or exit_price <= 0:
            return

        frac = sell_qty / pos.qty
        gross = sell_qty * exit_price
        proceeds = gross * (1.0 - self.sell_cost_ratio)
        cost_basis = (pos.invest_amount + pos.buy_cost_paid) * frac
        pnl_amount = proceeds - cost_basis
        pnl_rate = pnl_amount / cost_basis if cost_basis > 0 else 0.0

        self.cash += proceeds
        pos.qty -= sell_qty
        pos.invest_amount *= (1.0 - frac)
        pos.buy_cost_paid *= (1.0 - frac)
        pos.partial_tp_done = True
        pos.risk_free = True
        pos.breakeven_stop = pos.entry_price

        entry_date_s = pos.entry_date.strftime("%Y-%m-%d")
        self.trade_rows.append({
            "code": c6,
            "stage": 1,
            "entry_date": entry_date_s,
            "exit_date": pd.Timestamp(self.bdays[day_idx]).strftime("%Y-%m-%d"),
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "invest_amount": cost_basis,
            "pnl_amount": pnl_amount,
            "pnl_rate": pnl_rate,
            "exit_type": exit_type,
            "engine": pos.engine,
        })
        self._append_trade_detail(
            side="SELL",
            day_idx=day_idx,
            code=c6,
            trade_id=pos.trade_id,
            entry_date=entry_date_s,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            qty=float(sell_qty),
            invest_amount=cost_basis,
            proceeds=proceeds,
            pnl_amount=pnl_amount,
            pnl_rate=pnl_rate,
            exit_type=exit_type,
        )

    def _execute_sell(self, code: str, exit_price: float, exit_type: str, day_idx: int) -> None:
        c6 = str(code).zfill(6)
        pos = self.positions.get(c6)
        if pos is None or not np.isfinite(exit_price) or exit_price <= 0:
            return

        gross = pos.qty * exit_price
        proceeds = gross * (1.0 - self.sell_cost_ratio)
        cost_basis = pos.invest_amount + pos.buy_cost_paid
        pnl_amount = proceeds - cost_basis
        pnl_rate = pnl_amount / cost_basis if cost_basis > 0 else 0.0
        self.cash += proceeds
        entry_date_s = pos.entry_date.strftime("%Y-%m-%d")
        self.trade_rows.append({
            "code": c6,
            "stage": 1,
            "entry_date": entry_date_s,
            "exit_date": pd.Timestamp(self.bdays[day_idx]).strftime("%Y-%m-%d"),
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "invest_amount": pos.invest_amount,
            "pnl_amount": pnl_amount,
            "pnl_rate": pnl_rate,
            "exit_type": exit_type,
            "engine": pos.engine,
        })
        self._append_trade_detail(
            side="SELL",
            day_idx=day_idx,
            code=c6,
            trade_id=pos.trade_id,
            entry_date=entry_date_s,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            qty=float(pos.qty),
            invest_amount=pos.invest_amount,
            proceeds=proceeds,
            pnl_amount=pnl_amount,
            pnl_rate=pnl_rate,
            exit_type=exit_type,
        )
        del self.positions[c6]

    def _process_swing_exits(self, code: str, pos: V101OpenPosition, day_idx: int, bar: dict) -> None:
        decision = evaluate_exit(
            close=float(bar["close"]),
            high=float(bar["high"]),
            low=float(bar["low"]),
            avg_entry=float(pos.entry_price),
            swing_high=float(pos.swing_high),
            swing_low=float(pos.swing_low),
            tranches_filled=int(pos.tranches_filled),
            partial_tp_done=bool(pos.partial_tp_done),
            risk_free=bool(pos.risk_free),
            breakeven_stop=float(pos.breakeven_stop),
        )
        if decision is None:
            return
        exit_type, exit_price = decision
        if exit_type == "PARTIAL_TP_50":
            self._execute_partial_sell(code, exit_price, f"SWING_{exit_type}", day_idx, sell_ratio=0.5)
        else:
            self._execute_sell(code, exit_price, f"SWING_{exit_type}", day_idx)

    def _process_momentum_exits(self, code: str, pos: V101OpenPosition, day_idx: int, bar: dict, hist) -> None:
        stop_4 = pos.entry_price * (1.0 - INTRADAY_STOP_RATIO)
        if float(bar["low"]) <= stop_4:
            self._execute_sell(code, stop_4, "MOMENTUM_STOP_4PCT", day_idx)
            return

        close_s = pd.to_numeric(hist["close"], errors="coerce")
        ma10 = float(close_s.rolling(MA_EXIT_WINDOW).mean().iloc[-1])
        prior = float(pos.prior_high or hist["high"].iloc[-HIGH_LOOKBACK:].max())
        result = evaluate_momentum_exit(
            close=float(bar["close"]),
            high=float(bar["high"]),
            low=float(bar["low"]),
            avg_entry=float(pos.entry_price),
            prior_high=prior,
            partial_tp_done=bool(pos.partial_tp_done),
            risk_free=bool(pos.risk_free),
            breakeven_stop=float(pos.breakeven_stop),
            ma10=ma10,
        )
        if result is None:
            return
        exit_type, exit_price, sell_ratio = result
        if sell_ratio < 1.0:
            self._execute_partial_sell(code, exit_price, exit_type, day_idx, sell_ratio=sell_ratio)
        else:
            self._execute_sell(code, exit_price, exit_type, day_idx)

    def _process_exits(self, day_idx: int) -> None:
        for code in list(self.positions.keys()):
            pos = self.positions.get(code)
            if pos is None:
                continue
            self._append_history_bar(code, day_idx)
            pos.hold_days += 1
            bar = self._get_daily_bar(code, day_idx)
            if bar is None:
                continue
            hist = self._history_as_of(code, day_idx)
            if pos.engine == "swing":
                self._process_swing_exits(code, pos, day_idx, bar)
            elif hist is not None:
                self._process_momentum_exits(code, pos, day_idx, bar, hist)

    def _try_swing_entry(self, code: str, day_idx: int) -> None:
        c6 = str(code).zfill(6)
        if not self._passes_swing_universe(c6, day_idx):
            return
        self._append_history_bar(c6, day_idx)
        hist = self._history_as_of(c6, day_idx)
        if hist is None or len(hist) < self._macro_min_history_bars():
            return
        setup = build_fib_setup_from_history(hist)
        if setup is None:
            return
        close_px = float(hist.iloc[-1]["close"])
        pos = self.positions.get(c6)
        tranches_filled = pos.tranches_filled if pos is not None and pos.engine == "swing" else 0
        signal = detect_tranche_signal(close_px, tranches_filled, setup)
        if signal is None:
            return
        self._execute_buy_tranche(c6, close_px, day_idx, signal.amount_krw, setup, signal.tranche_index)

    def _try_momentum_entry(self, code: str, day_idx: int) -> None:
        c6 = str(code).zfill(6)
        if c6 in self.positions or not self._passes_momentum_universe(c6, day_idx):
            return
        self._append_history_bar(c6, day_idx)
        hist = self._history_as_of(c6, day_idx)
        if hist is None or len(hist) < self._macro_min_history_bars():
            return
        ok, _ = detect_momentum_entry(hist)
        if not ok:
            return
        close_px = float(hist.iloc[-1]["close"])
        self._execute_momentum_buy(c6, close_px, day_idx)

    def _process_swing_tranche_adds(self, day_idx: int) -> None:
        for code in list(self.positions.keys()):
            pos = self.positions.get(code)
            if pos is None or pos.engine != "swing":
                continue
            if self.cash <= 0:
                break
            self._try_swing_entry(code, day_idx)

    def _process_regime_entries(self, day_idx: int, regime: Regime) -> None:
        if regime == "cash" or self.cash <= 0:
            return
        if regime == "swing":
            for code in self._candidate_codes_ranked(day_idx, engine="swing"):
                if self.available_slots <= 0 or self.cash <= 0:
                    break
                self._try_swing_entry(code, day_idx)
        elif regime == "momentum":
            for code in self._candidate_codes_ranked(day_idx, engine="momentum"):
                if self.available_slots <= 0 or self.cash <= 0:
                    break
                self._try_momentum_entry(code, day_idx)

    def _realize_cash_from_positions(self, amount: float, day_idx: int) -> float:
        """수확분만큼 종가 기준 비례 매도 → 현금화."""
        if amount <= 0 or not self.positions:
            return 0.0
        total_mv = sum(self._position_market_value(c, day_idx) for c in self.positions)
        if total_mv <= 0:
            return 0.0
        realized = 0.0
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
            realized += proceeds
            remaining -= proceeds
            if pos.qty <= 0:
                del self.positions[code]
        return realized

    def _apply_capital_rebalance(self, day_idx: int) -> dict[str, float | str]:
        """15:30 EOD — Safe Vault 수확·수혈."""
        total_equity = self._total_equity(day_idx)
        result = self.capital_buffer.rebalance(total_equity)
        if result.cash_delta > 0:
            self.cash += result.cash_delta
        elif result.cash_delta < 0:
            withdraw = -result.cash_delta
            from_cash = min(self.cash, withdraw)
            self.cash -= from_cash
            left = withdraw - from_cash
            if left > 0:
                self._realize_cash_from_positions(left, day_idx)
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
            regime = self._regime_for_day(day_idx)
            trade_date = pd.Timestamp(self.bdays[day_idx]).normalize()

            self._process_exits(day_idx)
            self._process_swing_tranche_adds(day_idx)
            self._process_regime_entries(day_idx, regime)

            reb = self._apply_capital_rebalance(day_idx)
            total_equity = self._total_equity(day_idx)
            self.equity_rows.append({
                "date": trade_date.strftime("%Y-%m-%d"),
                "cash": self.cash,
                "positions_value": total_equity - self.cash,
                "total_equity": total_equity,
                "open_slots": self.open_slot_count,
                "regime": regime,
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
