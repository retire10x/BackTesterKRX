"""
v7.0.0 주도주 낙폭과대(Extreme Fear) 엔진 — v6.x 진입 두뇌 전면 교체.

[삭제] MA60/120 · 회전율 8% · MA20 변곡 · 양봉 인터록
[신설] 10일 내 500억+ 주도주 혈통 AND 과매도(RSI≤30 | MA20-10%) AND 거래량 급감(<30%)
[청산] +8% / -5% / 4일 (휩소 버퍼 확장)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.engine.portfolio_manager_v626 import (
    DEFAULT_PREWARM_BARS,
    Alpha626Config,
    PortfolioManagerV626,
)

MIN_PEAK_TRADING_VALUE_KRW = 50_000_000_000  # 500억
LOOKBACK_DAYS = 10
MAX_VOLUME_RATIO = 0.30
ENVELOPE_MA_RATIO = 0.90
RSI_OVERSOLD = 30.0
RSI_WINDOW = 14
MIN_HISTORY_BARS = 20


@dataclass
class Alpha700Config:
    """v7.0.0 Extreme Fear 파라미터."""
    min_peak_trading_value_krw: float = MIN_PEAK_TRADING_VALUE_KRW
    lookback_days: int = LOOKBACK_DAYS
    max_volume_ratio: float = MAX_VOLUME_RATIO
    envelope_ma_ratio: float = ENVELOPE_MA_RATIO
    rsi_oversold: float = RSI_OVERSOLD
    rsi_window: int = RSI_WINDOW
    stop_loss_ratio: float = 0.05
    target_profit_ratio: float = 0.08


class PortfolioManagerV700(PortfolioManagerV626):
    """v7.0.0 공포의 끝자락 역추세 진입 + 변동성 버퍼 청산."""

    def __init__(
        self,
        *args,
        alpha: Alpha700Config | None = None,
        **kwargs,
    ):
        super().__init__(*args, alpha=Alpha626Config(), **kwargs)
        self.alpha700 = alpha if alpha is not None else Alpha700Config()
        self.stop_loss_ratio = float(self.alpha700.stop_loss_ratio)
        self.target_profit_ratio = float(self.alpha700.target_profit_ratio)

    def _macro_min_history_bars(self) -> int:
        return max(MIN_HISTORY_BARS, self.alpha700.rsi_window + 1)

    def _passes_lead_stock_pedigree(self, code: str, day_idx: int) -> bool:
        """최근 10영업일 최고 거래대금 ≥ 500억."""
        lb = int(self.alpha700.lookback_days)
        peak = 0.0
        for di in range(max(0, day_idx - lb + 1), day_idx + 1):
            tv = self._get_trading_value_krw(code, di)
            if tv is not None and np.isfinite(tv):
                peak = max(peak, float(tv))
        return peak >= self.alpha700.min_peak_trading_value_krw

    def _is_extreme_fear_dip(self, ohlcv_df: pd.DataFrame) -> bool:
        """거래량 급감 + 과매도(엔벨로프 하단 또는 RSI≤30)."""
        if len(ohlcv_df) < self._macro_min_history_bars():
            return False

        close_s = pd.to_numeric(ohlcv_df["close"], errors="coerce")
        vol_s = pd.to_numeric(ohlcv_df["volume"], errors="coerce")
        today_close = float(close_s.iloc[-1])
        today_vol = float(vol_s.iloc[-1])
        if not np.isfinite(today_close) or today_close <= 0:
            return False
        if not np.isfinite(today_vol) or today_vol < 0:
            return False
        if self.use_price_filter and not (self.price_floor <= today_close <= self.price_ceiling):
            return False

        prior_vol = vol_s.iloc[-(self.alpha700.lookback_days + 1):-1]
        if len(prior_vol) < self.alpha700.lookback_days:
            return False
        recent_max_vol = float(prior_vol.max())
        if not np.isfinite(recent_max_vol) or recent_max_vol <= 0:
            return False
        if today_vol >= recent_max_vol * self.alpha700.max_volume_ratio:
            return False

        ma20 = float(close_s.rolling(window=self.lookback_window).mean().iloc[-1])
        if not np.isfinite(ma20) or ma20 <= 0:
            return False
        is_envelope_dip = today_close <= ma20 * self.alpha700.envelope_ma_ratio

        delta = close_s.diff()
        gain = delta.where(delta > 0, 0.0).rolling(window=self.alpha700.rsi_window).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(window=self.alpha700.rsi_window).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi_s = 100.0 - (100.0 / (1.0 + rs))
        rsi_14 = float(rsi_s.iloc[-1])
        if not np.isfinite(rsi_14):
            rsi_14 = 100.0
        is_rsi_dip = rsi_14 <= self.alpha700.rsi_oversold

        return is_envelope_dip or is_rsi_dip

    def _process_entries(self, day_idx: int, candidate_codes: list[str]) -> None:
        if self.available_slots <= 0 or self.cash <= 0:
            return
        for code in candidate_codes:
            if self.available_slots <= 0 or self.cash <= 0:
                break
            c6 = str(code).zfill(6)
            if c6 in self.positions:
                continue
            if not self._passes_lead_stock_pedigree(c6, day_idx):
                continue
            self._append_history_bar(c6, day_idx)
            hist = self._history_as_of(c6, day_idx)
            if hist is None:
                continue
            if not self._is_extreme_fear_dip(hist):
                continue
            entry_price = float(hist.iloc[-1]["close"])
            self._execute_buy(c6, entry_price, day_idx)

    def _execute_buy(self, code: str, entry_price: float, day_idx: int) -> bool:
        ok = super(PortfolioManagerV626, self)._execute_buy(code, entry_price, day_idx)
        if ok and self.trade_detail_rows:
            self.trade_detail_rows[-1]["exit_type"] = "ENTRY_V700_EXTREME_FEAR"
        return ok
