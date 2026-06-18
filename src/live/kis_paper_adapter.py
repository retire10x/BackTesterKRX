"""
v11.2 KIS 모의투자 브로커 어댑터.

PaperTradingBroker 와 동일한 인터페이스를 유지하면서,
주문·잔고 SSOT는 v10 LiveAccountGateway(KIS 모의 서버)를 사용한다.
투자 예산은 initial_capital(기본 200만 원) 내에서만 통제한다.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from src.live.live_account import LiveAccountGateway
from src.live.paper_trading_broker import (
    DEFAULT_INITIAL_CASH,
    FEE_TAX_RATIO,
    MAX_SLOTS,
    SLOT_BUDGET,
    FillRecord,
    PaperPosition,
)

logger = logging.getLogger("KisPaperAdapter")
KST = ZoneInfo("Asia/Seoul")
BALANCE_CACHE_SEC = 5.0


class KisPaperBrokerAdapter:
    """v11 ORB 전략 ↔ v10 KIS Gateway 어댑터."""

    def __init__(
        self,
        gateway: LiveAccountGateway,
        *,
        initial_capital: float = DEFAULT_INITIAL_CASH,
        max_slots: int = MAX_SLOTS,
        slot_budget: float = SLOT_BUDGET,
        fee_ratio: float = FEE_TAX_RATIO,
        meta_path: str | Path | None = None,
        project_root: str | Path | None = None,
    ):
        self.gateway = gateway
        self.initial_capital = float(initial_capital)
        self.max_slots = int(max_slots)
        self.slot_budget = float(slot_budget)
        self.fee_ratio = float(fee_ratio)
        self._cash_adjustment = 0.0

        root = Path(project_root or Path(__file__).resolve().parents[2])
        self.meta_path = Path(meta_path) if meta_path else root / "config" / "v11_kis_position_meta.json"

        self.positions: dict[str, PaperPosition] = {}
        self.fills: list[FillRecord] = []
        self.realized_pnl_today = 0.0
        self.had_trades_today = False
        self._trade_id = 0
        self._last_prices: dict[str, float] = {}
        self._position_meta: dict[str, dict[str, Any]] = {}
        self._balance_cache: dict[str, Any] | None = None
        self._balance_cache_ts = 0.0

        self._load_position_meta()

    @property
    def cash(self) -> float:
        """200만 원 예산 내 남은 가용 금액 (EOD Safe Vault 반영)."""
        invested = sum(p.entry_price * p.qty for p in self.positions.values())
        return max(0.0, self.initial_capital - invested + self._cash_adjustment)

    @cash.setter
    def cash(self, value: float) -> None:
        invested = sum(p.entry_price * p.qty for p in self.positions.values())
        self._cash_adjustment = float(value) - (self.initial_capital - invested)

    @property
    def open_slot_count(self) -> int:
        return len(self.positions)

    @property
    def available_slots(self) -> int:
        return max(0, self.max_slots - self.open_slot_count)

    def _load_position_meta(self) -> None:
        if not self.meta_path.is_file():
            return
        try:
            with open(self.meta_path, encoding="utf-8") as fh:
                raw = json.load(fh)
            self._position_meta = {
                str(k).zfill(6): v for k, v in (raw.get("positions") or {}).items()
            }
            self._trade_id = int(raw.get("trade_id") or 0)
            self.realized_pnl_today = float(raw.get("realized_pnl_today") or 0)
            self.had_trades_today = bool(raw.get("had_trades_today"))
            self._cash_adjustment = float(raw.get("cash_adjustment") or 0)
        except Exception as exc:
            logger.warning("KIS 포지션 메타 로드 실패 — 초기화: %s", exc)

    def _persist_position_meta(self) -> None:
        os.makedirs(self.meta_path.parent, exist_ok=True)
        payload = {
            "trade_id": self._trade_id,
            "realized_pnl_today": self.realized_pnl_today,
            "had_trades_today": self.had_trades_today,
            "cash_adjustment": self._cash_adjustment,
            "positions": self._position_meta,
        }
        with open(self.meta_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")

    def _persist(self) -> None:
        self._persist_position_meta()

    def _get_balance(self, *, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if (
            not force
            and self._balance_cache is not None
            and now - self._balance_cache_ts < BALANCE_CACHE_SEC
        ):
            return self._balance_cache
        bal = self.gateway.get_inquire_balance()
        self._balance_cache = bal
        self._balance_cache_ts = now
        return bal

    def _total_invested(self) -> float:
        return sum(p.entry_price * p.qty for p in self.positions.values())

    def _total_unrealized_pnl(self, balance: dict[str, Any] | None = None) -> float:
        bal = balance or self._get_balance()
        pnl = 0.0
        for item in bal.get("positions") or []:
            entry = float(item.get("entry_price") or 0)
            current = float(item.get("current_price") or entry)
            qty = int(item.get("quantity") or 0)
            if qty > 0 and entry > 0:
                pnl += (current - entry) * qty
        return pnl

    def get_available_budget(self) -> float:
        """200만 원 한도 내 매수 가능 금액."""
        return max(0.0, self.initial_capital - self._total_invested() + self._cash_adjustment)

    def get_available_cash(self) -> float:
        """200만 원 - KIS 계좌 내 현재 매입금액 (Task 지침 alias)."""
        return self.get_available_budget()

    def update_quote(self, code: str, price: float) -> None:
        c6 = str(code).zfill(6)
        if price > 0:
            self._last_prices[c6] = float(price)

    def total_equity(self) -> float:
        """시작자금 200만 원 + KIS 평가손익 + 당일 실현손익."""
        bal = self._get_balance()
        unrealized = self._total_unrealized_pnl(bal)
        return self.initial_capital + unrealized + self.realized_pnl_today

    def sync_positions(self) -> dict[str, Any]:
        """08:50 장전 KIS 잔고·포지션 동기화."""
        logger.info("🔄 KIS 모의계좌 잔고 및 포지션 동기화 시작")
        bal = self._get_balance(force=True)
        new_positions: dict[str, PaperPosition] = {}
        for item in bal.get("positions") or []:
            code = str(item.get("symbol", "")).zfill(6)
            qty = int(item.get("quantity") or 0)
            if qty <= 0:
                continue
            entry = float(item.get("entry_price") or 0)
            meta = self._position_meta.get(code, {})
            new_positions[code] = PaperPosition(
                code=code,
                qty=qty,
                entry_price=entry,
                invest_amount=entry * qty,
                buy_cost_paid=entry * qty * self.fee_ratio,
                trade_id=int(meta.get("trade_id") or 0),
                orb_high=float(meta.get("orb_high") or 0),
                partial_tp_done=bool(meta.get("partial_tp_done")),
                risk_free=bool(meta.get("risk_free")),
                breakeven_stop=float(meta.get("breakeven_stop") or 0),
                entry_time=str(meta.get("entry_time") or ""),
            )
        self.positions = new_positions
        self._persist_position_meta()
        logger.info(
            "✅ KIS 동기화 완료 — 보유 %d종 · 총자산(200만 기준) %s원",
            len(self.positions),
            f"{self.total_equity():,.0f}",
        )
        return {
            "synced_count": len(self.positions),
            "total_asset": bal.get("total_asset"),
            "available_cash": bal.get("available_cash"),
        }

    def _kis_order_ok(self, response: Any) -> bool:
        if isinstance(response, dict):
            return str(response.get("rt_cd", "1")) == "0"
        return bool(response)

    def _save_meta_from_position(self, pos: PaperPosition) -> None:
        self._position_meta[pos.code] = {
            "trade_id": pos.trade_id,
            "orb_high": pos.orb_high,
            "partial_tp_done": pos.partial_tp_done,
            "risk_free": pos.risk_free,
            "breakeven_stop": pos.breakeven_stop,
            "entry_time": pos.entry_time,
        }

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
        if c6 in self.positions or self.available_slots <= 0:
            return None

        bet = float(budget if budget is not None else self.slot_budget)
        bet = min(bet, self.get_available_budget())
        qty = int(bet // px)
        if qty < 1:
            logger.warning("[%s] 매수 수량 0 — 예산 부족 (bet=%.0f, px=%.0f)", c6, bet, px)
            return None

        gross = px * qty
        fee = gross * self.fee_ratio
        if gross + fee > self.get_available_budget() + 1:
            logger.warning("[%s] 200만 원 예산 초과 — 매수 차단", c6)
            return None

        logger.info("👉 KIS 모의투자 시장가 매수 전송: %s / %d주", c6, qty)
        response = self.gateway.place_market_order(c6, qty)
        if not self._kis_order_ok(response):
            logger.error("❌ KIS 매수 거부: %s", response)
            return None

        self.sync_positions()
        pos = self.positions.get(c6)
        if pos is None:
            pos = PaperPosition(
                code=c6,
                qty=qty,
                entry_price=px,
                invest_amount=gross,
                buy_cost_paid=fee,
                trade_id=0,
                orb_high=orb_high,
                entry_time=datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
            )
            self.positions[c6] = pos

        self._trade_id += 1
        pos.trade_id = self._trade_id
        pos.orb_high = orb_high
        if not pos.entry_time:
            pos.entry_time = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        self._save_meta_from_position(pos)

        now_s = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        rec = FillRecord(
            trade_id=self._trade_id,
            code=c6,
            side="BUY",
            qty=qty,
            price=px,
            gross=gross,
            fee=fee,
            slippage_cost=0.0,
            net_cash_delta=-(gross + fee),
            timestamp=now_s,
            note=note,
        )
        self.fills.append(rec)
        self.had_trades_today = True
        self._persist_position_meta()
        return rec

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

        px = float(price or self._last_prices.get(c6, pos.entry_price))
        if px <= 0:
            return None

        sell_qty = qty if qty is not None else max(1, int(pos.qty * sell_ratio))
        sell_qty = min(sell_qty, pos.qty)
        if sell_qty < 1:
            return None

        entry_price = pos.entry_price
        entry_time = pos.entry_time
        cost_basis_frac = sell_qty / pos.qty
        cost_basis = (pos.invest_amount + pos.buy_cost_paid) * cost_basis_frac

        logger.info("👉 KIS 모의투자 시장가 매도 전송: %s / %d주", c6, sell_qty)
        response = self.gateway.sell_all(c6, qty=sell_qty, exit_type=note or "ORB_EXIT")
        if not self._kis_order_ok(response):
            logger.error("❌ KIS 매도 거부: %s", response)
            return None

        gross = px * sell_qty
        fee = gross * self.fee_ratio
        proceeds = gross - fee
        pnl = proceeds - cost_basis

        self.realized_pnl_today += pnl
        self._trade_id += 1
        now_s = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        rec = FillRecord(
            trade_id=self._trade_id,
            code=c6,
            side="SELL",
            qty=sell_qty,
            price=px,
            gross=gross,
            fee=fee,
            slippage_cost=0.0,
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
            del self.positions[c6]
            self._position_meta.pop(c6, None)
        else:
            frac = 1.0 - cost_basis_frac
            pos.qty -= sell_qty
            pos.invest_amount *= frac
            pos.buy_cost_paid *= frac
            self._save_meta_from_position(pos)

        self._balance_cache = None
        self.sync_positions()
        self._persist_position_meta()
        return rec

    def reset_day_flags(self) -> None:
        self.realized_pnl_today = 0.0
        self.had_trades_today = False
        self._persist_position_meta()
