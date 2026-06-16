"""
v10.0 Momentum 프리셋 — 52주 신고가 대형주 정추세 눌림목 (High Tight Flag).

15:20 일봉 종가 확정 기준. 지수 연산·인터록 없음 — 사용자 --preset momentum 선택 시만 가동.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd

from src.engine.v10_live_core import V10PresetEngineBase, scan_large_cap_universe

logger = logging.getLogger("MomentumEngine")

MIN_MCAP_KRW = 1_000_000_000_000       # 1조
MIN_TRADE_AMT_KRW = 100_000_000_000    # 1,000억
MA_TOUCH_WINDOW = 20
MA_EXIT_WINDOW = 10
HIGH_LOOKBACK = 252                    # 52주(영업일)
RECENT_HIGH_DAYS = 10
MA_TOUCH_TOLERANCE = 0.015             # MA20 대비 ±1.5%
ENTRY_TIME = "15:20"


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    rename = {}
    for c in work.columns:
        cl = str(c).lower()
        if cl in ("open", "high", "low", "close", "volume"):
            rename[c] = cl
        elif cl == "close":
            rename[c] = "close"
    if rename:
        work = work.rename(columns=rename)
    for std, alt in (("close", "Close"), ("high", "High"), ("low", "Low"), ("open", "Open")):
        if std not in work.columns and alt in work.columns:
            work = work.rename(columns={alt: std})
    return work.sort_index()


def _near_ma(close: float, ma: float, tolerance: float = MA_TOUCH_TOLERANCE) -> bool:
    if not np.isfinite(close) or not np.isfinite(ma) or ma <= 0:
        return False
    return abs(close - ma) / ma <= tolerance


def made_52w_high_within(high: pd.Series, *, lookback: int = HIGH_LOOKBACK, days: int = RECENT_HIGH_DAYS) -> bool:
    """최근 `days` 영업일 내 52주 신고가(rolling max) 갱신 여부."""
    if len(high) < lookback + days:
        return False
    high_n = pd.to_numeric(high, errors="coerce")
    rolling_max = high_n.rolling(window=lookback, min_periods=lookback).max()
    recent = high_n.iloc[-days:]
    recent_max = rolling_max.iloc[-days:]
    hits = recent >= recent_max * 0.998
    return bool(hits.any())


def detect_momentum_entry(ohlcv_df: pd.DataFrame) -> tuple[bool, str]:
    """
    진입: 최근 10일 내 52주 신고가 후, 종가가 MA20에 바짝 붙었을 때.
    """
    need = max(HIGH_LOOKBACK + RECENT_HIGH_DAYS, MA_TOUCH_WINDOW + 2)
    if len(ohlcv_df) < need:
        return False, f"일봉 부족 ({len(ohlcv_df)} < {need})"

    work = _normalize_ohlcv(ohlcv_df)
    close_s = pd.to_numeric(work["close"], errors="coerce")
    high_s = pd.to_numeric(work["high"], errors="coerce")
    today_close = float(close_s.iloc[-1])
    if not np.isfinite(today_close) or today_close <= 0:
        return False, "당일 종가 무효"

    if not made_52w_high_within(high_s):
        return False, f"최근 {RECENT_HIGH_DAYS}일 내 52주 신고가 미달성"

    ma20 = float(close_s.rolling(MA_TOUCH_WINDOW).mean().iloc[-1])
    if not np.isfinite(ma20):
        return False, "MA20 산출 불가"

    if not _near_ma(today_close, ma20):
        return False, f"MA20 미접촉 (종가 {today_close:,.0f} / MA20 {ma20:,.0f})"

    return True, f"통과 — 52주 신고가·MA{MA_TOUCH_WINDOW} 눌림 ({today_close:,.0f})"


def evaluate_momentum_exit(
    *,
    close: float,
    high: float,
    low: float,
    avg_entry: float,
    prior_high: float,
    partial_tp_done: bool,
    risk_free: bool,
    breakeven_stop: float,
    ma10: float,
) -> tuple[str, float, float] | None:
    """
    청산: 전고점 돌파 50% 익절 → 본전 손절 → MA10 이탈 전량.
    반환: (exit_type, price, sell_ratio) — sell_ratio=1.0 이면 전량.
    """
    if not np.isfinite(close) or close <= 0:
        return None

    if not partial_tp_done and np.isfinite(prior_high) and prior_high > 0 and close > prior_high:
        return ("MOMENTUM_PARTIAL_TP_50", close, 0.5)

    if risk_free and np.isfinite(breakeven_stop) and breakeven_stop > 0 and low <= breakeven_stop:
        return ("MOMENTUM_RISK_FREE", breakeven_stop, 1.0)

    if partial_tp_done and np.isfinite(ma10) and close < ma10:
        return ("MOMENTUM_MA10_BREAK", close, 1.0)

    return None


def scan_momentum_universe(*, project_root: str | None = None) -> list[str]:
    """시총 1조+ · 거래대금 1,000억+ 대형주 주도주 스캔."""
    return scan_large_cap_universe(
        min_mcap=MIN_MCAP_KRW,
        min_trade_amt=MIN_TRADE_AMT_KRW,
        markets=("KOSPI", "KOSDAQ"),
        top_n=40,
        project_root=project_root,
        preset_label="momentum",
    )


@dataclass
class MomentumEngine(V10PresetEngineBase):
    """v10.0 상승장 Momentum 프리셋 라이브 엔진."""

    preset: str = "momentum"
    entry_time: str = ENTRY_TIME

    def entry_signal(self, ohlcv_df: pd.DataFrame) -> tuple[bool, str]:
        return detect_momentum_entry(ohlcv_df)

    def scan_universe(self) -> list[str]:
        return scan_momentum_universe(project_root=self.project_root)

    def evaluate_exit(
        self,
        *,
        ohlcv_df: pd.DataFrame,
        entry_price: float,
        state: dict[str, Any],
        bar: dict[str, float],
    ) -> tuple[str, float, float] | None:
        work = _normalize_ohlcv(ohlcv_df)
        close_s = pd.to_numeric(work["close"], errors="coerce")
        high_s = pd.to_numeric(work["high"], errors="coerce")
        prior_high = float(state.get("prior_high") or high_s.iloc[-HIGH_LOOKBACK:].max())
        ma10 = float(close_s.rolling(MA_EXIT_WINDOW).mean().iloc[-1])
        return evaluate_momentum_exit(
            close=bar["close"],
            high=bar["high"],
            low=bar["low"],
            avg_entry=entry_price,
            prior_high=prior_high,
            partial_tp_done=bool(state.get("partial_tp_done")),
            risk_free=bool(state.get("risk_free")),
            breakeven_stop=float(state.get("breakeven_stop") or 0),
            ma10=ma10,
        )

    def init_position_state(self, ohlcv_df: pd.DataFrame) -> dict[str, Any]:
        work = _normalize_ohlcv(ohlcv_df)
        high_s = pd.to_numeric(work["high"], errors="coerce")
        prior = float(high_s.iloc[-HIGH_LOOKBACK:].max())
        return {"prior_high": prior, "partial_tp_done": False, "risk_free": False, "breakeven_stop": 0.0}

    def on_partial_exit(self, state: dict[str, Any], entry_price: float) -> dict[str, Any]:
        state = dict(state)
        state["partial_tp_done"] = True
        state["risk_free"] = True
        state["breakeven_stop"] = entry_price
        return state

    def run_1520_routine(self) -> None:
        logger.info("[🚀 상승장 모멘텀] 52주 신고가 대형주 정추세 눌림목 감시 시작...")
        self.run_master_loop()
