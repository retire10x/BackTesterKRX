"""
v6.28 소생 및 파라미터 재조정 — v6.27 완화 + v5.5.2 청산 롤백.

[완화] 거래대금 ≥ 50억 (100억 → 50억)
[완화] 종가/MA20 ≤ 1.10 (1.06 → 1.10)
[롤백] 청산 +8% / -3% / 4일 (v5.5.2 순정)
"""
from __future__ import annotations

from dataclasses import dataclass

from src.engine.portfolio_manager_v627 import Alpha627Config, PortfolioManagerV627

DEFAULT_PREWARM_BARS = 120
MIN_TRADING_VALUE_KRW = 5_000_000_000  # 50억 원
MAX_MA20_DISPARITY = 1.10


@dataclass
class Alpha628Config(Alpha627Config):
    """v6.28 완화 파라미터."""
    min_trading_value_krw: float = MIN_TRADING_VALUE_KRW
    max_ma20_disparity: float = MAX_MA20_DISPARITY
    stop_loss_ratio: float = 0.03
    target_profit_ratio: float = 0.08


class PortfolioManagerV628(PortfolioManagerV627):
    """v6.28 완화 필터 + v5.5.2 순정 청산."""

    def __init__(
        self,
        *args,
        alpha: Alpha628Config | None = None,
        **kwargs,
    ):
        super().__init__(*args, alpha=alpha if alpha is not None else Alpha628Config(), **kwargs)
        cfg = self.alpha if isinstance(self.alpha, Alpha628Config) else Alpha628Config()
        self.alpha = cfg
        self.stop_loss_ratio = float(cfg.stop_loss_ratio)
        self.target_profit_ratio = float(cfg.target_profit_ratio)

    def _execute_buy(self, code: str, entry_price: float, day_idx: int) -> bool:
        ok = super(PortfolioManagerV627, self)._execute_buy(code, entry_price, day_idx)
        if ok and self.trade_detail_rows:
            self.trade_detail_rows[-1]["exit_type"] = "ENTRY_V628_REVIVAL_ALPHA"
        return ok
