"""
v9.0.0 대형주 피보나치 스윙 (Risk-Free Swing) — 순수 신호 로직.

15:20 일봉 종가 확정 기준. 분봉/틱 불필요.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from src.engine.v10_live_core import V10PresetEngineBase

MA_SHORT = 60
MA_LONG = 200
GC_MIN_BARS = 63   # 약 3개월
GC_MAX_BARS = 126  # 약 6개월
FIB_RATIOS = (0.382, 0.500, 0.618)
TRANCHE_AMOUNTS_KRW = (125_000, 125_000, 250_000)
SLOT_BUDGET_KRW = 500_000
FIB_TOLERANCE = 0.015  # 레vel 대비 ±1.5%
MIN_MCAP_KRW = 500_000_000_000       # 5,000억
PREFERRED_MCAP_KRW = 1_000_000_000_000  # 1조

KOSPI200_INDEX = "1028"
KOSDAQ150_INDEX = "2203"


@dataclass(frozen=True)
class FibSwingSetup:
    gc_date: pd.Timestamp
    swing_high: float
    swing_low: float
    fib_prices: tuple[float, float, float]


@dataclass(frozen=True)
class FibTrancheSignal:
    tranche_index: int  # 0, 1, 2
    fib_ratio: float
    fib_price: float
    amount_krw: float


def find_golden_cross_index(
    close: pd.Series,
    *,
    short: int = MA_SHORT,
    long: int = MA_LONG,
    min_bars_ago: int = GC_MIN_BARS,
    max_bars_ago: int = GC_MAX_BARS,
) -> int | None:
    """최근 min~max 영업일 내 MA60×MA200 골든크로스 바 인덱스(iloc). 없으면 None."""
    if len(close) < long + 2:
        return None
    ma_s = close.rolling(window=short).mean()
    ma_l = close.rolling(window=long).mean()
    cross = (ma_s > ma_l) & (ma_s.shift(1) <= ma_l.shift(1))
    idxs = np.flatnonzero(cross.fillna(False).to_numpy())
    if len(idxs) == 0:
        return None
    last = int(idxs[-1])
    bars_ago = len(close) - 1 - last
    if bars_ago < min_bars_ago or bars_ago > max_bars_ago:
        return None
    return last


def compute_fib_setup(
    ohlcv: pd.DataFrame,
    gc_idx: int,
) -> FibSwingSetup | None:
    """GC 이후 스윙 고/저 기준 피보나치 되돌림 타점 계산."""
    if gc_idx < 0 or gc_idx >= len(ohlcv):
        return None
    high_s = pd.to_numeric(ohlcv["high"], errors="coerce")
    low_s = pd.to_numeric(ohlcv["low"], errors="coerce")
    seg_high = high_s.iloc[gc_idx:]
    seg_low = low_s.iloc[gc_idx:]
    if seg_high.dropna().empty or seg_low.dropna().empty:
        return None

    rel_peak = int(seg_high.to_numpy().argmax())
    peak_idx = gc_idx + rel_peak
    swing_high = float(high_s.iloc[peak_idx])
    swing_low = float(low_s.iloc[gc_idx : peak_idx + 1].min())
    if not np.isfinite(swing_high) or not np.isfinite(swing_low):
        return None
    if swing_high <= swing_low:
        return None

    span = swing_high - swing_low
    fib_prices = tuple(swing_high - span * r for r in FIB_RATIOS)
    gc_ts = pd.Timestamp(ohlcv.index[gc_idx]).normalize()
    return FibSwingSetup(
        gc_date=gc_ts,
        swing_high=swing_high,
        swing_low=swing_low,
        fib_prices=fib_prices,  # type: ignore[arg-type]
    )


def _near_fib_level(close: float, fib_price: float, tolerance: float = FIB_TOLERANCE) -> bool:
    if not np.isfinite(close) or not np.isfinite(fib_price) or fib_price <= 0:
        return False
    return abs(close - fib_price) / fib_price <= tolerance


def detect_tranche_signal(
    close: float,
    tranches_filled: int,
    setup: FibSwingSetup,
) -> FibTrancheSignal | None:
    """분할 매수 격발 여부. tranches_filled=0이면 1차(0.382)만."""
    if tranches_filled < 0 or tranches_filled >= len(FIB_RATIOS):
        return None
    ratio = FIB_RATIOS[tranches_filled]
    fib_price = setup.fib_prices[tranches_filled]
    if not _near_fib_level(close, fib_price):
        return None
    return FibTrancheSignal(
        tranche_index=tranches_filled,
        fib_ratio=ratio,
        fib_price=fib_price,
        amount_krw=float(TRANCHE_AMOUNTS_KRW[tranches_filled]),
    )


def evaluate_exit(
    *,
    close: float,
    high: float,
    low: float,
    avg_entry: float,
    swing_high: float,
    swing_low: float,
    tranches_filled: int,
    partial_tp_done: bool,
    risk_free: bool,
    breakeven_stop: float,
) -> tuple[str, float] | None:
    """
    청산 판정. (exit_type, exit_price) 또는 None.
    우선순위: 부분익절 → 본전손절 → 전량손절
    """
    if not np.isfinite(close) or close <= 0:
        return None

    if not partial_tp_done and tranches_filled >= 1 and np.isfinite(swing_high):
        if close > swing_high:
            return ("PARTIAL_TP_50", close)

    if risk_free and np.isfinite(breakeven_stop) and breakeven_stop > 0:
        if low <= breakeven_stop:
            return ("RISK_FREE_BREAKEVEN", breakeven_stop)

    if tranches_filled >= len(FIB_RATIOS) and np.isfinite(avg_entry) and np.isfinite(swing_low):
        if close < swing_low or low < swing_low:
            return ("STOP_SWING_LOW", min(close, swing_low))
        risk = avg_entry - swing_low
        if risk > 0:
            stop_1_2 = avg_entry - 2.0 * risk
            if close <= stop_1_2 or low <= stop_1_2:
                return ("STOP_RR_1_2", min(close, stop_1_2))

    return None


def build_fib_setup_from_history(ohlcv: pd.DataFrame) -> FibSwingSetup | None:
    """일봉 OHLCV에서 GC + 피보나치 셋업 일괄 산출."""
    if ohlcv is None or ohlcv.empty:
        return None
    close_s = pd.to_numeric(ohlcv["close"], errors="coerce")
    gc_idx = find_golden_cross_index(close_s)
    if gc_idx is None:
        return None
    return compute_fib_setup(ohlcv, gc_idx)


def scan_swing_universe(*, project_root: str | None = None) -> list[str]:
    """KOSPI200·KOSDAQ150 편입 + 시총 5,000억+ 우량 대형주 유니버스."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from src.engine.v10_live_core import scan_large_cap_universe

    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    index_codes = load_index_members(today)
    if not index_codes:
        return scan_large_cap_universe(
            min_mcap=MIN_MCAP_KRW,
            min_trade_amt=50_000_000_000,
            markets=("KOSPI", "KOSDAQ"),
            top_n=80,
            project_root=project_root,
            preset_label="swing",
        )

    from src.engine.v10_live_core import _project_root
    from src.live.live_config import load_live_config, resolve_live_paths
    import json
    import os

    root = _project_root(project_root)
    cfg = load_live_config()
    paths = resolve_live_paths(cfg, str(root))
    codes = sorted(index_codes)
    os.makedirs(os.path.dirname(paths["universe_json"]) or ".", exist_ok=True)
    with open(paths["universe_json"], "w", encoding="utf-8") as fh:
        json.dump(codes, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return codes


def load_index_members(as_of_date: str) -> frozenset[str]:
    """KOSPI200·KOSDAQ150 편입 종목 (pykrx). 실패 시 빈 집합."""
    try:
        from pykrx import stock as pykrx_stock  # type: ignore
    except Exception:
        return frozenset()

    ymd = pd.Timestamp(str(as_of_date).strip()[:10]).strftime("%Y%m%d")
    codes: set[str] = set()
    for ticker in (KOSPI200_INDEX, KOSDAQ150_INDEX):
        try:
            df = pykrx_stock.get_index_portfolio_deposit_file(ymd, ticker)
            if df is None or getattr(df, "empty", True):
                continue
            col = "종목코드" if "종목코드" in df.columns else df.columns[0]
            for raw in df[col].astype(str):
                codes.add(str(raw).zfill(6))
        except Exception:
            continue
    return frozenset(codes)


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    rename = {}
    for c in work.columns:
        cl = str(c).lower()
        if cl in ("open", "high", "low", "close", "volume"):
            rename[c] = cl
    if rename:
        work = work.rename(columns=rename)
    for std, alt in (("close", "Close"), ("high", "High"), ("low", "Low")):
        if std not in work.columns and alt in work.columns:
            work = work.rename(columns={alt: std})
    return work.sort_index()


def detect_swing_entry(ohlcv_df: pd.DataFrame, *, tranches_filled: int = 0) -> tuple[bool, str]:
    """장기 정배열 + 피보나치 분할 매수 타점 (0.382/0.500/0.618)."""
    ok, msg, _ = resolve_swing_entry_order(ohlcv_df, tranches_filled=tranches_filled)
    return ok, msg


def resolve_swing_entry_order(
    ohlcv_df: pd.DataFrame,
    *,
    tranches_filled: int = 0,
) -> tuple[bool, str, float]:
    """분할 매수 격발 여부 + 1:1:2 금액(원). tranches_filled=0 → 1차 125,000원."""
    setup = build_fib_setup_from_history(_normalize_ohlcv(ohlcv_df))
    if setup is None:
        return False, "골든크로스·피보나치 셋업 없음", 0.0

    close_s = pd.to_numeric(_normalize_ohlcv(ohlcv_df)["close"], errors="coerce")
    today_close = float(close_s.iloc[-1])
    sig = detect_tranche_signal(today_close, tranches_filled, setup)
    if sig is None:
        ratio = FIB_RATIOS[tranches_filled] if tranches_filled < len(FIB_RATIOS) else 0
        return False, f"피보나치 {ratio:.3f} 레벨 미접촉", 0.0
    return True, f"통과 — FIB {sig.fib_ratio:.3f} @ {sig.fib_price:,.0f}", float(sig.amount_krw)


def evaluate_swing_exit(
    *,
    close: float,
    high: float,
    low: float,
    avg_entry: float,
    state: dict,
) -> tuple[str, float, float] | None:
    """Risk-Free 스윙 청산 → (exit_type, price, sell_ratio)."""
    result = evaluate_exit(
        close=close,
        high=high,
        low=low,
        avg_entry=avg_entry,
        swing_high=float(state.get("swing_high") or 0),
        swing_low=float(state.get("swing_low") or 0),
        tranches_filled=int(state.get("tranches_filled") or 1),
        partial_tp_done=bool(state.get("partial_tp_done")),
        risk_free=bool(state.get("risk_free")),
        breakeven_stop=float(state.get("breakeven_stop") or 0),
    )
    if result is None:
        return None
    exit_type, price = result
    if exit_type == "PARTIAL_TP_50":
        return ("SWING_PARTIAL_TP_50", price, 0.5)
    return (exit_type, price, 1.0)


@dataclass
class FibSwingEngine(V10PresetEngineBase):
    """v10.0 횡보장 Swing 프리셋 라이브 엔진."""

    preset: str = "swing"
    entry_time: str = "15:20"

    def entry_signal(self, ohlcv_df: pd.DataFrame) -> tuple[bool, str]:
        return detect_swing_entry(ohlcv_df, tranches_filled=0)

    def supports_tranche_add(self) -> bool:
        return True

    def resolve_entry_order(
        self,
        ohlcv_df: pd.DataFrame,
        *,
        state: dict | None = None,
    ) -> tuple[bool, str, float]:
        tranches_filled = int(state.get("tranches_filled", 0)) if state else 0
        return resolve_swing_entry_order(ohlcv_df, tranches_filled=tranches_filled)

    def on_tranche_fill(self, state: dict[str, Any], *, tranches_filled: int) -> dict[str, Any]:
        state = dict(state)
        state["tranches_filled"] = tranches_filled
        return state

    def scan_universe(self) -> list[str]:
        return scan_swing_universe(project_root=self.project_root)

    def evaluate_exit(
        self,
        *,
        ohlcv_df: pd.DataFrame,
        entry_price: float,
        state: dict,
        bar: dict[str, float],
    ) -> tuple[str, float, float] | None:
        return evaluate_swing_exit(
            close=bar["close"],
            high=bar["high"],
            low=bar["low"],
            avg_entry=entry_price,
            state=state,
        )

    def init_position_state(self, ohlcv_df: pd.DataFrame) -> dict[str, Any]:
        setup = build_fib_setup_from_history(_normalize_ohlcv(ohlcv_df))
        if setup is None:
            return {"tranches_filled": 1}
        return {
            "swing_high": setup.swing_high,
            "swing_low": setup.swing_low,
            "fib_prices": setup.fib_prices,
            "tranches_filled": 1,
            "partial_tp_done": False,
            "risk_free": False,
            "breakeven_stop": 0.0,
        }

    def on_partial_exit(self, state: dict[str, Any], entry_price: float) -> dict[str, Any]:
        state = dict(state)
        state["partial_tp_done"] = True
        state["risk_free"] = True
        state["breakeven_stop"] = entry_price
        return state

    def run_1520_routine(self) -> None:
        import logging

        logging.getLogger("FibSwingEngine").info(
            "[🛡️ 횡보장 스윙] 우량 대형주 피보나치 바닥 낚시질 시작..."
        )
        self.run_master_loop()

