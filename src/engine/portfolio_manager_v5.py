"""
v5.0 20일선 변곡점 스나이퍼 + MA20 이탈 추세 청산 포트폴리오 엔진.

전략 원칙: 20일선 하부 바닥권에서 20영업일 전 종가 저항 돌파(변곡) 시 종가 진입,
종가가 MA20을 하방 이탈하면 청산. Phase H/I·고정 손익비 로직 없음.
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


class PortfolioManagerV5:
    """v5.0 변곡점 스나이퍼 — 20일선 변곡 진입, MA20 종가 이탈 청산."""

    def __init__(
        self,
        day_frames: list[pd.DataFrame],
        bdays: pd.DatetimeIndex,
        *,
        start_date: str,
        end_date: str,
        v5_config: V5Config | None = None,
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

        self.initial_equity = float(env.initial_cash)
        self.max_slots = int(port.max_slots)
        self.fixed_amount = float(port.slot_invest_amount)
        self.buy_cost_ratio = float(costs.buy_cost_ratio)
        self.sell_cost_ratio = float(costs.sell_cost_ratio)

        self.lookback_window = int(strat.lookback_window)
        self.exit_ma_window = int(strat.exit_ma_window)
        self.price_ceiling = float(strat.price_ceiling)
        self.price_floor = float(strat.price_floor)
        self.strategy_name = str(strat.strategy_name)

        self.cash = float(self.initial_equity)
        self.positions: dict[str, V5OpenPosition] = {}
        self.stock_history: dict[str, pd.DataFrame] = {}

        self.trade_rows: list[dict[str, Any]] = []
        self.trade_detail_rows: list[dict[str, Any]] = []
        self.equity_rows: list[dict[str, Any]] = []
        self.pass_logs: list[str] = []
        self._trade_id_counter = 0

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

    def _is_ma_inflection_turning_up(self, ohlcv_df: pd.DataFrame) -> bool:
        """20일선 변곡: 어제 종가≤MA20(바닥권) + 오늘 종가>20영업일 전 종가."""
        window = self.lookback_window
        if len(ohlcv_df) < window + 1:
            return False

        close_s = pd.to_numeric(ohlcv_df["close"], errors="coerce")
        today_close = float(close_s.iloc[-1])
        if not np.isfinite(today_close) or today_close <= 0:
            return False
        if not (self.price_floor <= today_close <= self.price_ceiling):
            return False

        past_20_close = float(close_s.iloc[-(window + 1)])
        if not np.isfinite(past_20_close):
            return False

        ma_s = close_s.rolling(window=window).mean()
        yesterday_close = float(close_s.iloc[-2])
        yesterday_ma = float(ma_s.iloc[-2])
        if not np.isfinite(yesterday_close) or not np.isfinite(yesterday_ma):
            return False

        if yesterday_close <= yesterday_ma and today_close > past_20_close:
            return True
        return False

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
            exit_type="ENTRY_MA_INFLECTION_20D",
        )
        return True

    def _process_exits(self, day_idx: int) -> None:
        for code in list(self.positions.keys()):
            self._append_history_bar(code, day_idx)
            hist = self._history_as_of(code, day_idx)
            if hist is None:
                continue
            if self._should_trend_exit_ma20(hist):
                exit_price = float(hist.iloc[-1]["close"])
                self._execute_sell(code, exit_price, "TREND_EXIT_MA20", day_idx)

    def _candidate_codes_ranked(self, day_idx: int) -> list[str]:
        day_frame = self.day_frames[day_idx]
        rows: list[tuple[str, float]] = []
        for code in day_frame.index:
            c6 = str(code).zfill(6)
            if c6 in self.positions:
                continue
            close_px = float(day_frame.loc[code, "Close"])
            vol = float(day_frame.loc[code, "Volume"])
            if not np.isfinite(close_px) or not np.isfinite(vol):
                continue
            if not (self.price_floor <= close_px <= self.price_ceiling):
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
        """일자별 청산(MA 이탈) 후 변곡점 종가 진입."""
        self._process_exits(day_idx)
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
