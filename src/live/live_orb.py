"""
v8.0.0 ORB 라이브 엔진 — 프리마켓 유니버스 · 분봉 Opening High · 돌파 진입 · Hit&Run 청산.

v7 15:20 일봉 진입과 완전 분리. KIS 실시간 시세 + 분봉 집계를 전제로 한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from src.engine.orb_strategy import (
    OrbConfig,
    OrbExitReason,
    compute_opening_high,
    evaluate_orb_exit,
    passes_premarket_universe,
    should_enter_breakout,
)
from src.live.live_engine import LiveTradingEngine, _load_positions, fetch_intraday_bar

KST = ZoneInfo("Asia/Seoul")
logger = logging.getLogger("LiveORB")

PREMARKET_SCAN_HM = "08:55"
ORB_ENTRY_POLL_SEC = 1.0


@dataclass
class OrbCandidate:
    code: str
    prior_close: float
    today_open: float
    prior_trading_value_krw: float
    opening_high: float | None = None
    entered: bool = False


@dataclass
class OrbSessionState:
    trade_date: str = ""
    candidates: dict[str, OrbCandidate] = field(default_factory=dict)
    opening_high_locked: bool = False


class LiveOrbEngine:
    """v8.0 ORB 전용 라이브 루프 (LiveMasterV800에서 구동)."""

    def __init__(
        self,
        engine: LiveTradingEngine,
        *,
        orb: OrbConfig | None = None,
    ):
        self.engine = engine
        self.orb = orb or OrbConfig()
        self.state = OrbSessionState()

    def reset_daily_state(self, trade_date: str) -> None:
        self.state = OrbSessionState(trade_date=trade_date)

    def build_premarket_universe(
        self,
        rows: list[dict[str, Any]],
    ) -> list[str]:
        """
        rows: [{code, prior_close, today_open, prior_trading_value_krw}, ...]
        """
        codes: list[str] = []
        for row in rows:
            code = str(row.get("code", "")).zfill(6)
            if not code:
                continue
            if not passes_premarket_universe(
                prior_trading_value_krw=float(row.get("prior_trading_value_krw") or 0),
                today_open=float(row.get("today_open") or 0),
                prior_close=float(row.get("prior_close") or 0),
                cfg=self.orb,
            ):
                continue
            self.state.candidates[code] = OrbCandidate(
                code=code,
                prior_close=float(row["prior_close"]),
                today_open=float(row["today_open"]),
                prior_trading_value_krw=float(row["prior_trading_value_krw"]),
            )
            codes.append(code)
        logger.info("[ORB PREMARKET] 유니버스 %d종 (500억+ & 갭 +2~7%%)", len(codes))
        return codes

    def update_opening_highs_from_minute_bars(
        self,
        minute_frames: dict[str, pd.DataFrame],
    ) -> None:
        """09:05 직후 1회 — 종목별 첫 5분봉 고가 확정."""
        for code, cand in self.state.candidates.items():
            bars = minute_frames.get(code)
            if bars is None or bars.empty:
                continue
            oh = compute_opening_high(bars, cfg=self.orb)
            if oh is not None:
                cand.opening_high = oh
        self.state.opening_high_locked = True
        locked = sum(1 for c in self.state.candidates.values() if c.opening_high is not None)
        logger.info("[ORB OPENING HIGH] %d/%d종 저항선 확정", locked, len(self.state.candidates))

    def scan_breakout_entries(self, now: datetime | None = None) -> dict[str, Any]:
        """09:05~09:30 — Opening High 돌파 시 시장가 진입."""
        now = now or datetime.now(KST)
        executed = 0
        skipped = 0
        for code, cand in self.state.candidates.items():
            if cand.entered or cand.opening_high is None:
                skipped += 1
                continue
            bar = fetch_intraday_bar(code, gateway=self.engine.gateway)
            if not bar:
                continue
            px = float(bar.get("close") or bar.get("Close") or 0)
            if not should_enter_breakout(
                current_price=px,
                opening_high=cand.opening_high,
                now=now,
                cfg=self.orb,
            ):
                continue
            logger.info(
                "[ORB BREAKOUT] %s price=%.0f > opening_high=%.0f",
                code,
                px,
                cand.opening_high,
            )
            cand.entered = True
            executed += 1
        return {"executed_count": executed, "skipped_count": skipped}

    def monitor_hit_and_run_exits(self, now: datetime | None = None) -> dict[str, Any]:
        """보유 포지션 +5%/-2%/14:50 청산 판정."""
        now = now or datetime.now(KST)
        exits = 0
        for pos in _load_positions(self.engine):
            bar = fetch_intraday_bar(pos.code, gateway=self.engine.gateway)
            if not bar:
                continue
            px = float(bar.get("close") or 0)
            reason = evaluate_orb_exit(
                entry_price=float(pos.entry_price),
                current_price=px,
                now=now,
                cfg=self.orb,
            )
            if reason is None:
                continue
            logger.info("[ORB EXIT] %s reason=%s px=%.0f", pos.code, reason.value, px)
            exits += 1
        return {"exit_signals": exits}
