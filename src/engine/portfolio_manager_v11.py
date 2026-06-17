"""
v11.0 ORB 데이트레이딩 + EOD Safe Vault 포트폴리오 매니저.

당일 15:20 전량 청산(Time-stop) · 15:30 CapitalBufferManager 정산.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from src.engine.capital_buffer_manager import CapitalBufferManager
from src.engine.orb_strategy_v11 import (
    detect_orb_breakout,
    estimate_orb_setup,
    evaluate_orb_exit,
    passes_ma5_alignment,
)
from src.engine.portfolio_manager import PortfolioResult, TRADES_DETAIL_COLUMNS
from src.engine.portfolio_manager_v626 import Alpha626Config, PortfolioManagerV626

SELL_COST_RATIO_V11 = 0.0020


@dataclass
class Alpha11Config(Alpha626Config):
    """v11.0 ORB 데이트레이딩 파라미터."""

    top_n_turnover: int = 100
    require_ma5_alignment: bool = True
    min_turnover_ratio: float = 0.0


@dataclass
class V11OpenPosition:
    code: str
    entry_date: pd.Timestamp
    entry_price: float
    qty: int
    invest_amount: float
    buy_cost_paid: float
    trade_id: int
    slot_budget_at_entry: float
    orb_high: float
    partial_tp_done: bool = False
    risk_free: bool = False
    breakeven_stop: float = 0.0


class PortfolioManagerV11(PortfolioManagerV626):
    """ORB 데이트레이딩 + Safe Vault."""

    def __init__(
        self,
        *args,
        alpha: Alpha11Config | None = None,
        capital_buffer: CapitalBufferManager | None = None,
        **kwargs,
    ):
        super().__init__(*args, alpha=alpha if alpha is not None else Alpha11Config(), **kwargs)
        self.sell_cost_ratio = SELL_COST_RATIO_V11
        self.capital_buffer = capital_buffer or CapitalBufferManager(
            target_capital=float(self.initial_equity)
        )
        self.has_realized_pnl_today = False
        self.had_trades_today = False
        self.overnight_violations: list[str] = []
        self.positions: dict[str, V11OpenPosition] = {}

    @property
    def open_slot_count(self) -> int:
        return len(self.positions)

    def _history_closes(self, code: str, day_idx: int, lookback: int = 6) -> pd.Series | None:
        hist = self._history_as_of(code, day_idx)
        if hist is None or len(hist) < 5:
            return None
        return hist["close"].iloc[-lookback:]

    def _prev_day_turnover_ranked(self, day_idx: int) -> list[str]:
        """전일 거래대금 상위 N — look-ahead 방지."""
        if day_idx < 1:
            return []
        prev_idx = day_idx - 1
        day_frame = self.day_frames[prev_idx]
        index_by_c6 = {str(k).zfill(6): k for k in day_frame.index}
        if self.target_universe is not None:
            scan_codes = [c for c in self.target_universe if c in index_by_c6]
        else:
            scan_codes = list(index_by_c6.keys())

        rows: list[tuple[str, float]] = []
        for c6 in scan_codes:
            if c6 in self.positions:
                continue
            key = index_by_c6[c6]
            close_px = float(day_frame.loc[key, "Close"])
            vol = float(day_frame.loc[key, "Volume"])
            if not np.isfinite(close_px) or not np.isfinite(vol) or close_px <= 0:
                continue
            rows.append((c6, close_px * vol))
        rows.sort(key=lambda x: x[1], reverse=True)
        top_n = int(self.alpha.top_n_turnover)
        return [c for c, _ in rows[:top_n]]

    def _passes_orb_universe(self, code: str, day_idx: int) -> bool:
        if day_idx < 1:
            return False
        prev_idx = day_idx - 1
        self._append_history_bar(code, prev_idx)
        closes = self._history_closes(code, prev_idx, lookback=10)
        if closes is None:
            return False
        if self.alpha.require_ma5_alignment and not passes_ma5_alignment(closes):
            return False
        return True

    def _avg_volume_5d(self, code: str, day_idx: int) -> float:
        hist = self._history_as_of(code, day_idx)
        if hist is None or len(hist) < 2:
            return 0.0
        vols = hist["volume"].iloc[-6:-1] if len(hist) >= 6 else hist["volume"].iloc[:-1]
        if vols.empty:
            return 0.0
        return float(vols.mean())

    def _execute_orb_buy(self, code: str, entry_price: float, orb_high: float, day_idx: int) -> bool:
        c6 = str(code).zfill(6)
        if c6 in self.positions or self.available_slots <= 0:
            return False
        if not np.isfinite(entry_price) or entry_price <= 0:
            return False

        qty = int(self.fixed_amount // entry_price)
        if qty < 1:
            return False

        invest_amount = float(qty * entry_price)
        buy_cost_paid = invest_amount * self.buy_cost_ratio
        total_debit = invest_amount + buy_cost_paid
        if self.cash < total_debit:
            return False

        self._trade_id_counter += 1
        trade_id = self._trade_id_counter
        trade_date = pd.Timestamp(self.bdays[day_idx]).normalize()
        self.cash -= total_debit
        self.positions[c6] = V11OpenPosition(
            code=c6,
            entry_date=trade_date,
            entry_price=entry_price,
            qty=qty,
            invest_amount=invest_amount,
            buy_cost_paid=buy_cost_paid,
            trade_id=trade_id,
            slot_budget_at_entry=self.fixed_amount,
            orb_high=orb_high,
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
            exit_type="ORB_BREAKOUT",
        )
        self.had_trades_today = True
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
        if pos is None or sell_ratio <= 0 or sell_ratio >= 1:
            return
        if not np.isfinite(exit_price) or exit_price <= 0:
            return

        sell_qty = int(pos.qty * sell_ratio)
        if sell_qty < 1:
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
        self.has_realized_pnl_today = True
        self.had_trades_today = True

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
        self.has_realized_pnl_today = True
        self.had_trades_today = True

    def _process_orb_entries(self, day_idx: int, candidates: list[str]) -> None:
        if self.available_slots <= 0 or self.cash <= 0:
            return
        for code in candidates:
            if self.available_slots <= 0 or self.cash <= 0:
                break
            c6 = str(code).zfill(6)
            if c6 in self.positions:
                continue
            if not self._passes_orb_universe(c6, day_idx):
                continue

            bar = self._get_daily_bar(c6, day_idx)
            if bar is None:
                continue

            setup = estimate_orb_setup(bar["open"], bar["high"], bar["low"])
            if setup is None:
                continue

            avg_vol = self._avg_volume_5d(c6, day_idx)
            if not detect_orb_breakout(
                open_px=bar["open"],
                high_px=bar["high"],
                low_px=bar["low"],
                close_px=bar["close"],
                volume=bar["volume"],
                avg_volume_5d=avg_vol,
                setup=setup,
            ):
                continue

            entry_px = max(setup.orb_high, bar["open"])
            if self._execute_orb_buy(c6, entry_px, setup.orb_high, day_idx):
                trade_date = pd.Timestamp(self.bdays[day_idx]).date()
                self.pass_logs.append(
                    f"{trade_date} {c6} [ORB BUY] @{entry_px:,.0f} · ORB고 {setup.orb_high:,.0f}"
                )

    def _process_orb_exits(self, day_idx: int, *, force_eod: bool = False) -> None:
        for code in list(self.positions.keys()):
            pos = self.positions.get(code)
            if pos is None:
                continue
            bar = self._get_daily_bar(code, day_idx)
            if bar is None:
                continue

            decision = evaluate_orb_exit(
                entry_price=pos.entry_price,
                open_px=bar["open"],
                high_px=bar["high"],
                low_px=bar["low"],
                close_px=bar["close"],
                partial_tp_done=pos.partial_tp_done,
                risk_free=pos.risk_free,
                breakeven_stop=pos.breakeven_stop,
                force_eod=force_eod,
            )
            if decision is None:
                continue

            if decision.sell_ratio < 1.0:
                self._execute_partial_sell(
                    code, decision.exit_price, decision.exit_type, day_idx,
                    sell_ratio=decision.sell_ratio,
                )
            else:
                self._execute_sell(code, decision.exit_price, decision.exit_type, day_idx)

    def _force_time_stop_all(self, day_idx: int) -> None:
        """15:20 — 잔여 포지션 전량 시장가 청산 (종가 proxy)."""
        if not self.positions:
            return
        self._process_orb_exits(day_idx, force_eod=True)
        for code in list(self.positions.keys()):
            bar = self._get_daily_bar(code, day_idx)
            if bar is None:
                continue
            self._execute_sell(code, float(bar["close"]), "TIME_STOP_1520", day_idx)

    def _verify_no_overnight(self, day_idx: int) -> None:
        if self.positions:
            trade_date = pd.Timestamp(self.bdays[day_idx]).strftime("%Y-%m-%d")
            codes = ",".join(sorted(self.positions.keys()))
            self.overnight_violations.append(f"{trade_date}: {codes}")

    def evaluate_daily_trades_v11(self, day_idx: int) -> None:
        self.had_trades_today = False
        candidates = self._prev_day_turnover_ranked(day_idx)
        self._process_orb_entries(day_idx, candidates)
        self._process_orb_exits(day_idx)
        self._force_time_stop_all(day_idx)
        self._verify_no_overnight(day_idx)

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
        has_pnl = self.had_trades_today or self.has_realized_pnl_today
        result = self.capital_buffer.rebalance(total_equity, has_realized_pnl=has_pnl)
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
        self.had_trades_today = False
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
            self.evaluate_daily_trades_v11(day_idx)
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

        if self.overnight_violations:
            for v in self.overnight_violations:
                self.pass_logs.append(f"OVERNIGHT VIOLATION {v}")

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

    def overnight_stats(self) -> dict[str, Any]:
        return {
            "overnight_count": len(self.overnight_violations),
            "overnight_violations": list(self.overnight_violations),
        }
