"""
v6.30 연구 엔진 (A+B+D 통합) — 승률 개선 가설 검증 전용.

⚠️ v5.5.2 SSOT(portfolio_manager_v5)는 불가침. 본 모듈은 이를 상속해
   청산 로직만 오버라이드하는 연구용 샌드박스다.

레버 A — 대규모 표본: target_universe 확대(코스닥 전종목 거래대금 상위 N)는
          러너(run_v6_30_research)에서 주입. 본 엔진은 진입 후보 마스크만 지원.
레버 B — 익일 시가 갭다운 방어: 진입 익일(hold_days==1) 시가가 -gap_down_ratio
          이하로 출발하면 장중 손절 대기 없이 시가 즉시 청산.
레버 D — 부분 익절 + 본전 이동 + 트레일링:
          1단계: high ≥ entry×(1+partial_tp) → 물량 50% 익절, 잔량 손절가 본전 고정
          2단계: 잔량 저가가 본전 이탈 시 본전 청산
          3단계: high ≥ entry×(1+trail_arm) 돌파 후 최고점 대비 -trail_giveback
                 후퇴 시 잔량 전량 청산
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.engine.portfolio_manager_v5 import PortfolioManagerV5


@dataclass
class LeverConfig:
    """v6.30 레버 파라미터 (작업지시서 v6.30 기본값)."""
    # 레버 B
    gap_down_enabled: bool = True
    gap_down_ratio: float = 0.025          # 익일 시가 -2.5% 이하 → 즉시 청산
    # 레버 D
    partial_tp_enabled: bool = True
    partial_tp_ratio: float = 0.04         # +4% 부분 익절 발화
    partial_tp_fraction: float = 0.5       # 50% 청산
    breakeven_ratio: float = 0.0           # 잔량 손절가 = 본전(entry × (1+0))
    trail_arm_ratio: float = 0.08          # +8% 돌파 시 트레일링 가동
    trail_giveback_ratio: float = 0.02     # 최고점 대비 -2% 후퇴 시 청산


@dataclass
class _LeverState:
    is_partial_cleared: bool = False
    stop_loss_price: float = 0.0
    max_high: float = 0.0


class PortfolioManagerV630(PortfolioManagerV5):
    """v5.5.2 진입 로직 + 레버 B/D 청산. 부분청산 지원."""

    def __init__(self, *args, lever: LeverConfig | None = None, enable_prewarm: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.lever = lever if lever is not None else LeverConfig()
        self.enable_prewarm = enable_prewarm
        self.lever_state: dict[str, _LeverState] = {}

    # ── 워밍업: target_universe 히스토리 프리적재(MA120 보장) ──────
    def _prewarm_history(self) -> None:
        """시뮬 시작 전 target_universe 종목의 과거 봉을 stock_history에 적재.

        구간 분할 시 stock_history가 구간 시작부터만 누적되어 MA120 워밍업이
        불가능한 문제를 해결. look-ahead 아님(시뮬 시작 이전 데이터만 사용).
        """
        if self.target_universe is None:
            return
        need = self._macro_min_history_bars() + 2
        start = max(0, self._sim_start_idx - need)
        for di in range(start, self._sim_start_idx):
            for code in self.target_universe:
                self._append_history_bar(code, di)

    def run(self):
        if self.enable_prewarm:
            self._prewarm_history()
        return super().run()

    # ── 포지션 생애주기 — 레버 상태 동기화 ─────────────────────────
    def _execute_buy(self, code: str, entry_price: float, day_idx: int) -> bool:
        ok = super()._execute_buy(code, entry_price, day_idx)
        if ok:
            c6 = str(code).zfill(6)
            self.lever_state[c6] = _LeverState(
                is_partial_cleared=False,
                stop_loss_price=entry_price * (1.0 - self.stop_loss_ratio),
                max_high=entry_price,
            )
        return ok

    def _execute_sell(self, code: str, exit_price: float, exit_type: str, day_idx: int) -> None:
        super()._execute_sell(code, exit_price, exit_type, day_idx)
        self.lever_state.pop(str(code).zfill(6), None)

    # ── 부분 청산 (레버 D 1단계) ───────────────────────────────────
    def _execute_partial_sell(
        self, code: str, sell_qty: int, exit_price: float, exit_type: str, day_idx: int
    ) -> None:
        c6 = str(code).zfill(6)
        pos = self.positions.get(c6)
        if pos is None or sell_qty < 1 or sell_qty >= pos.qty:
            return
        if not np.isfinite(exit_price) or exit_price <= 0:
            return

        gross = sell_qty * exit_price
        proceeds = gross * (1.0 - self.sell_cost_ratio)
        full_cost_basis = pos.invest_amount + pos.buy_cost_paid
        portion = sell_qty / pos.qty
        cost_basis = full_cost_basis * portion
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
            "invest_amount": pos.invest_amount * portion,
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
            invest_amount=pos.invest_amount * portion,
            proceeds=proceeds,
            pnl_amount=pnl_amount,
            pnl_rate=pnl_rate,
            exit_type=exit_type,
        )
        # 잔량 비례 축소
        pos.qty -= sell_qty
        pos.invest_amount *= (1.0 - portion)
        pos.buy_cost_paid *= (1.0 - portion)

    # ── 레버 B + D 통합 청산 ───────────────────────────────────────
    def _process_exits(self, day_idx: int) -> None:
        lev = self.lever
        # 레버 B·D 모두 비활성 → v5.5.2 원본 청산(+8%/-3%/타임스탑) 그대로 (레버 A 공정 비교용)
        if not lev.gap_down_enabled and not lev.partial_tp_enabled:
            super()._process_exits(day_idx)
            return
        for code in list(self.positions.keys()):
            pos = self.positions[code]
            self._append_history_bar(code, day_idx)
            pos.hold_days += 1

            bar = self._get_daily_bar(code, day_idx)
            if bar is None:
                continue

            entry = pos.entry_price
            state = self.lever_state.get(code)
            if state is None:
                state = _LeverState(
                    is_partial_cleared=False,
                    stop_loss_price=entry * (1.0 - self.stop_loss_ratio),
                    max_high=entry,
                )
                self.lever_state[code] = state

            open_px = float(bar["open"])
            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar["close"])

            # ── 레버 B: 익일(진입 다음 영업일) 시가 갭다운 방어 ──
            if (
                lev.gap_down_enabled
                and pos.hold_days == 1
                and open_px > 0
                and open_px <= entry * (1.0 - lev.gap_down_ratio)
            ):
                self._execute_sell(code, open_px, "GAP_DOWN_EXIT", day_idx)
                continue

            if not state.is_partial_cleared:
                # 손절 우선(보수적) — 같은 봉 손절·익절 동시 도달 시 손절 처리
                stop_px = entry * (1.0 - self.stop_loss_ratio)
                if low <= stop_px:
                    self._execute_sell(code, stop_px, "STOP_LOSS", day_idx)
                    continue

                # 레버 D 1단계: 부분 익절
                if lev.partial_tp_enabled and high >= entry * (1.0 + lev.partial_tp_ratio):
                    tp_px = entry * (1.0 + lev.partial_tp_ratio)
                    if open_px > tp_px:  # 갭상승 시 시가 체결(불리하지 않은 보수 처리)
                        tp_px = open_px
                    partial_qty = pos.qty // 2 if lev.partial_tp_fraction == 0.5 else int(pos.qty * lev.partial_tp_fraction)
                    if partial_qty >= 1 and partial_qty < pos.qty:
                        self._execute_partial_sell(code, partial_qty, tp_px, "PARTIAL_TP_50", day_idx)
                        state.is_partial_cleared = True
                        state.stop_loss_price = entry * (1.0 + lev.breakeven_ratio)
                        state.max_high = high
                        continue
                    # 절반 불가(1주) → 전량 익절 처리
                    self._execute_sell(code, tp_px, "TAKE_PROFIT_FULL", day_idx)
                    continue

                # 타임스탑
                if pos.hold_days >= self.max_hold_days:
                    self._execute_sell(code, close, "TIME_STOP", day_idx)
                continue

            # ── 부분익절 후 잔량 관리 ──
            # 2단계: 본전 이탈
            if low <= state.stop_loss_price:
                exit_px = min(state.stop_loss_price, open_px) if open_px < state.stop_loss_price else state.stop_loss_price
                self._execute_sell(code, exit_px, "BREAKEVEN_STOP", day_idx)
                continue

            # 3단계: +8% 돌파 후 트레일링
            state.max_high = max(state.max_high, high)
            if state.max_high >= entry * (1.0 + lev.trail_arm_ratio):
                trail_px = state.max_high * (1.0 - lev.trail_giveback_ratio)
                if low <= trail_px:
                    exit_px = min(trail_px, open_px) if open_px < trail_px else trail_px
                    self._execute_sell(code, exit_px, "TRAILING_STOP_CLEAR", day_idx)
                    continue
