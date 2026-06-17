"""
v10.2.0 자본 수확·상시 복구 매니저 (Capital Harvesting & Refill Manager).

15:30 장마감 후 Total Equity 기준으로 Safe Vault ↔ 가용 원금(200만) 리밸런싱.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger("V102_CapitalManager")

DEFAULT_TARGET_CAPITAL = 2_000_000.0
STATE_REL = "config/v10_capital_buffer.json"

RebalanceEvent = Literal["none", "harvest", "refill_full", "refill_partial"]


@dataclass
class RebalanceResult:
    """일별 리밸런싱 결과."""

    available_capital: float
    cash_delta: float
    event: RebalanceEvent
    amount_moved: float = 0.0


@dataclass
class CapitalBufferStats:
    harvest_count: int = 0
    refill_full_count: int = 0
    refill_partial_count: int = 0
    total_harvested: float = 0.0
    total_refilled: float = 0.0


class CapitalBufferManager:
    """Safe Vault + 200만 원 가용 원금 · 5% 안전 버퍼(210만) 수확 임계."""

    def __init__(
        self,
        target_capital: float = DEFAULT_TARGET_CAPITAL,
        buffer_ratio: float = 0.05,
    ):
        self.target_capital = float(target_capital)
        self.buffer_ratio = float(buffer_ratio)
        self.harvest_threshold = self.target_capital * (1.0 + self.buffer_ratio)
        self.safe_vault = 0.0
        self.stats = CapitalBufferStats()

    def process_daily_rebalancing(
        self,
        current_balance: float,
        has_realized_pnl: bool = False,
    ) -> float:
        """15:30 EOD — 가용 투자 원금 반환 (레거시 단일 float API)."""
        return self.rebalance(current_balance, has_realized_pnl=has_realized_pnl).available_capital

    def rebalance(
        self,
        current_balance: float,
        has_realized_pnl: bool = False,
    ) -> RebalanceResult:
        """
        [v10.2.1] 확정 청산일에만 수확·수혈. 200~210만 원은 완충 구간.
        cash_delta: 호출측 self.cash 에 더할 금액 (수혈 + / 수확 -).
        """
        bal = float(current_balance)
        if not has_realized_pnl:
            return RebalanceResult(
                available_capital=bal,
                cash_delta=0.0,
                event="none",
            )

        if not (bal >= 0 and self.target_capital > 0):
            return RebalanceResult(
                available_capital=bal,
                cash_delta=0.0,
                event="none",
            )

        if bal > self.harvest_threshold:
            profit = bal - self.harvest_threshold
            self.safe_vault += profit
            self.stats.harvest_count += 1
            self.stats.total_harvested += profit
            logger.info(
                "💰 [자본 수확] 확정수익 %s원 금고 적립. (금고 총액: %s원)",
                f"{profit:,.0f}",
                f"{self.safe_vault:,.0f}",
            )
            return RebalanceResult(
                available_capital=self.harvest_threshold,
                cash_delta=-profit,
                event="harvest",
                amount_moved=profit,
            )

        if bal < self.target_capital:
            deficit = self.target_capital - bal
            if self.safe_vault >= deficit:
                self.safe_vault -= deficit
                self.stats.refill_full_count += 1
                self.stats.total_refilled += deficit
                logger.info(
                    "🔒 [자본 수혈] 손실 발생. 금고에서 %s원 꺼내 원금 %s원 완벽 복구.",
                    f"{deficit:,.0f}",
                    f"{self.target_capital:,.0f}",
                )
                return RebalanceResult(
                    available_capital=self.target_capital,
                    cash_delta=deficit,
                    event="refill_full",
                    amount_moved=deficit,
                )

            part_fill = self.safe_vault
            self.safe_vault = 0.0
            restored = bal + part_fill
            self.stats.refill_partial_count += 1
            self.stats.total_refilled += part_fill
            logger.warning(
                "⚠️ [자본 부분 수혈] 금고 잔액 부족으로 %s원만 수혈. 현재 가용자산: %s원",
                f"{part_fill:,.0f}",
                f"{restored:,.0f}",
            )
            return RebalanceResult(
                available_capital=restored,
                cash_delta=part_fill,
                event="refill_partial",
                amount_moved=part_fill,
            )

        return RebalanceResult(
            available_capital=bal,
            cash_delta=0.0,
            event="none",
        )

    def summary(self) -> dict[str, float | int]:
        return {
            "safe_vault": self.safe_vault,
            "target_capital": self.target_capital,
            "harvest_threshold": self.harvest_threshold,
            "harvest_count": self.stats.harvest_count,
            "refill_full_count": self.stats.refill_full_count,
            "refill_partial_count": self.stats.refill_partial_count,
            "total_harvested": self.stats.total_harvested,
            "total_refilled": self.stats.total_refilled,
        }


def load_capital_buffer(
    *,
    project_root: str | Path | None = None,
    target_capital: float = DEFAULT_TARGET_CAPITAL,
) -> CapitalBufferManager:
    root = Path(project_root or Path(__file__).resolve().parents[2])
    path = root / STATE_REL
    mgr = CapitalBufferManager(target_capital=target_capital)
    if not path.is_file():
        return mgr
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        mgr.safe_vault = float(raw.get("safe_vault") or 0)
        stats = raw.get("stats") or {}
        mgr.stats = CapitalBufferStats(
            harvest_count=int(stats.get("harvest_count") or 0),
            refill_full_count=int(stats.get("refill_full_count") or 0),
            refill_partial_count=int(stats.get("refill_partial_count") or 0),
            total_harvested=float(stats.get("total_harvested") or 0),
            total_refilled=float(stats.get("total_refilled") or 0),
        )
    except Exception as exc:
        logger.warning("Safe Vault 상태 로드 실패 — 초기화: %s", exc)
    return mgr


def save_capital_buffer(mgr: CapitalBufferManager, *, project_root: str | Path | None = None) -> None:
    root = Path(project_root or Path(__file__).resolve().parents[2])
    path = root / STATE_REL
    os.makedirs(path.parent, exist_ok=True)
    payload = {
        "safe_vault": mgr.safe_vault,
        "target_capital": mgr.target_capital,
        "stats": {
            "harvest_count": mgr.stats.harvest_count,
            "refill_full_count": mgr.stats.refill_full_count,
            "refill_partial_count": mgr.stats.refill_partial_count,
            "total_harvested": mgr.stats.total_harvested,
            "total_refilled": mgr.stats.total_refilled,
        },
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
