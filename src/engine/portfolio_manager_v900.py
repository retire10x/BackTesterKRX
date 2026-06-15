"""
v9.0.0 대형주 피보나치 스윙 (Risk-Free Swing) 포트폴리오 엔진.

자금: 200만 원 · 4슬롯 · 슬롯당 50만(1:1:2 분할)
유니버스: KOSPI200/KOSDAQ150 또는 시총 1조+, 5,000억 미만 제외
진입: MA60×MA200 GC(3~6개월) + 피보나치 0.382/0.500/0.618 종가 분할매수
청산: 스윙고점 50% 익절 → 본전 손절선 → 0라인/1:2 전량손절
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.engine.fib_swing_strategy import (
    MIN_MCAP_KRW,
    PREFERRED_MCAP_KRW,
    SLOT_BUDGET_KRW,
    FibSwingSetup,
    build_fib_setup_from_history,
    detect_tranche_signal,
    evaluate_exit,
    load_index_members,
)
from src.engine.portfolio_manager_v626 import DEFAULT_PREWARM_BARS, PortfolioManagerV626


@dataclass
class V9OpenPosition:
    code: str
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
    gc_date: pd.Timestamp | None = None
    partial_tp_done: bool = False
    risk_free: bool = False
    breakeven_stop: float = 0.0


@dataclass
class Alpha900Config:
    min_mcap_krw: float = MIN_MCAP_KRW
    preferred_mcap_krw: float = PREFERRED_MCAP_KRW
    slot_budget_krw: float = SLOT_BUDGET_KRW
    prewarm_bars: int = DEFAULT_PREWARM_BARS


class PortfolioManagerV900(PortfolioManagerV626):
    """v9.0.0 피보나치 분할매수 + Risk-Free 청산."""

    def __init__(
        self,
        *args,
        alpha: Alpha900Config | None = None,
        index_members: frozenset[str] | None = None,
        **kwargs,
    ):
        super().__init__(*args, enable_prewarm=True, **kwargs)
        self.alpha900 = alpha if alpha is not None else Alpha900Config()
        self.prewarm_bars = max(int(self.alpha900.prewarm_bars), 220)
        self.index_members = index_members if index_members is not None else frozenset()
        self.max_slots = 4
        self.fixed_amount = float(self.alpha900.slot_budget_krw)
        self.macro_trend_enabled = False
        self.use_hit_and_run_exit = False

    def _macro_min_history_bars(self) -> int:
        return max(220, self.prewarm_bars)

    def _passes_universe_filter(self, code: str, day_idx: int) -> bool:
        c6 = str(code).zfill(6)
        mc = self._get_market_cap_krw(c6, day_idx)
        if mc is None or mc < self.alpha900.min_mcap_krw:
            return False
        if c6 in self.index_members:
            return True
        return mc >= self.alpha900.preferred_mcap_krw

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
            self.pass_logs.append(
                f"{pd.Timestamp(self.bdays[day_idx]).date()} {c6} Pass — "
                f"분할 {tranche_index + 1}차 주수 0 (가격 {entry_price:,.0f})"
            )
            return False

        invest_amount = float(qty * entry_price)
        buy_cost_paid = invest_amount * self.buy_cost_ratio
        total_debit = invest_amount + buy_cost_paid
        if self.cash < total_debit:
            self.pass_logs.append(
                f"{pd.Timestamp(self.bdays[day_idx]).date()} {c6} Pass — 현금 부족 "
                f"(분할 {tranche_index + 1}차)"
            )
            return False

        trade_date = pd.Timestamp(self.bdays[day_idx]).normalize()
        pos = self.positions.get(c6)

        if pos is None:
            if self.available_slots <= 0:
                return False
            self._trade_id_counter += 1
            trade_id = self._trade_id_counter
            self.cash -= total_debit
            self.positions[c6] = V9OpenPosition(
                code=c6,
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
                gc_date=setup.gc_date,
            )
            exit_tag = f"ENTRY_FIB_T{tranche_index + 1}_{setup.fib_prices[tranche_index]:.0f}"
            self._append_trade_detail(
                side="BUY",
                day_idx=day_idx,
                code=c6,
                trade_id=trade_id,
                entry_date=trade_date.strftime("%Y-%m-%d"),
                entry_price=entry_price,
                qty=float(qty),
                invest_amount=invest_amount,
                exit_type=exit_tag,
            )
            self.pass_logs.append(
                f"{trade_date.date()} {c6} [FIB BUY T{tranche_index + 1}] "
                f"@{entry_price:,.0f} · {qty}주 · {invest_amount:,.0f}원"
            )
            return True

        if not isinstance(pos, V9OpenPosition):
            return False
        if tranche_index != pos.tranches_filled:
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
            exit_type=f"ADD_FIB_T{tranche_index + 1}",
        )
        self.pass_logs.append(
            f"{trade_date.date()} {c6} [FIB ADD T{tranche_index + 1}] "
            f"@{entry_price:,.0f} · 평단 {new_avg:,.0f}"
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
        if pos is None or not isinstance(pos, V9OpenPosition):
            return
        if sell_ratio <= 0 or sell_ratio >= 1:
            return

        sell_qty = int(pos.qty * sell_ratio)
        if sell_qty < 1:
            return
        if not np.isfinite(exit_price) or exit_price <= 0:
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

        trade_date = pd.Timestamp(self.bdays[day_idx]).normalize()
        entry_date_s = pos.entry_date.strftime("%Y-%m-%d")
        self.trade_rows.append({
            "code": c6,
            "stage": 1,
            "entry_date": entry_date_s,
            "exit_date": trade_date.strftime("%Y-%m-%d"),
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "invest_amount": cost_basis,
            "pnl_amount": pnl_amount,
            "pnl_rate": pnl_rate,
            "exit_type": exit_type,
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
        self.pass_logs.append(
            f"{trade_date.date()} {c6} [{exit_type}] 50% @{exit_price:,.0f} · "
            f"본전손절 {pos.breakeven_stop:,.0f}"
        )

    def _execute_sell(self, code: str, exit_price: float, exit_type: str, day_idx: int) -> None:
        c6 = str(code).zfill(6)
        pos = self.positions.get(c6)
        if pos is None:
            return
        if not np.isfinite(exit_price) or exit_price <= 0:
            return

        gross = pos.qty * exit_price
        proceeds = gross * (1.0 - self.sell_cost_ratio)
        cost_basis = pos.invest_amount + pos.buy_cost_paid
        pnl_amount = proceeds - cost_basis
        pnl_rate = pnl_amount / cost_basis if cost_basis > 0 else 0.0

        self.cash += proceeds
        trade_date = pd.Timestamp(self.bdays[day_idx]).normalize()
        entry_date_s = pos.entry_date.strftime("%Y-%m-%d")

        self.trade_rows.append({
            "code": c6,
            "stage": 1,
            "entry_date": entry_date_s,
            "exit_date": trade_date.strftime("%Y-%m-%d"),
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "invest_amount": pos.invest_amount,
            "pnl_amount": pnl_amount,
            "pnl_rate": pnl_rate,
            "exit_type": exit_type,
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

    def _process_exits(self, day_idx: int) -> None:
        for code in list(self.positions.keys()):
            pos = self.positions.get(code)
            if pos is None or not isinstance(pos, V9OpenPosition):
                continue
            self._append_history_bar(code, day_idx)
            pos.hold_days += 1
            bar = self._get_daily_bar(code, day_idx)
            if bar is None:
                continue

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
                continue
            exit_type, exit_price = decision
            if exit_type == "PARTIAL_TP_50":
                self._execute_partial_sell(code, exit_price, exit_type, day_idx, sell_ratio=0.5)
            else:
                self._execute_sell(code, exit_price, exit_type, day_idx)

    def _try_fib_entry(self, code: str, day_idx: int) -> None:
        c6 = str(code).zfill(6)
        if not self._passes_universe_filter(c6, day_idx):
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
        tranches_filled = pos.tranches_filled if isinstance(pos, V9OpenPosition) else 0

        signal = detect_tranche_signal(close_px, tranches_filled, setup)
        if signal is None:
            return

        self._execute_buy_tranche(
            c6,
            close_px,
            day_idx,
            signal.amount_krw,
            setup,
            signal.tranche_index,
        )

    def _process_entries(self, day_idx: int, candidate_codes: list[str]) -> None:
        if self.cash <= 0:
            return
        for code in candidate_codes:
            if self.cash <= 0:
                break
            self._try_fib_entry(code, day_idx)
        for code in list(self.positions.keys()):
            if self.cash <= 0:
                break
            self._try_fib_entry(code, day_idx)

    def _candidate_codes_ranked(self, day_idx: int) -> list[str]:
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
            if not self._passes_universe_filter(c6, day_idx):
                continue
            key = index_by_c6[c6]
            close_px = float(day_frame.loc[key, "Close"])
            vol = float(day_frame.loc[key, "Volume"])
            if not np.isfinite(close_px) or not np.isfinite(vol):
                continue
            rows.append((c6, close_px * vol))
        rows.sort(key=lambda x: x[1], reverse=True)
        return [c for c, _ in rows]
