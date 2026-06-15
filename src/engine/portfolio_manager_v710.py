"""
v7.1.0 Pivot 엔진 — 낙폭과대 불씨에 브레이크 확인을 더한 정밀 튜닝.

[완화] 최근 20영업일 최고 거래대금 >= 200억 (v7.0 10일/500억)
[신설] 낙폭과대 상태에서 양봉 또는 긴 아랫꼬리 브레이크 캔들 확인
[롤백] 청산 +8% / -3% / 4일
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.engine.portfolio_manager_v700 import (
    ENVELOPE_MA_RATIO,
    MAX_VOLUME_RATIO,
    MIN_HISTORY_BARS,
    RSI_OVERSOLD,
    RSI_WINDOW,
    Alpha700Config,
    PortfolioManagerV700,
)

MIN_PIVOT_TRADING_VALUE_KRW = 20_000_000_000  # 200억
PIVOT_LOOKBACK_DAYS = 20


@dataclass
class Alpha710Config(Alpha700Config):
    """v7.1.0 Pivot 파라미터."""
    min_peak_trading_value_krw: float = MIN_PIVOT_TRADING_VALUE_KRW
    lookback_days: int = PIVOT_LOOKBACK_DAYS
    max_volume_ratio: float = MAX_VOLUME_RATIO
    envelope_ma_ratio: float = ENVELOPE_MA_RATIO
    rsi_oversold: float = RSI_OVERSOLD
    rsi_window: int = RSI_WINDOW
    stop_loss_ratio: float = 0.03
    target_profit_ratio: float = 0.08


class PortfolioManagerV710(PortfolioManagerV700):
    """v7.1.0 수급 메모리 완화 + 브레이크 캔들 확인 + 손절 -3%."""

    def __init__(
        self,
        *args,
        alpha: Alpha710Config | None = None,
        **kwargs,
    ):
        super().__init__(*args, alpha=alpha if alpha is not None else Alpha710Config(), **kwargs)
        cfg = self.alpha700 if isinstance(self.alpha700, Alpha710Config) else Alpha710Config()
        self.alpha700 = cfg
        self.stop_loss_ratio = float(cfg.stop_loss_ratio)
        self.target_profit_ratio = float(cfg.target_profit_ratio)

    def _macro_min_history_bars(self) -> int:
        return max(MIN_HISTORY_BARS, self.alpha700.rsi_window + 1, self.alpha700.lookback_days + 1)

    def _passes_lead_stock_pedigree(self, code: str, day_idx: int) -> bool:
        """최근 20영업일 최고 거래대금 >= 200억."""
        lb = int(self.alpha700.lookback_days)
        peak = 0.0
        for di in range(max(0, day_idx - lb + 1), day_idx + 1):
            tv = self._get_trading_value_krw(code, di)
            if tv is not None and np.isfinite(tv):
                peak = max(peak, float(tv))
        return peak >= self.alpha700.min_peak_trading_value_krw

    @staticmethod
    def _is_break_confirmation_candle(open_px: float, low_px: float, close_px: float) -> bool:
        """양봉 또는 몸통보다 긴 아랫꼬리면 하락 방어 흔적으로 인정."""
        if not all(np.isfinite(v) for v in (open_px, low_px, close_px)):
            return False
        if min(open_px, close_px, low_px) <= 0:
            return False
        is_bullish = close_px > open_px
        body = abs(close_px - open_px)
        lower_shadow = min(open_px, close_px) - low_px
        has_long_lower_shadow = lower_shadow > body
        return is_bullish or has_long_lower_shadow

    def _is_extreme_fear_dip(self, ohlcv_df: pd.DataFrame) -> bool:
        """거래량 급감 + 과매도 + 브레이크 캔들 확인."""
        if len(ohlcv_df) < self._macro_min_history_bars():
            return False

        close_s = pd.to_numeric(ohlcv_df["close"], errors="coerce")
        open_s = pd.to_numeric(ohlcv_df["open"], errors="coerce")
        low_s = pd.to_numeric(ohlcv_df["low"], errors="coerce")
        vol_s = pd.to_numeric(ohlcv_df["volume"], errors="coerce")
        today_close = float(close_s.iloc[-1])
        today_open = float(open_s.iloc[-1])
        today_low = float(low_s.iloc[-1])
        today_vol = float(vol_s.iloc[-1])
        if not np.isfinite(today_close) or today_close <= 0:
            return False
        if not np.isfinite(today_vol) or today_vol < 0:
            return False
        if self.use_price_filter and not (self.price_floor <= today_close <= self.price_ceiling):
            return False
        if not self._is_break_confirmation_candle(today_open, today_low, today_close):
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

    def _execute_buy(self, code: str, entry_price: float, day_idx: int) -> bool:
        ok = super(PortfolioManagerV700, self)._execute_buy(code, entry_price, day_idx)
        if ok and self.trade_detail_rows:
            self.trade_detail_rows[-1]["exit_type"] = "ENTRY_V710_PIVOT"
        return ok
