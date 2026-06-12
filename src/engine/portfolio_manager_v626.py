"""
v6.26 진입 알파 개편 엔진 — 회전율 주도주 + 양봉 인터록, 청산 순정화.

⚠️ v5.5.2 SSOT(portfolio_manager_v5) 청산(+8%/-3%/4일)은 불변.
   본 모듈은 진입 필터만 개편하는 연구용 샌드박스다.

[삭제] MA60/MA120 우상향 · 종가>MA120 매크로 필터
[신설] 당일 거래대금/시가총액 ≥ 8% 회전율 필터
[신설] 당일 종가 > 당일 시가 (양봉 확정 인터록)
[유지] MA20 변곡점 진입 코어
[청산] v5.5.2 Hit & Run 고정 레이더 (트레일링·부분익절·갭다운 없음)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.engine.portfolio_manager_v5 import PortfolioManagerV5

DEFAULT_PREWARM_BARS = 120


@dataclass
class Alpha626Config:
    """v6.26 진입 알파 파라미터."""
    min_turnover_ratio: float = 0.08   # 거래대금 / 시가총액 ≥ 8%


class PortfolioManagerV626(PortfolioManagerV5):
    """v6.26 회전율 주도주 진입 + v5.5.2 순정 청산."""

    def __init__(
        self,
        *args,
        alpha: Alpha626Config | None = None,
        prewarm_bars: int = DEFAULT_PREWARM_BARS,
        enable_prewarm: bool = True,
        marcap_by_date_code: dict[tuple[str, str], float] | None = None,
        shares_by_code: dict[str, float] | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        # v6.26: 둔중한 MA60/MA120 매크로 필터 전면 제거
        self.macro_trend_enabled = False
        self.macro_ma_window = 0
        self.macro_price_above_ma = 0
        self.macro_dual_slope_windows = ()

        self.alpha = alpha if alpha is not None else Alpha626Config()
        self.prewarm_bars = max(int(prewarm_bars), self.lookback_window + 1)
        self.enable_prewarm = bool(enable_prewarm)
        self.marcap_by_date_code = marcap_by_date_code if marcap_by_date_code is not None else {}
        self.shares_by_code = shares_by_code if shares_by_code is not None else {}

    def _macro_min_history_bars(self) -> int:
        """MA120 워밍업 불필요 — MA20 변곡만 사용."""
        return self.lookback_window + 1

    def _prewarm_history(self) -> None:
        """시뮬 시작 전 prewarm_bars(기본 120)만큼 히스토리 프리적재."""
        if self.target_universe is None:
            return
        start = max(0, self._sim_start_idx - self.prewarm_bars)
        for di in range(start, self._sim_start_idx):
            for code in self.target_universe:
                self._append_history_bar(code, di)

    def run(self):
        if self.enable_prewarm:
            self._prewarm_history()
        return super().run()

    def _resolve_frame_key(self, code: str, day_idx: int):
        c6 = str(code).zfill(6)
        day_frame = self.day_frames[day_idx]
        index_by_c6 = {str(k).zfill(6): k for k in day_frame.index}
        return index_by_c6.get(c6)

    def _get_trading_value_krw(self, code: str, day_idx: int) -> float | None:
        key = self._resolve_frame_key(code, day_idx)
        if key is None:
            return None
        day_frame = self.day_frames[day_idx]
        if "Amount" in day_frame.columns:
            amt = float(day_frame.loc[key, "Amount"])
            if np.isfinite(amt) and amt > 0:
                return amt
        close_px = float(day_frame.loc[key, "Close"])
        vol = float(day_frame.loc[key, "Volume"])
        if np.isfinite(close_px) and np.isfinite(vol) and close_px > 0 and vol > 0:
            return close_px * vol
        return None

    def _get_market_cap_krw(self, code: str, day_idx: int) -> float | None:
        c6 = str(code).zfill(6)
        dt = pd.Timestamp(self.bdays[day_idx]).normalize().strftime("%Y-%m-%d")
        mc = self.marcap_by_date_code.get((dt, c6))
        if mc is not None and np.isfinite(mc) and mc > 0:
            return float(mc)
        shares = self.shares_by_code.get(c6)
        bar = self._get_daily_bar(c6, day_idx)
        if shares is not None and bar is not None:
            close_px = float(bar["close"])
            if np.isfinite(close_px) and close_px > 0 and shares > 0:
                return close_px * shares
        return None

    def _passes_turnover_filter(self, code: str, day_idx: int) -> bool:
        tv = self._get_trading_value_krw(code, day_idx)
        mc = self._get_market_cap_krw(code, day_idx)
        if tv is None or mc is None or mc <= 0:
            return False
        return (tv / mc) >= self.alpha.min_turnover_ratio

    def _is_ma_inflection_turning_up(self, ohlcv_df: pd.DataFrame) -> bool:
        """MA20 변곡 + 양봉(종가>시가). 매크로 필터 없음."""
        window = self.lookback_window
        if len(ohlcv_df) < self._macro_min_history_bars():
            return False

        close_s = pd.to_numeric(ohlcv_df["close"], errors="coerce")
        open_s = pd.to_numeric(ohlcv_df["open"], errors="coerce")
        today_close = float(close_s.iloc[-1])
        today_open = float(open_s.iloc[-1])
        if not np.isfinite(today_close) or today_close <= 0:
            return False
        if not np.isfinite(today_open) or today_close <= today_open:
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

        return yesterday_close <= yesterday_ma and today_close > past_20_close

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
            if not self._passes_turnover_filter(c6, day_idx):
                continue
            entry_price = float(hist.iloc[-1]["close"])
            self._execute_buy(c6, entry_price, day_idx)

    def _execute_buy(self, code: str, entry_price: float, day_idx: int) -> bool:
        ok = super()._execute_buy(code, entry_price, day_idx)
        if ok and self.trade_detail_rows:
            self.trade_detail_rows[-1]["exit_type"] = "ENTRY_V626_TURNOVER_ALPHA"
        return ok
