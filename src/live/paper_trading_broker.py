"""
v11.2 모의투자(Paper Trading) 브로커 엔진.

초기 자본 200만 원 · 슬리피지 0.1% · 수수료+세금 0.20%.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal
from zoneinfo import ZoneInfo

logger = logging.getLogger("PaperBroker")
KST = ZoneInfo("Asia/Seoul")

DEFAULT_INITIAL_CASH = 2_000_000.0
FEE_TAX_RATIO = 0.0020
SLIPPAGE_RATIO = 0.0010
MAX_SLOTS = 4
SLOT_BUDGET = 500_000.0

Side = Literal["BUY", "SELL"]


@dataclass
class PaperPosition:
    code: str
    qty: int
    entry_price: float
    invest_amount: float
    buy_cost_paid: float
    trade_id: int
    orb_high: float = 0.0
    partial_tp_done: bool = False
    risk_free: bool = False
    breakeven_stop: float = 0.0
    entry_time: str = ""


@dataclass
class FillRecord:
    trade_id: int
    code: str
    side: Side
    qty: int
    price: float
    gross: float
    fee: float
    slippage_cost: float
    net_cash_delta: float
    timestamp: str
    note: str = ""
    entry_price: float = 0.0
    entry_time: str = ""
    pnl_rate: float = 0.0


@dataclass
class PaperAccountSnapshot:
    cash: float
    positions: dict[str, PaperPosition]
    total_equity: float
    realized_pnl_today: float
    had_trades_today: bool


class PaperTradingBroker:
    """가상 계좌 — 최신 1분봉 종가 기준 즉시 체결."""

    def __init__(
        self,
        *,
        initial_cash: float = DEFAULT_INITIAL_CASH,
        fee_ratio: float = FEE_TAX_RATIO,
        slippage_ratio: float = SLIPPAGE_RATIO,
        max_slots: int = MAX_SLOTS,
        slot_budget: float = SLOT_BUDGET,
        state_path: str | Path | None = None,
        on_fill: Callable[[FillRecord, PaperPosition | None], None] | None = None,
    ):
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.fee_ratio = float(fee_ratio)
        self.slippage_ratio = float(slippage_ratio)
        self.max_slots = int(max_slots)
        self.slot_budget = float(slot_budget)
        self.state_path = Path(state_path) if state_path else None
        self.positions: dict[str, PaperPosition] = {}
        self.fills: list[FillRecord] = []
        self.realized_pnl_today = 0.0
        self.had_trades_today = False
        self._trade_id = 0
        self._last_prices: dict[str, float] = {}
        self.on_fill = on_fill

        if self.state_path and self.state_path.is_file():
            self._load_state()

    @property
    def open_slot_count(self) -> int:
        return len(self.positions)

    @property
    def available_slots(self) -> int:
        return max(0, self.max_slots - self.open_slot_count)

    def update_quote(self, code: str, price: float) -> None:
        c6 = str(code).zfill(6)
        if price > 0:
            self._last_prices[c6] = float(price)

    def total_equity(self) -> float:
        eq = self.cash
        for code, pos in self.positions.items():
            mark = self._last_prices.get(code, pos.entry_price)
            eq += pos.qty * mark
        return eq

    def snapshot(self) -> PaperAccountSnapshot:
        return PaperAccountSnapshot(
            cash=self.cash,
            positions=dict(self.positions),
            total_equity=self.total_equity(),
            realized_pnl_today=self.realized_pnl_today,
            had_trades_today=self.had_trades_today,
        )

    def _apply_buy(self, code: str, qty: int, raw_price: float, *, orb_high: float = 0.0, note: str = "") -> FillRecord | None:
        c6 = str(code).zfill(6)
        if qty < 1 or raw_price <= 0:
            return None
        if c6 in self.positions or self.available_slots <= 0:
            return None

        fill_px = raw_price * (1.0 + self.slippage_ratio)
        gross = fill_px * qty
        fee = gross * self.fee_ratio
        total_debit = gross + fee
        if self.cash < total_debit:
            return None

        self._trade_id += 1
        self.cash -= total_debit
        slip_cost = (fill_px - raw_price) * qty
        now_s = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        self.positions[c6] = PaperPosition(
            code=c6,
            qty=qty,
            entry_price=fill_px,
            invest_amount=gross,
            buy_cost_paid=fee,
            trade_id=self._trade_id,
            orb_high=orb_high,
            entry_time=now_s,
        )
        rec = FillRecord(
            trade_id=self._trade_id,
            code=c6,
            side="BUY",
            qty=qty,
            price=fill_px,
            gross=gross,
            fee=fee,
            slippage_cost=slip_cost,
            net_cash_delta=-total_debit,
            timestamp=now_s,
            note=note,
        )
        self.fills.append(rec)
        self.had_trades_today = True
        self._persist()
        if self.on_fill:
            self.on_fill(rec, self.positions.get(c6))
        return rec

    def place_market_buy(
        self,
        code: str,
        *,
        price: float | None = None,
        budget: float | None = None,
        orb_high: float = 0.0,
        note: str = "",
    ) -> FillRecord | None:
        c6 = str(code).zfill(6)
        px = float(price or self._last_prices.get(c6, 0))
        if px <= 0:
            return None
        bet = float(budget if budget is not None else self.slot_budget)
        qty = int(bet // px)
        if qty < 1:
            return None
        return self._apply_buy(c6, qty, px, orb_high=orb_high, note=note)

    def place_market_sell(
        self,
        code: str,
        *,
        price: float | None = None,
        qty: int | None = None,
        sell_ratio: float = 1.0,
        note: str = "",
    ) -> FillRecord | None:
        c6 = str(code).zfill(6)
        pos = self.positions.get(c6)
        if pos is None:
            return None
        px = float(price or self._last_prices.get(c6, 0))
        if px <= 0:
            return None

        sell_qty = qty if qty is not None else max(1, int(pos.qty * sell_ratio))
        sell_qty = min(sell_qty, pos.qty)
        if sell_qty < 1:
            return None

        fill_px = px * (1.0 - self.slippage_ratio)
        gross = fill_px * sell_qty
        fee = gross * self.fee_ratio
        proceeds = gross - fee
        slip_cost = (px - fill_px) * sell_qty

        cost_basis_frac = sell_qty / pos.qty
        cost_basis = (pos.invest_amount + pos.buy_cost_paid) * cost_basis_frac
        pnl = proceeds - cost_basis
        entry_price = pos.entry_price
        entry_time = pos.entry_time
        self.realized_pnl_today += pnl
        self.cash += proceeds

        now_s = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        self._trade_id += 1
        rec = FillRecord(
            trade_id=self._trade_id,
            code=c6,
            side="SELL",
            qty=sell_qty,
            price=fill_px,
            gross=gross,
            fee=fee,
            slippage_cost=slip_cost,
            net_cash_delta=proceeds,
            timestamp=now_s,
            note=note,
            entry_price=entry_price,
            entry_time=entry_time,
            pnl_rate=pnl / cost_basis if cost_basis > 0 else 0.0,
        )
        self.fills.append(rec)
        self.had_trades_today = True

        if sell_qty >= pos.qty:
            closed_pos = pos
            del self.positions[c6]
        else:
            closed_pos = None
            frac = 1.0 - cost_basis_frac
            pos.qty -= sell_qty
            pos.invest_amount *= frac
            pos.buy_cost_paid *= frac

        self._persist()
        if self.on_fill:
            self.on_fill(rec, closed_pos)
        return rec

    def reset_day_flags(self) -> None:
        self.realized_pnl_today = 0.0
        self.had_trades_today = False

    def _persist(self) -> None:
        if not self.state_path:
            return
        os.makedirs(self.state_path.parent, exist_ok=True)
        payload = {
            "cash": self.cash,
            "initial_cash": self.initial_cash,
            "trade_id": self._trade_id,
            "realized_pnl_today": self.realized_pnl_today,
            "had_trades_today": self.had_trades_today,
            "positions": {k: asdict(v) for k, v in self.positions.items()},
            "last_prices": self._last_prices,
        }
        with open(self.state_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")

    def _load_state(self) -> None:
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                raw = json.load(fh)
            self.cash = float(raw.get("cash", self.initial_cash))
            self._trade_id = int(raw.get("trade_id") or 0)
            self.realized_pnl_today = float(raw.get("realized_pnl_today") or 0)
            self.had_trades_today = bool(raw.get("had_trades_today"))
            self._last_prices = {str(k).zfill(6): float(v) for k, v in (raw.get("last_prices") or {}).items()}
            for k, v in (raw.get("positions") or {}).items():
                self.positions[str(k).zfill(6)] = PaperPosition(**v)
        except Exception as exc:
            logger.warning("Paper broker 상태 로드 실패 — 초기화: %s", exc)
