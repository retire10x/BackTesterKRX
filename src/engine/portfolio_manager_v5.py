"""
v5.x 20일선 변곡점 스나이퍼 포트폴리오 엔진.

진입: 20일선 하부 바닥권에서 20영업일 전 종가 저항 돌파(변곡) 시 종가 매수.
청산: v5_0/v5_1 MA20 종가 이탈 · v5_2 고정 익절/손절/타임스탑(Hit & Run).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from src.engine.portfolio_manager import (
    PortfolioResult,
    TRADES_DETAIL_COLUMNS,
)
from src.engine.portfolio_manager import PortfolioManager as _PortfolioManagerV4
from src.v5_config import V5Config, load_v5_config


@dataclass
class V5OpenPosition:
    code: str
    entry_date: pd.Timestamp
    entry_price: float
    qty: int
    invest_amount: float
    buy_cost_paid: float
    trade_id: int
    slot_budget_at_entry: float
    hold_days: int = 0


class PortfolioManagerV5:
    """v5 변곡점 스나이퍼 — 변곡 진입, MA20 추세청산 또는 Hit & Run 고정 손익비."""

    def __init__(
        self,
        day_frames: list[pd.DataFrame],
        bdays: pd.DatetimeIndex,
        *,
        start_date: str,
        end_date: str,
        v5_config: V5Config | None = None,
        target_universe: frozenset[str] | None = None,
        starting_cash: float | None = None,
        period_end_date: str | None = None,
        trade_id_offset: int = 0,
    ):
        cfg = v5_config if v5_config is not None else load_v5_config()
        self.cfg = cfg
        env = cfg.environment
        port = cfg.portfolio
        strat = cfg.strategy
        costs = port.trading_costs

        self.day_frames = day_frames
        self.bdays = pd.DatetimeIndex(bdays).normalize()
        self.start_date = pd.Timestamp(str(start_date).strip()[:10]).normalize()
        self.end_date = pd.Timestamp(str(end_date).strip()[:10]).normalize()

        self.initial_equity = float(
            starting_cash if starting_cash is not None else env.initial_cash
        )
        self.max_slots = int(port.max_slots)
        self.fixed_amount = float(port.slot_invest_amount)
        self.buy_cost_ratio = float(costs.buy_cost_ratio)
        self.sell_cost_ratio = float(costs.sell_cost_ratio)

        self.lookback_window = int(strat.lookback_window)
        self.use_hit_and_run_exit = strat.use_hit_and_run_exit
        if self.use_hit_and_run_exit:
            self.stop_loss_ratio = float(strat.stop_loss_ratio)
            self.target_profit_ratio = float(strat.target_profit_ratio)
            self.max_hold_days = int(strat.max_hold_days)
            self.exit_ma_window = int(strat.exit_ma_window or strat.lookback_window)
        else:
            self.exit_ma_window = int(strat.exit_ma_window or strat.lookback_window)
            self.stop_loss_ratio = 0.0
            self.target_profit_ratio = 0.0
            self.max_hold_days = 0
        self.use_price_filter = strat.price_ceiling is not None and strat.price_floor is not None
        self.price_ceiling = float(strat.price_ceiling) if strat.price_ceiling is not None else float("inf")
        self.price_floor = float(strat.price_floor) if strat.price_floor is not None else 0.0
        self.strategy_name = str(strat.strategy_name)
        self.target_universe = target_universe
        macro = strat.macro_trend_filter
        self.macro_trend_enabled = bool(macro.enabled) if macro is not None else False
        if macro is not None and macro.enabled:
            self.macro_ma_window = int(macro.ma_window) if macro.ma_window is not None else 0
            self.macro_price_above_ma = (
                int(macro.check_prices_above_ma) if macro.check_prices_above_ma is not None else 0
            )
            self.macro_dual_slope_windows = tuple(int(w) for w in macro.dual_slope_alignment)
        else:
            self.macro_ma_window = 0
            self.macro_price_above_ma = 0
            self.macro_dual_slope_windows = ()

        self.cash = float(self.initial_equity)
        self.positions: dict[str, V5OpenPosition] = {}
        self.stock_history: dict[str, pd.DataFrame] = {}

        self.trade_rows: list[dict[str, Any]] = []
        self.trade_detail_rows: list[dict[str, Any]] = []
        self.equity_rows: list[dict[str, Any]] = []
        self.pass_logs: list[str] = []
        self._trade_id_counter = int(trade_id_offset)
        self.period_end_date = (
            pd.Timestamp(str(period_end_date).strip()[:10]).normalize()
            if period_end_date
            else None
        )

        self._sim_start_idx = int(self.bdays.get_indexer([self.start_date], method="bfill")[0])
        self._sim_end_idx = int(self.bdays.get_indexer([self.end_date], method="ffill")[0])
        if self._sim_start_idx < 0 or self._sim_end_idx < 0:
            raise ValueError("시뮬레이션 기간이 벌크 데이터 범위 밖입니다.")

    @property
    def open_slot_count(self) -> int:
        return len(self.positions)

    @property
    def available_slots(self) -> int:
        return max(0, self.max_slots - self.open_slot_count)

    def _history_as_of(self, code: str, day_idx: int) -> pd.DataFrame | None:
        c6 = str(code).zfill(6)
        hist = self.stock_history.get(c6)
        if hist is None or hist.empty:
            return None
        dt = pd.Timestamp(self.bdays[day_idx]).normalize()
        sub = hist.loc[hist.index <= dt]
        if sub.empty:
            return None
        return sub

    def _append_history_bar(self, code: str, day_idx: int) -> None:
        c6 = str(code).zfill(6)
        day_frame = self.day_frames[day_idx]
        if c6 not in day_frame.index:
            return
        row = day_frame.loc[c6]
        bar = {
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row["Volume"]),
        }
        dt = pd.Timestamp(self.bdays[day_idx]).normalize()
        if c6 not in self.stock_history:
            self.stock_history[c6] = pd.DataFrame([bar], index=[dt])
            return
        hist = self.stock_history[c6]
        if dt in hist.index:
            hist.loc[dt] = bar
        else:
            self.stock_history[c6] = pd.concat([hist, pd.DataFrame([bar], index=[dt])]).sort_index()

    def _macro_min_history_bars(self) -> int:
        need = self.lookback_window + 1
        if not self.macro_trend_enabled:
            return need
        if self.macro_ma_window > 0:
            need = max(need, self.macro_ma_window)
        if self.macro_price_above_ma > 0:
            need = max(need, self.macro_price_above_ma)
        for w in self.macro_dual_slope_windows:
            need = max(need, w + 1)
        return need

    def _passes_macro_trend_filter(self, close_s: pd.Series, today_close: float) -> bool:
        """v5.4 종가>MA · v5.5 듀얼 기울기(MA오늘>MA어제) + 종가>MA120."""
        if not self.macro_trend_enabled:
            return True

        for w in self.macro_dual_slope_windows:
            ma_s = close_s.rolling(window=w).mean()
            ma_today = float(ma_s.iloc[-1])
            ma_yesterday = float(ma_s.iloc[-2])
            if not np.isfinite(ma_today) or not np.isfinite(ma_yesterday):
                return False
            if ma_today <= ma_yesterday:
                return False

        if self.macro_price_above_ma > 0:
            ma_floor = float(close_s.rolling(window=self.macro_price_above_ma).mean().iloc[-1])
            if not np.isfinite(ma_floor) or today_close <= ma_floor:
                return False
        elif self.macro_ma_window > 0:
            ma_macro = float(close_s.rolling(window=self.macro_ma_window).mean().iloc[-1])
            if not np.isfinite(ma_macro) or today_close <= ma_macro:
                return False
        return True

    def _is_ma_inflection_turning_up(self, ohlcv_df: pd.DataFrame) -> bool:
        """20일선 변곡 + (선택) 장기 대세·듀얼 우상향 필터."""
        window = self.lookback_window
        if len(ohlcv_df) < self._macro_min_history_bars():
            return False

        close_s = pd.to_numeric(ohlcv_df["close"], errors="coerce")
        today_close = float(close_s.iloc[-1])
        if not np.isfinite(today_close) or today_close <= 0:
            return False
        if self.use_price_filter and not (self.price_floor <= today_close <= self.price_ceiling):
            return False

        past_20_close = float(close_s.iloc[-(window + 1)])
        if not np.isfinite(past_20_close):
            return False

        ma_s = close_s.rolling(window=window).mean()
        yesterday_close = float(close_s.iloc[-2])
        yesterday_ma = float(ma_s.iloc[-2])
        if not np.isfinite(yesterday_close) or not np.isfinite(yesterday_ma):
            return False

        if not (yesterday_close <= yesterday_ma and today_close > past_20_close):
            return False

        return self._passes_macro_trend_filter(close_s, today_close)

    def _should_trend_exit_ma20(self, ohlcv_df: pd.DataFrame) -> bool:
        window = self.exit_ma_window
        if len(ohlcv_df) < window:
            return False
        close_s = pd.to_numeric(ohlcv_df["close"], errors="coerce")
        today_close = float(close_s.iloc[-1])
        ma_exit = float(close_s.rolling(window=window).mean().iloc[-1])
        if not np.isfinite(today_close) or not np.isfinite(ma_exit):
            return False
        return today_close < ma_exit

    def _evaluate_hit_and_run_exit(
        self,
        *,
        entry_price: float,
        high: float,
        low: float,
        close: float,
        hold_days: int,
    ) -> tuple[float, str] | None:
        """손절(-3%) → 익절(+6%) → max_hold_days 타임스탑(종가). 장중 저가/고가 반영."""
        target_px = entry_price * (1.0 + self.target_profit_ratio)
        stop_px = entry_price * (1.0 - self.stop_loss_ratio)

        if low <= stop_px:
            return stop_px, "STOP_LOSS"
        if high >= target_px:
            return target_px, "TAKE_PROFIT"
        if hold_days >= self.max_hold_days:
            return close, "TIME_STOP"
        return None

    def _position_market_value(self, code: str, day_idx: int) -> float:
        pos = self.positions.get(code)
        if pos is None:
            return 0.0
        ohlcv = self._get_daily_bar(code, day_idx)
        if ohlcv is None:
            return pos.invest_amount
        close_px = float(ohlcv["close"])
        if not np.isfinite(close_px) or close_px <= 0:
            return pos.invest_amount
        return pos.qty * close_px

    def _get_daily_bar(self, code: str, day_idx: int) -> dict[str, float] | None:
        c6 = str(code).zfill(6)
        day_frame = self.day_frames[day_idx]
        if c6 not in day_frame.index:
            return None
        row = day_frame.loc[c6]
        return {
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row["Volume"]),
        }

    def _total_equity(self, day_idx: int) -> float:
        mv = sum(self._position_market_value(code, day_idx) for code in self.positions)
        return self.cash + mv

    def _append_trade_detail(
        self,
        *,
        side: str,
        day_idx: int,
        code: str,
        trade_id: int,
        entry_date: str | None = None,
        entry_price: float | None = None,
        exit_price: float | None = None,
        qty: float | None = None,
        invest_amount: float | None = None,
        proceeds: float | None = None,
        pnl_amount: float | None = None,
        pnl_rate: float | None = None,
        exit_type: str | None = None,
    ) -> None:
        timestamp = pd.Timestamp(self.bdays[day_idx]).normalize().strftime("%Y-%m-%d")
        ep = float(entry_price) if entry_price is not None and np.isfinite(entry_price) else np.nan
        q = float(qty) if qty is not None and np.isfinite(qty) else np.nan
        inv = float(invest_amount) if invest_amount is not None and np.isfinite(invest_amount) else np.nan

        self.trade_detail_rows.append({
            "trade_id": int(trade_id),
            "side": str(side).upper(),
            "timestamp": timestamp,
            "code": str(code).zfill(6),
            "stage": 1,
            "entry_date": entry_date or "",
            "entry_price": ep,
            "exit_price": float(exit_price) if exit_price is not None and np.isfinite(exit_price) else np.nan,
            "qty": q,
            "invest_amount": inv,
            "proceeds": float(proceeds) if proceeds is not None and np.isfinite(proceeds) else np.nan,
            "pnl_amount": float(pnl_amount) if pnl_amount is not None and np.isfinite(pnl_amount) else np.nan,
            "pnl_rate": float(pnl_rate) if pnl_rate is not None and np.isfinite(pnl_rate) else np.nan,
            "exit_type": exit_type or "",
            "cash_after": float(self.cash),
            "total_equity_after": float(self._total_equity(day_idx)),
            "open_slots_after": int(self.open_slot_count),
            "slot_budget_at_entry": float(self.fixed_amount),
            "alloc_ratio": (
                float(invest_amount) / self.fixed_amount
                if invest_amount is not None and self.fixed_amount > 0 and np.isfinite(invest_amount)
                else np.nan
            ),
        })

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

    def _execute_buy(self, code: str, entry_price: float, day_idx: int) -> bool:
        c6 = str(code).zfill(6)
        if c6 in self.positions or self.available_slots <= 0:
            return False
        if not np.isfinite(entry_price) or entry_price <= 0:
            return False

        qty = int(self.fixed_amount // entry_price)
        if qty < 1:
            self.pass_logs.append(
                f"{pd.Timestamp(self.bdays[day_idx]).date()} {c6} Pass — 정수 주수 0 "
                f"(가격 {entry_price:,.0f} / 예산 {self.fixed_amount:,.0f})"
            )
            return False

        invest_amount = float(qty * entry_price)
        buy_cost_paid = invest_amount * self.buy_cost_ratio
        total_debit = invest_amount + buy_cost_paid
        if self.cash < total_debit:
            self.pass_logs.append(
                f"{pd.Timestamp(self.bdays[day_idx]).date()} {c6} Pass — 현금 부족 "
                f"(필요 {total_debit:,.0f} / 보유 {self.cash:,.0f})"
            )
            return False

        self._trade_id_counter += 1
        trade_id = self._trade_id_counter
        trade_date = pd.Timestamp(self.bdays[day_idx]).normalize()
        self.cash -= total_debit
        self.positions[c6] = V5OpenPosition(
            code=c6,
            entry_date=trade_date,
            entry_price=entry_price,
            qty=qty,
            invest_amount=invest_amount,
            buy_cost_paid=buy_cost_paid,
            trade_id=trade_id,
            slot_budget_at_entry=self.fixed_amount,
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
            exit_type=(
                "ENTRY_MA_INFLECTION_HIT_RUN"
                if self.use_hit_and_run_exit
                else "ENTRY_MA_INFLECTION_20D"
            ),
        )
        return True

    def _period_reset_all(self, day_idx: int) -> None:
        """구간 종료일 미청산 포지션 전량 종가 청산 (PERIOD_RESET)."""
        for code in list(self.positions.keys()):
            bar = self._get_daily_bar(code, day_idx)
            if bar is None:
                continue
            self._execute_sell(code, float(bar["close"]), "PERIOD_RESET", day_idx)

    def _process_exits(self, day_idx: int) -> None:
        for code in list(self.positions.keys()):
            pos = self.positions[code]
            self._append_history_bar(code, day_idx)
            pos.hold_days += 1

            if self.use_hit_and_run_exit:
                bar = self._get_daily_bar(code, day_idx)
                if bar is None:
                    continue
                exit_info = self._evaluate_hit_and_run_exit(
                    entry_price=pos.entry_price,
                    high=bar["high"],
                    low=bar["low"],
                    close=bar["close"],
                    hold_days=pos.hold_days,
                )
                if exit_info is None:
                    continue
                exit_price, exit_type = exit_info
                self._execute_sell(code, exit_price, exit_type, day_idx)
                continue

            hist = self._history_as_of(code, day_idx)
            if hist is None:
                continue
            if self._should_trend_exit_ma20(hist):
                exit_price = float(hist.iloc[-1]["close"])
                self._execute_sell(code, exit_price, "TREND_EXIT_MA20", day_idx)

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
            key = index_by_c6[c6]
            close_px = float(day_frame.loc[key, "Close"])
            vol = float(day_frame.loc[key, "Volume"])
            if not np.isfinite(close_px) or not np.isfinite(vol):
                continue
            if self.use_price_filter and not (self.price_floor <= close_px <= self.price_ceiling):
                continue
            rows.append((c6, close_px * vol))
        rows.sort(key=lambda x: x[1], reverse=True)
        return [c for c, _ in rows]

    def _process_entries(self, day_idx: int, candidate_codes: list[str]) -> None:
        if self.available_slots <= 0 or self.cash <= 0:
            return
        for code in candidate_codes:
            if self.available_slots <= 0 or self.cash <= 0:
                break
            c6 = str(code).zfill(6)
            if c6 in self.positions:
                continue
            self._append_history_bar(c6, day_idx)
            hist = self._history_as_of(c6, day_idx)
            if hist is None:
                continue
            if not self._is_ma_inflection_turning_up(hist):
                continue
            entry_price = float(hist.iloc[-1]["close"])
            self._execute_buy(c6, entry_price, day_idx)

    def evaluate_daily_trades_v5(self, day_idx: int) -> None:
        """일자별 청산 후 변곡점 종가 진입. 구간 말일은 PERIOD_RESET 후 진입 없음."""
        trade_date = pd.Timestamp(self.bdays[day_idx]).normalize()
        is_period_end = (
            self.period_end_date is not None and trade_date == self.period_end_date
        )
        self._process_exits(day_idx)
        if is_period_end:
            self._period_reset_all(day_idx)
            return
        candidates = self._candidate_codes_ranked(day_idx)
        self._process_entries(day_idx, candidates)

    def run(self) -> PortfolioResult:
        for day_idx in range(self._sim_start_idx, self._sim_end_idx + 1):
            self.evaluate_daily_trades_v5(day_idx)
            trade_date = pd.Timestamp(self.bdays[day_idx]).normalize()
            total_equity = self._total_equity(day_idx)
            self.equity_rows.append({
                "date": trade_date.strftime("%Y-%m-%d"),
                "cash": self.cash,
                "positions_value": total_equity - self.cash,
                "total_equity": total_equity,
                "open_slots": self.open_slot_count,
            })

        equity_curve = pd.DataFrame(self.equity_rows)
        trades = pd.DataFrame(self.trade_rows)
        if self.trade_detail_rows:
            trades_detail = pd.DataFrame(self.trade_detail_rows)[TRADES_DETAIL_COLUMNS]
        else:
            trades_detail = pd.DataFrame(columns=TRADES_DETAIL_COLUMNS)
        metrics = _PortfolioManagerV4._compute_metrics(
            equity_curve, trades, self.initial_equity
        )
        return PortfolioResult(
            metrics=metrics,
            equity_curve=equity_curve,
            trades=trades,
            trades_detail=trades_detail,
            pass_logs=self.pass_logs,
        )
