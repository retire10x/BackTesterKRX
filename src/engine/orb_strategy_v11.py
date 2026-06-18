"""
v11.2 라이브 ORB (Opening Range Breakout) 데이트레이딩 전략 코어.

한국투자증권(KIS) 실시간 당일 1분봉 OHLCV 기반 정밀 돌파 진입 및 청산 엔진.
09:00~09:15 분봉 기준선 확정 ──> 09:16~10:30 돌파 진입 ──> 실시간 TP/SL/본전 스탑 감시.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

ORB_RANGE_RATIO = 0.35
MORNING_THRUST_MIN = 0.25
VOLUME_SURGE_RATIO = 1.2

STOP_LOSS_PCT = 0.025
PARTIAL_TP_PCT = 0.03
FULL_TP_PCT = 0.05
PARTIAL_SELL_RATIO = 0.5


@dataclass(frozen=True)
class ORBSetup:
    """당일 ORB 기준선."""

    orb_high: float
    orb_low: float
    open_px: float


@dataclass(frozen=True)
class ORBExitDecision:
    exit_type: str
    exit_price: float
    sell_ratio: float = 1.0


def estimate_orb_setup(open_px: float, high_px: float, low_px: float) -> ORBSetup | None:
    """09:00~09:15 최고가 proxy — 시가 + (고가-시가) × 35%."""
    if not all(np.isfinite(v) and v > 0 for v in (open_px, high_px, low_px)):
        return None
    if high_px < open_px:
        return None
    orb_high = open_px + (high_px - open_px) * ORB_RANGE_RATIO
    return ORBSetup(orb_high=orb_high, orb_low=open_px, open_px=open_px)


def passes_ma5_alignment(closes: pd.Series) -> bool:
    """5일 이평선 정배열: 종가 > MA5, MA5 우상향."""
    if len(closes) < 6:
        return False
    ma5 = closes.rolling(5).mean()
    if not np.isfinite(ma5.iloc[-1]) or not np.isfinite(ma5.iloc[-2]):
        return False
    return float(closes.iloc[-1]) > float(ma5.iloc[-1]) and float(ma5.iloc[-1]) > float(ma5.iloc[-2])


def detect_orb_breakout(
    *,
    open_px: float,
    high_px: float,
    low_px: float,
    close_px: float,
    volume: float,
    avg_volume_5d: float,
    setup: ORBSetup,
) -> bool:
    """
    09:00~10:30 신규 진입 proxy.
    - 고가가 ORB 저항선 돌파
    - 장 초반 상방 추세 (morning thrust)
    - 종가가 ORB 위에서 마감
    - 거래량 ≥ 5일 평균 × 1.2
    """
    if high_px <= setup.orb_high:
        return False
    if close_px < setup.orb_high:
        return False
    day_range = high_px - low_px
    if day_range <= 0:
        return False
    morning_thrust = (high_px - open_px) / day_range
    if morning_thrust < MORNING_THRUST_MIN:
        return False
    if avg_volume_5d > 0 and volume < avg_volume_5d * VOLUME_SURGE_RATIO:
        return False
    return True


def evaluate_orb_exit(
    *,
    entry_price: float,
    open_px: float,
    high_px: float,
    low_px: float,
    close_px: float,
    partial_tp_done: bool,
    risk_free: bool,
    breakeven_stop: float,
    force_eod: bool = False,
) -> ORBExitDecision | None:
    """
    당일 청산 우선순위 (보수적 daily-bar 순서):
    1. 손절 -2.5%
    2. 익절 +5% 전량
    3. 익절 +3% 50% (미실행 시)
    4. 본전 락인 (risk_free)
    5. 15:20 Time-stop (종가 proxy)
    """
    if not np.isfinite(entry_price) or entry_price <= 0:
        return None

    stop_px = entry_price * (1.0 - STOP_LOSS_PCT)
    partial_px = entry_price * (1.0 + PARTIAL_TP_PCT)
    full_px = entry_price * (1.0 + FULL_TP_PCT)

    if low_px <= stop_px:
        return ORBExitDecision("STOP_LOSS", stop_px, 1.0)

    if high_px >= full_px:
        return ORBExitDecision("TAKE_PROFIT_FULL", full_px, 1.0)

    if not partial_tp_done and high_px >= partial_px:
        return ORBExitDecision("PARTIAL_TP_50", partial_px, PARTIAL_SELL_RATIO)

    if risk_free and low_px <= breakeven_stop:
        return ORBExitDecision("RISK_FREE_BREAKEVEN", breakeven_stop, 1.0)

    if force_eod:
        return ORBExitDecision("TIME_STOP_1520", close_px, 1.0)

    return None
