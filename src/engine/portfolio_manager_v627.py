"""
v6.27 수익률 극대화 및 과열 진입 제어 — v6.26 확장.

[유지] MA20 변곡 · 회전율≥8% · 양봉 인터록 · MA60/120 제거
[신설] 종가/MA20 ≤ 1.06 고이격 추격 차단
[신설] 당일 거래대금 ≥ 100억 원 절대값 필터 (회전율 AND)
[개편] 청산 +12% / -4% / 4일 (v6.26 +8%/-3% 대비)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.engine.portfolio_manager_v626 import Alpha626Config, PortfolioManagerV626

DEFAULT_PREWARM_BARS = 120
MIN_TRADING_VALUE_KRW = 10_000_000_000  # 100억 원
MAX_MA20_DISPARITY = 1.06


@dataclass
class Alpha627Config(Alpha626Config):
    """v6.27 진입·청산 파라미터."""
    min_trading_value_krw: float = MIN_TRADING_VALUE_KRW
    max_ma20_disparity: float = MAX_MA20_DISPARITY
    stop_loss_ratio: float = 0.04
    target_profit_ratio: float = 0.12


class PortfolioManagerV627(PortfolioManagerV626):
    """v6.27 이격도·거래대금 듀얼 필터 + 손익비 개편."""

    def __init__(
        self,
        *args,
        alpha: Alpha627Config | None = None,
        **kwargs,
    ):
        super().__init__(*args, alpha=alpha if alpha is not None else Alpha627Config(), **kwargs)
        cfg = self.alpha if isinstance(self.alpha, Alpha627Config) else Alpha627Config()
        self.alpha = cfg
        self.stop_loss_ratio = float(cfg.stop_loss_ratio)
        self.target_profit_ratio = float(cfg.target_profit_ratio)

    def _is_ma_inflection_turning_up(self, ohlcv_df: pd.DataFrame) -> bool:
        if not super()._is_ma_inflection_turning_up(ohlcv_df):
            return False

        window = self.lookback_window
        close_s = pd.to_numeric(ohlcv_df["close"], errors="coerce")
        today_close = float(close_s.iloc[-1])
        ma20_s = close_s.rolling(window=window).mean()
        today_ma20 = float(ma20_s.iloc[-1])
        if not np.isfinite(today_ma20) or today_ma20 <= 0:
            return False
        if today_close > today_ma20 * self.alpha.max_ma20_disparity:
            return False
        return True

    def _passes_turnover_filter(self, code: str, day_idx: int) -> bool:
        if not super()._passes_turnover_filter(code, day_idx):
            return False
        tv = self._get_trading_value_krw(code, day_idx)
        if tv is None or tv < self.alpha.min_trading_value_krw:
            return False
        return True

    def _execute_buy(self, code: str, entry_price: float, day_idx: int) -> bool:
        ok = super()._execute_buy(code, entry_price, day_idx)
        if ok and self.trade_detail_rows:
            self.trade_detail_rows[-1]["exit_type"] = "ENTRY_V627_DISPARITY_ALPHA"
        return ok
