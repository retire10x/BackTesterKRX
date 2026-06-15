"""
v7.2.0 Final Master 엔진 — v7.1.0 Pivot에 KOSDAQ 3일선 시장 인터록 추가.

[상속] v7.1.0 수급 메모리/낙폭과대/브레이크 캔들/청산 손익비 유지
[신설] KOSDAQ 종가가 3일 이동평균선 아래면 당일 신규 매수 전면 차단
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.engine.portfolio_manager_v710 import Alpha710Config, PortfolioManagerV710

KOSDAQ_MARKET_MA_WINDOW = 3


@dataclass
class Alpha720Config(Alpha710Config):
    """v7.2.0 Final Master 파라미터."""
    market_ma_window: int = KOSDAQ_MARKET_MA_WINDOW


class PortfolioManagerV720(PortfolioManagerV710):
    """v7.1.0 Pivot 진입 전 KOSDAQ 3일선 시장 생존선을 검증."""

    def __init__(
        self,
        *args,
        alpha: Alpha720Config | None = None,
        kosdaq_index_df: pd.DataFrame | None = None,
        **kwargs,
    ):
        super().__init__(*args, alpha=alpha if alpha is not None else Alpha720Config(), **kwargs)
        cfg = self.alpha700 if isinstance(self.alpha700, Alpha720Config) else Alpha720Config()
        self.alpha700 = cfg
        self.kosdaq_index_df = self._normalize_kosdaq_index_df(kosdaq_index_df)
        self._market_close_s = pd.to_numeric(self.kosdaq_index_df["close"], errors="coerce")
        self._market_ma_s = self._market_close_s.rolling(window=int(cfg.market_ma_window)).mean()
        self._market_intercept_dates: set[str] = set()

    @staticmethod
    def _normalize_kosdaq_index_df(kosdaq_index_df: pd.DataFrame | None) -> pd.DataFrame:
        if kosdaq_index_df is None or kosdaq_index_df.empty:
            raise ValueError("PortfolioManagerV720 requires a non-empty kosdaq_index_df.")
        out = kosdaq_index_df.copy()
        if not isinstance(out.index, pd.DatetimeIndex):
            out.index = pd.to_datetime(out.index)
        out.index = out.index.normalize()
        out = out.sort_index()
        lower_cols = {str(c).strip().lower(): c for c in out.columns}
        if "close" not in lower_cols:
            raise ValueError("kosdaq_index_df must contain a close column.")
        close_col = lower_cols["close"]
        if close_col != "close":
            out = out.rename(columns={close_col: "close"})
        return out

    def _is_market_safe(self, day_idx: int) -> bool:
        """KOSDAQ 지수가 단기 생존선인 3일선 위에 있는지 검증."""
        target_date = pd.Timestamp(self.bdays[day_idx]).normalize()
        if target_date not in self._market_close_s.index:
            return False

        today_idx_close = float(self._market_close_s.loc[target_date])
        today_idx_ma = float(self._market_ma_s.loc[target_date])
        if not np.isfinite(today_idx_close) or not np.isfinite(today_idx_ma):
            return False
        return today_idx_close >= today_idx_ma

    def _log_market_intercept_once(self, day_idx: int) -> None:
        target_date = pd.Timestamp(self.bdays[day_idx]).normalize()
        date_s = target_date.strftime("%Y-%m-%d")
        if date_s in self._market_intercept_dates:
            return
        self._market_intercept_dates.add(date_s)
        close_v = self._market_close_s.get(target_date, np.nan)
        ma_v = self._market_ma_s.get(target_date, np.nan)
        msg = (
            f"[MARKET INTERCEPT] {date_s} KOSDAQ close={float(close_v):.2f} "
            f"< MA{int(self.alpha700.market_ma_window)}={float(ma_v):.2f} -> entries blocked"
        )
        self.pass_logs.append(msg)
        print(msg, flush=True)

    def _process_entries(self, day_idx: int, candidate_codes: list[str]) -> None:
        if not self._is_market_safe(day_idx):
            self._log_market_intercept_once(day_idx)
            return
        super()._process_entries(day_idx, candidate_codes)

    def _execute_buy(self, code: str, entry_price: float, day_idx: int) -> bool:
        ok = super()._execute_buy(code, entry_price, day_idx)
        if ok and self.trade_detail_rows:
            self.trade_detail_rows[-1]["exit_type"] = "ENTRY_V720_FINAL_MASTER"
        return ok
