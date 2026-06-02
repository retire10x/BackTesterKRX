from __future__ import annotations

import os
import pickle
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import FinanceDataReader as fdr
import numpy as np
import pandas as pd

from src.data_loader import _load_ohlcv_pykrx_by_date, _normalize_pykrx_ohlcv_columns, ensure_datetime_index
from src.data_loader import _fetch_bulk_ohlcv_day_frames, _merge_multi_market_bulk_day_frames
from src.data_loader import fetch_listing_market_cap_krw_by_code
from src.engine.smart_money_cascade import (
    PHASE_I_ANCHOR_TOP_N,
    PHASE_I_MIN_ANCHOR_TRADE_KRW,
    PHASE_I_VOLUME_DRY_RATIO,
    STAGE_ALLOCATIONS,
    compute_stage_invest_amount,
    evaluate_daily_exit,
    scan_phase_i_kosdaq_universe,
    scan_smart_money_universe,
    stage_entry_triggered,
)
from src.v4_config import V4Config, load_v4_config

WARM_BDAYS = 30
_CACHE_ROOT = Path(__file__).resolve().parents[2] / "data" / "cache"


def _default_v4_config() -> V4Config:
    return load_v4_config()


@dataclass
class OpenPosition:
    code: str
    stage: int
    entry_date: pd.Timestamp
    entry_price: float
    invest_amount: float
    hold_days: int = 0
    trade_id: int = 0
    slot_budget_at_entry: float = 0.0
    alloc_ratio: float = 0.0


@dataclass
class TrackedStock:
    code: str
    stage: int = 1
    smart_money_date: pd.Timestamp | None = None
    anchor_close: float | None = None
    anchor_volume_amt: float | None = None
    next_entry_day_idx: int = 0
    completed: bool = False


def _load_kosdaq_code_set() -> frozenset[str]:
    """FDR 상장표 기준 코스닥 종목 코드 집합."""
    listing = fdr.StockListing("KRX")
    if listing is None or listing.empty or "Code" not in listing.columns:
        listing = fdr.StockListing("KOSDAQ")
    if listing is None or listing.empty:
        return frozenset()
    codes = listing["Code"].astype(str).str.strip().str.zfill(6)
    if "Market" in listing.columns:
        m = listing["Market"].astype(str).str.upper()
        mask = m.str.contains("KOSDAQ", na=False) | m.str.contains("KQ", na=False)
        return frozenset(codes[mask].tolist())
    return frozenset(codes.tolist())


@dataclass
class PortfolioResult:
    metrics: dict[str, Any]
    equity_curve: pd.DataFrame
    trades: pd.DataFrame
    trades_detail: pd.DataFrame
    pass_logs: list[str] = field(default_factory=list)


TRADES_DETAIL_COLUMNS = [
    "trade_id",
    "side",
    "timestamp",
    "code",
    "stage",
    "entry_date",
    "entry_price",
    "exit_price",
    "qty",
    "invest_amount",
    "proceeds",
    "pnl_amount",
    "pnl_rate",
    "exit_type",
    "cash_after",
    "total_equity_after",
    "open_slots_after",
    "slot_budget_at_entry",
    "alloc_ratio",
]


def _panel_cache_path(start_date: str, end_date: str, warm_bdays: int) -> Path:
    sd = pd.Timestamp(str(start_date).strip()[:10]).strftime("%Y%m%d")
    ed = pd.Timestamp(str(end_date).strip()[:10]).strftime("%Y%m%d")
    return _CACHE_ROOT / f"v4_portfolio_panel_{sd}_{ed}_w{warm_bdays}.pkl"


def _load_ticker_panel(
    warm_sd: pd.Timestamp,
    ed: pd.Timestamp,
    *,
    cache_path: Path,
) -> dict[str, pd.DataFrame]:
    if cache_path.is_file():
        try:
            with cache_path.open("rb") as fh:
                cached = pickle.load(fh)
            if isinstance(cached, dict) and cached:
                print(f"♻️  패널 캐시 로드: {cache_path.name} ({len(cached)} 종목)")
                return cached
        except Exception:
            pass

    listing = fdr.StockListing("KRX")
    codes = (
        listing["Code"]
        .astype(str)
        .str.strip()
        .str.zfill(6)
        .tolist()
    )
    warm_s = warm_sd.strftime("%Y-%m-%d")
    end_s = ed.strftime("%Y-%m-%d")

    panel: dict[str, pd.DataFrame] = {}
    total = len(codes)
    max_workers = min(8, max(1, (os.cpu_count() or 4)))
    print(f"📥 종목별 OHLCV 패널 구축 시작 ({total} 종목, workers={max_workers})...", flush=True)

    def _fetch_one(code: str) -> tuple[str, pd.DataFrame | None]:
        raw = _load_ohlcv_pykrx_by_date(code, warm_s, end_s)
        if raw is None or raw.empty:
            return code, None
        df = ensure_datetime_index(_normalize_pykrx_ohlcv_columns(raw))
        if len(df) < 5:
            return code, None
        return code, df

    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_fetch_one, code) for code in codes]
        for fut in as_completed(futures):
            done += 1
            if done == 1 or done % 100 == 0 or done == total:
                print(f"   ... {done}/{total}", flush=True)
            code, df = fut.result()
            if df is not None:
                panel[code] = df

    if not panel:
        raise RuntimeError("종목별 OHLCV 패널 구축 실패 — pykrx 수집망을 확인하세요.")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("wb") as fh:
        pickle.dump(panel, fh, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"💾 패널 캐시 저장: {cache_path.name} ({len(panel)} 종목)")
    return panel


def _day_frames_from_panel(
    panel: dict[str, pd.DataFrame],
    bdays: pd.DatetimeIndex,
) -> list[pd.DataFrame]:
    day_frames: list[pd.DataFrame] = []
    for d_ts in bdays:
        dt = pd.Timestamp(d_ts).normalize()
        rows: dict[str, dict[str, float]] = {}
        for code, df in panel.items():
            if dt not in df.index:
                continue
            row = df.loc[dt]
            rows[code] = {
                "Open": float(row["open"]),
                "High": float(row["high"]),
                "Low": float(row["low"]),
                "Close": float(row["close"]),
                "Volume": float(row["volume"]),
            }
        day_frames.append(
            pd.DataFrame.from_dict(rows, orient="index").sort_index()
            if rows
            else pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        )
    return day_frames


def load_merged_market_day_frames(
    start_date: str,
    end_date: str,
    *,
    warm_bdays: int = WARM_BDAYS,
    force_bulk: bool = False,
) -> tuple[list[pd.DataFrame], pd.DatetimeIndex]:
    """KOSPI+KOSDAQ 일별 전종목 OHLCV 벌크 프레임 로드 (벌크 실패 시 종목 패널 폴백)."""
    sd = pd.Timestamp(str(start_date).strip()[:10]).normalize()
    ed = pd.Timestamp(str(end_date).strip()[:10]).normalize()
    warm_sd = sd - pd.offsets.BDay(max(1, int(warm_bdays)))
    bdays = pd.bdate_range(warm_sd, ed)
    if bdays.empty:
        raise RuntimeError("영업일 캘린더가 비어 있습니다.")

    try:
        from pykrx import stock as pykrx_stock  # type: ignore

        anchor_ymd = ed.strftime("%Y%m%d")
        per_market: list[list[pd.DataFrame]] = []
        for market in ("KOSPI", "KOSDAQ"):
            frames = _fetch_bulk_ohlcv_day_frames(
                pykrx_stock=pykrx_stock,
                market=market,
                bdays=bdays,
                anchor_ymd=anchor_ymd,
                cancel_event=None,
            )
            if frames is None:
                raise RuntimeError(f"{market} 벌크 OHLCV 로드 실패")
            per_market.append(frames)

        merged = _merge_multi_market_bulk_day_frames(per_market)
        if merged is None:
            raise RuntimeError("시장 병합 OHLCV 로드 실패")
        print("✅ pykrx 벌크 일별 스냅샷 로드 완료")
        return merged, bdays
    except Exception as bulk_exc:
        if force_bulk:
            raise RuntimeError(f"벌크 로드 실패(force_bulk=True): {bulk_exc}") from bulk_exc
        print(f"⚠️  벌크 로드 불가 — 종목 패널 폴백 전환 ({bulk_exc})")

    cache_path = _panel_cache_path(start_date, end_date, warm_bdays)
    panel = _load_ticker_panel(warm_sd, ed, cache_path=cache_path)
    merged = _day_frames_from_panel(panel, bdays)
    return merged, bdays


def _market_snapshot_for_scan(day_frame: pd.DataFrame) -> pd.DataFrame:
    snap = day_frame.copy()
    snap.index = snap.index.map(lambda x: str(x).zfill(6))
    snap = snap.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    out = snap.reset_index()
    if len(out.columns) > 0:
        out = out.rename(columns={out.columns[0]: "code"})
    out["code"] = out["code"].astype(str).str.zfill(6)
    return out[["code", "open", "high", "low", "close", "volume"]]


class PortfolioManager:
    """v4.0 스마트머니 연쇄 청산 — 일자별 자산·슬롯 통제 포트폴리오 시뮬레이터."""

    def __init__(
        self,
        day_frames: list[pd.DataFrame],
        bdays: pd.DatetimeIndex,
        *,
        start_date: str,
        end_date: str,
        initial_equity: float | None = None,
        max_slots: int | None = None,
        allowed_codes: frozenset[str] | set[str] | None = None,
        anchor_first_smart_money_only: bool = False,
        preload_ohlcv: dict[str, pd.DataFrame] | None = None,
        phase_g_mode: bool = True,
        phase_h_mode: bool = False,
        phase_i_mode: bool = False,
        phase_h_sl_ratio: float | None = None,
        phase_h_tp_ratio: float | None = None,
        phase_h_fixed_amount: float | None = None,
        phase_h_emperor_price_ratio: float | None = None,
        phase_i_volume_dry_ratio: float | None = None,
        phase_i_min_anchor_trade_krw: float | None = None,
        phase_i_anchor_top_n: int | None = None,
        v4_config: V4Config | None = None,
    ):
        cfg = v4_config if v4_config is not None else _default_v4_config()
        strat = cfg.strategy
        port = cfg.portfolio
        costs = cfg.costs

        self.day_frames = day_frames
        self.bdays = pd.DatetimeIndex(bdays).normalize()
        self.start_date = pd.Timestamp(str(start_date).strip()[:10]).normalize()
        self.end_date = pd.Timestamp(str(end_date).strip()[:10]).normalize()
        self.v4_config = cfg
        self.initial_equity = float(
            port.initial_cash if initial_equity is None else initial_equity
        )
        self.max_slots = int(port.max_slots if max_slots is None else max_slots)
        self.allowed_codes = (
            frozenset(str(c).zfill(6) for c in allowed_codes) if allowed_codes else None
        )
        self.anchor_first_smart_money_only = bool(anchor_first_smart_money_only)
        self.phase_g_mode = bool(phase_g_mode) and not bool(phase_i_mode)
        self.phase_h_mode = bool(phase_h_mode) and not bool(phase_i_mode)
        self.phase_i_mode = bool(phase_i_mode)
        if self.phase_i_mode and self.phase_h_mode:
            raise ValueError("phase_h_mode와 phase_i_mode는 동시에 켤 수 없습니다.")
        self.nuliim_ratio = float(strat.nuliim_ratio)
        self.fixed_invest_amount = float(strat.fixed_invest_amount)
        self.stop_loss_ratio = float(strat.stop_loss_ratio)
        self.target_profit_ratio = float(strat.target_profit_ratio)
        self.max_hold_days = int(strat.max_hold_days)
        self.min_invest_amount = float(strat.min_invest_amount)
        self.max_track_days = int(strat.max_track_days)
        self.max_daily_cash_deploy_ratio = float(strat.max_daily_cash_deploy_ratio)
        self.buy_fee_rate = float(costs.buy_fee)
        self.sell_cost_rate = float(costs.sell_fee_tax)
        self.environment_mode = str(cfg.environment_mode).strip().lower()
        self._anchored_codes: set[str] = set()

        self.cash = float(self.initial_equity)
        self.positions: dict[str, OpenPosition] = {}
        self.tracked: dict[str, TrackedStock] = {}
        self.stock_history: dict[str, pd.DataFrame] = {}
        if preload_ohlcv:
            self.preload_stock_histories(preload_ohlcv)

        self.trade_rows: list[dict[str, Any]] = []
        self.trade_detail_rows: list[dict[str, Any]] = []
        self.equity_rows: list[dict[str, Any]] = []
        self.pass_logs: list[str] = []
        self._trade_id_counter = 0
        self._day_deployed_cash = 0.0
        self.phase_g_same_day_entries = 0
        # Phase H — YAML SSOT (동결: h2_sl03_tp10_ec20)
        if phase_h_sl_ratio is not None:
            self.phase_h_sl_ratio = float(phase_h_sl_ratio)
        elif self.phase_h_mode or self.phase_i_mode:
            self.phase_h_sl_ratio = float(strat.stop_loss_ratio)
        else:
            self.phase_h_sl_ratio = 0.03
        if phase_h_tp_ratio is not None:
            self.phase_h_tp_ratio = float(phase_h_tp_ratio)
        elif self.phase_h_mode or self.phase_i_mode:
            self.phase_h_tp_ratio = float(strat.target_profit_ratio)
        else:
            self.phase_h_tp_ratio = 0.10
        self.phase_i_volume_dry_ratio = (
            float(phase_i_volume_dry_ratio)
            if phase_i_volume_dry_ratio is not None
            else PHASE_I_VOLUME_DRY_RATIO
        )
        self.phase_i_min_anchor_trade_krw = (
            float(phase_i_min_anchor_trade_krw)
            if phase_i_min_anchor_trade_krw is not None
            else PHASE_I_MIN_ANCHOR_TRADE_KRW
        )
        self.phase_i_anchor_top_n = (
            int(phase_i_anchor_top_n)
            if phase_i_anchor_top_n is not None
            else PHASE_I_ANCHOR_TOP_N
        )
        default_h_fixed = (
            float(strat.field_test_invest_amount)
            if self.environment_mode == "field_test" or self.phase_h_mode or self.phase_i_mode
            else 3_000_000.0
        )
        self.phase_h_fixed_amount = float(phase_h_fixed_amount) if phase_h_fixed_amount is not None else default_h_fixed
        self.phase_h_min_wait_bdays = int(strat.phase_h_min_wait_bdays)
        self.phase_h_local_bottom_lookback = 20  # H-2: 로컬 저점 20영업일
        if phase_h_emperor_price_ratio is not None:
            self.phase_h_emperor_price_ratio = float(phase_h_emperor_price_ratio)
        elif self.phase_h_mode:
            self.phase_h_emperor_price_ratio = float(strat.emperor_cap_ratio)
        else:
            self.phase_h_emperor_price_ratio = 0.30
        self.phase_h_time_stop_days = int(strat.max_hold_days)
        self.phase_h_stock_price_ceiling = float(strat.stock_price_ceiling)
        self.phase_h_stock_price_floor = float(strat.stock_price_floor)
        self._kosdaq_codes: frozenset[str] = frozenset()
        self._marcap_kosdaq: dict[str, float] = {}
        if self.phase_i_mode:
            self._kosdaq_codes = _load_kosdaq_code_set()
            self._marcap_kosdaq = fetch_listing_market_cap_krw_by_code("KOSDAQ")
            if not self._marcap_kosdaq:
                self._marcap_kosdaq = fetch_listing_market_cap_krw_by_code("KRX")
            print(
                f"📌 Phase I: 코스닥 {len(self._kosdaq_codes)}종목 · "
                f"시총맵 {len(self._marcap_kosdaq)}건 · SL -{self.phase_h_sl_ratio:.0%} / TP +{self.phase_h_tp_ratio:.0%}"
            )

        self._sim_start_idx = int(self.bdays.get_indexer([self.start_date], method="bfill")[0])
        self._sim_end_idx = int(self.bdays.get_indexer([self.end_date], method="ffill")[0])
        if self._sim_start_idx < 0 or self._sim_end_idx < 0:
            raise ValueError("시뮬레이션 기간이 벌크 데이터 범위 밖입니다.")

    def preload_stock_histories(self, ohlcv_by_code: dict[str, pd.DataFrame]) -> None:
        """패리티/단일 엔진 정합: 종목별 전체 OHLCV를 stock_history에 선적재."""
        from src.engine.smart_money_cascade import _normalize_ohlcv_columns

        for code, df in ohlcv_by_code.items():
            c6 = str(code).zfill(6)
            if df is None or df.empty:
                continue
            work = _normalize_ohlcv_columns(ensure_datetime_index(df.copy()))
            work.index = pd.DatetimeIndex(work.index).normalize()
            self.stock_history[c6] = work

    def _code_allowed(self, code: str) -> bool:
        c6 = str(code).zfill(6)
        if self.allowed_codes is not None and c6 not in self.allowed_codes:
            return False
        return True

    def _ohlcv_history_as_of(self, code: str, day_idx: int) -> pd.DataFrame | None:
        """당일 종가 시점까지의 OHLCV만 사용(선적재 시 미래 봉 참조 방지)."""
        c6 = str(code).zfill(6)
        hist = self.stock_history.get(c6)
        if hist is None or hist.empty:
            return None
        dt = pd.Timestamp(self.bdays[day_idx]).normalize()
        sub = hist.loc[hist.index <= dt].copy()
        if len(sub) < 2:
            return None
        return sub

    @property
    def open_slot_count(self) -> int:
        return len(self.positions)

    @property
    def available_slots(self) -> int:
        return max(0, self.max_slots - self.open_slot_count)

    def _slot_budget(self, total_equity: float) -> float:
        if self.phase_h_mode or self.phase_i_mode:
            return self.phase_h_fixed_amount
        if self.phase_g_mode:
            return self.fixed_invest_amount
        return total_equity / self.max_slots

    def _get_daily_ohlcv(self, code: str, day_idx: int) -> dict[str, float] | None:
        c6 = str(code).zfill(6)
        day_frame = self.day_frames[day_idx]
        if c6 not in day_frame.index:
            return None
        row = day_frame.loc[c6]
        return {
            "Open": float(row["Open"]),
            "High": float(row["High"]),
            "Low": float(row["Low"]),
            "Close": float(row["Close"]),
            "Volume": float(row["Volume"]),
        }

    def _compute_fixed_invest_amount(self) -> float:
        """Phase G 단리: 슬롯당 고정 금액, 잔고 부족 시 95%·최소 투자금."""
        invest = (
            self.phase_h_fixed_amount
            if (self.phase_h_mode or self.phase_i_mode)
            else self.fixed_invest_amount
        )
        if self.cash < invest:
            invest = self.cash * 0.95
        if invest < self.min_invest_amount:
            return 0.0
        return invest

    def _phase_h_bdays_since_anchor(self, tracked: TrackedStock, day_idx: int) -> int | None:
        """기준봉 대비 경과 **영업일** 수 (달력일 .days 사용 금지 — H-2)."""
        if tracked.smart_money_date is None:
            return None
        anchor_idx = int(
            self.bdays.get_indexer([tracked.smart_money_date.normalize()], method="bfill")[0]
        )
        if anchor_idx < 0:
            return None
        return day_idx - anchor_idx

    def _phase_h_tactical_filter(
        self,
        tracked: TrackedStock,
        code: str,
        day_idx: int,
        current_close: float,
    ) -> bool:
        """
        [Phase H-2] 황제주 가격 필터 + 영업일 관망 + 20일 로컬 쌍바닥 지지.
        """
        if not np.isfinite(current_close) or current_close <= 0:
            return False

        # 1) 황제주: 1주 가격이 슬롯 베팅금의 30% 초과 시 수량 누수 방지
        max_share_px = self.phase_h_fixed_amount * self.phase_h_emperor_price_ratio
        if current_close > max_share_px:
            return False

        # 2) 시간 축: 기준봉 이후 최소 5영업일 박스 횡보 관망
        bdays_since = self._phase_h_bdays_since_anchor(tracked, day_idx)
        if bdays_since is None or bdays_since < self.phase_h_min_wait_bdays:
            return False

        hist = self._ohlcv_history_as_of(code, day_idx)
        min_bars = max(25, self.phase_h_local_bottom_lookback + 5)
        if hist is None or len(hist) < min_bars:
            return False
        if not {"close", "low"}.issubset(set(hist.columns)):
            return False

        close_s = pd.to_numeric(hist["close"], errors="coerce")
        low_s = pd.to_numeric(hist["low"], errors="coerce")
        if close_s.isna().all() or low_s.isna().all():
            return False

        current_low = float(low_s.iloc[-1])
        ma_5 = float(close_s.rolling(window=5).mean().iloc[-1])
        ma_10 = float(close_s.rolling(window=10).mean().iloc[-1])
        ma_20 = float(close_s.rolling(window=20).mean().iloc[-1])

        if not np.isfinite(current_low):
            return False
        if not np.isfinite(ma_5) or not np.isfinite(ma_10) or not np.isfinite(ma_20):
            return False

        # 3) 5일선 아래(추격 방지)
        if current_close >= ma_5:
            return False
        # 4) MA10/20 수렴권
        if not (ma_20 * 0.97 <= current_close <= ma_10 * 1.03):
            return False

        # 5) 최근 20영업일(오늘 제외) 로컬 저점 — 쌍바닥 지지
        lookback = min(self.phase_h_local_bottom_lookback, len(hist) - 1)
        if lookback < 5:
            return False
        past_days = hist.iloc[-(lookback + 1):-1]
        real_first_bottom = float(pd.to_numeric(past_days["low"], errors="coerce").min())
        if not np.isfinite(real_first_bottom) or real_first_bottom <= 0:
            return False

        lower = real_first_bottom * 0.99
        upper = real_first_bottom * 1.03
        return (lower <= current_close <= upper) or (lower <= current_low <= upper)

    def _phase_h_entry_allowed(
        self,
        tracked: TrackedStock,
        code: str,
        day_idx: int,
        current_close: float,
    ) -> bool:
        """
        H-3 진입 게이트:
        1) 주가 상하한 캡(연산 경량화)
        2) Phase H 쌍바닥 전술 필터
        """
        if not np.isfinite(current_close) or current_close <= 0:
            return False
        if current_close < self.phase_h_stock_price_floor:
            return False
        if current_close > self.phase_h_stock_price_ceiling:
            return False
        return self._phase_h_tactical_filter(tracked, code, day_idx, current_close)

    def _phase_i_entry_allowed(
        self,
        tracked: TrackedStock,
        code: str,
        day_idx: int,
        current_close: float,
    ) -> bool:
        """
        Phase I: 코스닥 시총 밴드(기준봉 스캔) + 거래량 실종(기준봉 대비 15% 이하) + H-2 쌍바닥.
        """
        if not np.isfinite(current_close) or current_close <= 0:
            return False
        if current_close < self.phase_h_stock_price_floor:
            return False
        if current_close > self.phase_h_stock_price_ceiling:
            return False

        anchor_amt = tracked.anchor_volume_amt
        if anchor_amt is None or not np.isfinite(anchor_amt) or anchor_amt <= 0:
            return False

        ohlcv = self._get_daily_ohlcv(code, day_idx)
        if ohlcv is None:
            return False
        vol = float(ohlcv["Volume"])
        if not np.isfinite(vol) or vol < 0:
            return False
        today_amt = current_close * vol
        if today_amt > anchor_amt * self.phase_i_volume_dry_ratio:
            return False

        return self._phase_h_tactical_filter(tracked, code, day_idx, current_close)

    def _evaluate_phase_g_exit(
        self,
        *,
        entry_price: float,
        invest_amount: float,
        high: float,
        low: float,
        close: float,
        hold_days: int,
    ) -> tuple[float, float, float, float, str] | None:
        """하드 손절(-5%) → 익절(+3.5%) → 3일 타임스탑 (저가/고가 장중 반영)."""
        target_px = entry_price * (1.0 + self.target_profit_ratio)
        stop_px = entry_price * (1.0 - self.stop_loss_ratio)

        if low <= stop_px:
            exit_price = stop_px
            exit_type = "STOP_LOSS"
        elif high >= target_px:
            exit_price = target_px
            exit_type = "TAKE_PROFIT"
        elif hold_days >= self.max_hold_days:
            exit_price = close
            exit_type = "TIME_STOP"
        else:
            return None

        gross = invest_amount * (exit_price / entry_price)
        net_proceeds = gross * (1.0 - self.sell_cost_rate)
        pnl_amount = net_proceeds - invest_amount
        pnl_rate = pnl_amount / invest_amount if invest_amount > 0 else 0.0
        return exit_price, net_proceeds, pnl_amount, pnl_rate, exit_type

    def _phase_g_entry_allowed(
        self,
        *,
        tracked: TrackedStock,
        trade_date: pd.Timestamp,
        current_close: float,
    ) -> bool:
        """1회차: 기준봉 당일 금지 + 기준봉 종가 대비 -nuliim% 눌림목."""
        if tracked.stage != 1:
            return True
        if tracked.smart_money_date is not None and trade_date.normalize() == tracked.smart_money_date.normalize():
            return False
        anchor = tracked.anchor_close
        if anchor is None or anchor <= 0 or not np.isfinite(current_close):
            return False
        trigger_price = anchor * (1.0 - self.nuliim_ratio)
        return current_close <= trigger_price

    def _legacy_entry_signal(self, code: str, day_idx: int, stage: int) -> bool:
        hist = self._ohlcv_history_as_of(code, day_idx)
        if hist is None:
            return False
        return stage_entry_triggered(hist, stage)

    def _position_market_value(self, code: str, day_idx: int) -> float:
        pos = self.positions.get(code)
        if pos is None:
            return 0.0
        day_frame = self.day_frames[day_idx]
        c6 = str(code).zfill(6)
        if c6 not in day_frame.index:
            return pos.invest_amount
        close_px = float(day_frame.loc[c6, "Close"])
        if not np.isfinite(close_px) or close_px <= 0:
            return pos.invest_amount
        return pos.invest_amount * (close_px / pos.entry_price)

    def _total_equity(self, day_idx: int) -> float:
        mv = sum(self._position_market_value(code, day_idx) for code in self.positions)
        return self.cash + mv

    def _append_trade_detail(
        self,
        *,
        side: str,
        day_idx: int,
        code: str,
        stage: int,
        trade_id: int,
        entry_date: str | None = None,
        entry_price: float | None = None,
        exit_price: float | None = None,
        invest_amount: float | None = None,
        proceeds: float | None = None,
        pnl_amount: float | None = None,
        pnl_rate: float | None = None,
        exit_type: str | None = None,
        slot_budget_at_entry: float | None = None,
        alloc_ratio: float | None = None,
    ) -> None:
        timestamp = pd.Timestamp(self.bdays[day_idx]).normalize().strftime("%Y-%m-%d")
        ep = float(entry_price) if entry_price is not None and np.isfinite(entry_price) else np.nan
        inv = float(invest_amount) if invest_amount is not None and np.isfinite(invest_amount) else np.nan
        qty = inv / ep if np.isfinite(ep) and ep > 0 and np.isfinite(inv) else np.nan

        self.trade_detail_rows.append({
            "trade_id": int(trade_id),
            "side": str(side).upper(),
            "timestamp": timestamp,
            "code": str(code).zfill(6),
            "stage": int(stage),
            "entry_date": entry_date or "",
            "entry_price": ep,
            "exit_price": float(exit_price) if exit_price is not None and np.isfinite(exit_price) else np.nan,
            "qty": qty,
            "invest_amount": inv,
            "proceeds": float(proceeds) if proceeds is not None and np.isfinite(proceeds) else np.nan,
            "pnl_amount": float(pnl_amount) if pnl_amount is not None and np.isfinite(pnl_amount) else np.nan,
            "pnl_rate": float(pnl_rate) if pnl_rate is not None and np.isfinite(pnl_rate) else np.nan,
            "exit_type": exit_type or "",
            "cash_after": float(self.cash),
            "total_equity_after": float(self._total_equity(day_idx)),
            "open_slots_after": int(self.open_slot_count),
            "slot_budget_at_entry": (
                float(slot_budget_at_entry)
                if slot_budget_at_entry is not None and np.isfinite(slot_budget_at_entry)
                else np.nan
            ),
            "alloc_ratio": (
                float(alloc_ratio) if alloc_ratio is not None and np.isfinite(alloc_ratio) else np.nan
            ),
        })

    def _append_history_bar(self, code: str, day_idx: int) -> None:
        c6 = str(code).zfill(6)
        day_frame = self.day_frames[day_idx]
        if c6 not in day_frame.index:
            return
        row = day_frame.loc[c6]
        bar = {
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row["Volume"]),
        }
        dt = pd.Timestamp(self.bdays[day_idx]).normalize()
        if c6 not in self.stock_history:
            self.stock_history[c6] = pd.DataFrame([bar], index=[dt])
            return
        hist = self.stock_history[c6]
        if dt in hist.index:
            hist.loc[dt] = bar
        else:
            self.stock_history[c6] = pd.concat([hist, pd.DataFrame([bar], index=[dt])]).sort_index()

    def _process_exits(self, day_idx: int) -> None:
        """일별 청산 — Phase G: 손절/익절/타임스탑, 레거시: cascade evaluate_daily_exit."""
        trade_date = pd.Timestamp(self.bdays[day_idx]).normalize()
        closed_codes: list[str] = []

        for code, pos in list(self.positions.items()):
            c6 = str(code).zfill(6)
            ohlcv = self._get_daily_ohlcv(c6, day_idx)
            if ohlcv is None:
                continue

            pos.hold_days += 1
            high = ohlcv["High"]
            low = ohlcv["Low"]
            close = ohlcv["Close"]

            if self.phase_h_mode or self.phase_i_mode:
                target_px = pos.entry_price * (1.0 + self.phase_h_tp_ratio)
                stop_px = pos.entry_price * (1.0 - self.phase_h_sl_ratio)
                if low <= stop_px:
                    exit_price = stop_px
                    exit_type = "STOP_LOSS_H"
                elif high >= target_px:
                    exit_price = target_px
                    exit_type = "TAKE_PROFIT_H"
                elif pos.hold_days >= self.phase_h_time_stop_days:
                    exit_price = close
                    exit_type = "TIME_STOP_H"
                else:
                    continue
                gross = pos.invest_amount * (exit_price / pos.entry_price)
                proceeds = gross * (1.0 - self.sell_cost_rate)
                pnl_amount = proceeds - pos.invest_amount
                pnl_rate = pnl_amount / pos.invest_amount if pos.invest_amount > 0 else 0.0
            elif self.phase_g_mode:
                exit_info = self._evaluate_phase_g_exit(
                    entry_price=pos.entry_price,
                    invest_amount=pos.invest_amount,
                    high=high,
                    low=low,
                    close=close,
                    hold_days=pos.hold_days,
                )
                if exit_info is None:
                    continue
                exit_price, proceeds, pnl_amount, pnl_rate, exit_type = exit_info
            else:
                legacy = evaluate_daily_exit(high, close, pos.entry_price, pos.hold_days)
                if legacy is None:
                    continue
                exit_price, pnl_rate, exit_type = legacy
                proceeds = pos.invest_amount * (1.0 + pnl_rate)
                pnl_amount = proceeds - pos.invest_amount

            self.cash += proceeds
            entry_date_s = pos.entry_date.strftime("%Y-%m-%d")

            self.trade_rows.append({
                "code": c6,
                "stage": pos.stage,
                "entry_date": entry_date_s,
                "exit_date": trade_date.strftime("%Y-%m-%d"),
                "entry_price": pos.entry_price,
                "exit_price": exit_price,
                "invest_amount": pos.invest_amount,
                "pnl_amount": pnl_amount,
                "pnl_rate": pnl_rate,
                "exit_type": exit_type,
            })

            self._append_trade_detail(
                side="SELL",
                day_idx=day_idx,
                code=c6,
                stage=pos.stage,
                trade_id=pos.trade_id,
                entry_date=entry_date_s,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                invest_amount=pos.invest_amount,
                proceeds=proceeds,
                pnl_amount=pnl_amount,
                pnl_rate=pnl_rate,
                exit_type=exit_type,
                slot_budget_at_entry=pos.slot_budget_at_entry,
                alloc_ratio=pos.alloc_ratio,
            )

            tracked = self.tracked.get(c6)
            if tracked is not None:
                if self.phase_h_mode or self.phase_i_mode:
                    tracked.completed = True
                elif tracked.stage >= 4:
                    tracked.completed = True
                else:
                    tracked.stage += 1
                    tracked.next_entry_day_idx = day_idx + 1

            closed_codes.append(c6)

        for code in closed_codes:
            self.positions.pop(code, None)

    def _expire_stale_tracked(self, day_idx: int) -> None:
        """D-2: 기준봉 후 N영업일 내 진입 없으면 tracked 제거(유령 큐 방지)."""
        expired: list[str] = []
        for c6, tracked in list(self.tracked.items()):
            if tracked.completed or c6 in self.positions:
                continue
            # 연쇄 2회차 이상 진행 중이면 만료하지 않음 (단일 엔진 cascade와 정합)
            if tracked.stage > 1:
                continue
            if tracked.smart_money_date is None:
                expired.append(c6)
                continue
            sm_idx = int(
                self.bdays.get_indexer([tracked.smart_money_date.normalize()], method="bfill")[0]
            )
            if sm_idx < 0 or (day_idx - sm_idx) > self.max_track_days:
                expired.append(c6)
        for c6 in expired:
            sm = self.tracked[c6].smart_money_date
            self.pass_logs.append(
                f"{pd.Timestamp(self.bdays[day_idx]).date()} {c6} tracked 만료 "
                f"(기준봉 {sm.date() if sm is not None else '?'}, "
                f">{self.max_track_days}영업일)"
            )
            del self.tracked[c6]

    def _register_universe(self, codes: list[str], day_idx: int) -> None:
        trade_date = pd.Timestamp(self.bdays[day_idx]).normalize()
        day_frame = self.day_frames[day_idx]
        for code in codes:
            c6 = str(code).zfill(6)
            if not self._code_allowed(c6):
                continue
            if self.anchor_first_smart_money_only and c6 in self._anchored_codes:
                continue
            if c6 in self.tracked:
                if not self.tracked[c6].completed:
                    continue
                del self.tracked[c6]
            anchor_px = np.nan
            anchor_vol_amt = None
            if c6 in day_frame.index:
                row = day_frame.loc[c6]
                anchor_px = float(row["Close"])
                close_v = float(row["Close"])
                vol_v = float(row["Volume"])
                if np.isfinite(close_v) and np.isfinite(vol_v) and close_v > 0:
                    anchor_vol_amt = close_v * vol_v
            self._anchored_codes.add(c6)
            self.tracked[c6] = TrackedStock(
                code=c6,
                stage=1,
                smart_money_date=trade_date,
                anchor_close=anchor_px if np.isfinite(anchor_px) and anchor_px > 0 else None,
                anchor_volume_amt=anchor_vol_amt,
                next_entry_day_idx=day_idx + 1,
            )

    def _max_daily_deploy_remaining(self) -> float:
        cap = float(self.cash) * self.max_daily_cash_deploy_ratio
        return max(0.0, cap - self._day_deployed_cash)

    def _process_entries(self, day_idx: int, candidate_codes: list[str]) -> None:
        """일별 진입 — Phase G: 눌림목·단리, 레거시: 복리+MA 회차 조건."""
        if self.available_slots <= 0 or self.cash <= 0:
            return

        total_equity = self._total_equity(day_idx)
        slot_budget = self._slot_budget(total_equity)
        trade_date = pd.Timestamp(self.bdays[day_idx]).normalize()
        daily_remaining = self._max_daily_deploy_remaining()
        if daily_remaining <= 0 and not self.phase_g_mode:
            return

        for code in candidate_codes:
            if self.available_slots <= 0 or self.cash <= 0:
                break

            c6 = str(code).zfill(6)
            if c6 in self.positions:
                continue

            tracked = self.tracked.get(c6)
            if tracked is None or tracked.completed:
                continue
            if day_idx < tracked.next_entry_day_idx:
                continue

            ohlcv = self._get_daily_ohlcv(c6, day_idx)
            if ohlcv is None:
                continue
            entry_price = float(ohlcv["Close"])
            if not np.isfinite(entry_price) or entry_price <= 0:
                continue

            if self.phase_i_mode:
                if tracked.stage != 1:
                    continue
                if not self._phase_i_entry_allowed(tracked, c6, day_idx, entry_price):
                    continue
                invest_amount = self._compute_fixed_invest_amount()
                max_affordable = int(invest_amount // entry_price) * entry_price
                if max_affordable < self.min_invest_amount:
                    continue
                invest_amount = min(invest_amount, max_affordable)
                if invest_amount <= 0:
                    continue
                daily_cap = self._max_daily_deploy_remaining()
                if daily_cap <= 0:
                    continue
                invest_amount = min(invest_amount, daily_cap)
                alloc_ratio = (
                    invest_amount / self.phase_h_fixed_amount
                    if self.phase_h_fixed_amount > 0
                    else 1.0
                )
            elif self.phase_h_mode:
                # Phase H: 1회차(기준봉 기반) 단일 진입만 허용
                if tracked.stage != 1:
                    continue
                if not self._phase_h_entry_allowed(
                    tracked, c6, day_idx, entry_price
                ):
                    continue
                invest_amount = self._compute_fixed_invest_amount()
                # H-2: 황제주 수량 누수 — 실제 매수 가능 금액(정수 주수)으로 캡
                max_affordable = int(invest_amount // entry_price) * entry_price
                if max_affordable < self.min_invest_amount:
                    continue
                invest_amount = min(invest_amount, max_affordable)
                if invest_amount <= 0:
                    continue
                daily_cap = self._max_daily_deploy_remaining()
                if daily_cap <= 0:
                    continue
                invest_amount = min(invest_amount, daily_cap)
                alloc_ratio = (
                    invest_amount / self.phase_h_fixed_amount
                    if self.phase_h_fixed_amount > 0 else 1.0
                )
            elif self.phase_g_mode:
                if tracked.stage == 1:
                    if not self._phase_g_entry_allowed(
                        tracked=tracked,
                        trade_date=trade_date,
                        current_close=entry_price,
                    ):
                        continue
                elif not self._legacy_entry_signal(c6, day_idx, tracked.stage):
                    continue
                invest_amount = self._compute_fixed_invest_amount()
                if invest_amount <= 0:
                    continue
                daily_cap = self._max_daily_deploy_remaining()
                if daily_cap <= 0:
                    continue
                invest_amount = min(invest_amount, daily_cap)
                alloc_ratio = invest_amount / self.fixed_invest_amount if self.fixed_invest_amount > 0 else 1.0
            else:
                if not self._legacy_entry_signal(c6, day_idx, tracked.stage):
                    continue
                alloc_ratio = STAGE_ALLOCATIONS[tracked.stage]
                if daily_remaining <= 0:
                    continue
                invest_amount = compute_stage_invest_amount(
                    total_equity=total_equity,
                    max_slots=self.max_slots,
                    stage=tracked.stage,
                    cash=self.cash,
                    available_slots=self.available_slots,
                    max_daily_remaining_cash=self._max_daily_deploy_remaining(),
                )
                if invest_amount <= 0:
                    continue

            if self.cash < invest_amount:
                self.pass_logs.append(
                    f"{trade_date.date()} {c6} {tracked.stage}회차 Pass — "
                    f"현금 부족 (필요 {invest_amount:,.0f} / 보유 {self.cash:,.0f})"
                )
                continue

            if (
                self.phase_g_mode
                and tracked.stage == 1
                and tracked.smart_money_date is not None
                and trade_date.normalize() == tracked.smart_money_date.normalize()
            ):
                self.phase_g_same_day_entries += 1
                continue

            self._trade_id_counter += 1
            trade_id = self._trade_id_counter
            self.cash -= invest_amount
            self._day_deployed_cash += invest_amount
            self.positions[c6] = OpenPosition(
                code=c6,
                stage=tracked.stage,
                entry_date=trade_date,
                entry_price=entry_price,
                invest_amount=invest_amount,
                hold_days=0,
                trade_id=trade_id,
                slot_budget_at_entry=slot_budget,
                alloc_ratio=alloc_ratio,
            )

            self._append_trade_detail(
                side="BUY",
                day_idx=day_idx,
                code=c6,
                stage=tracked.stage,
                trade_id=trade_id,
                entry_date=trade_date.strftime("%Y-%m-%d"),
                entry_price=entry_price,
                invest_amount=invest_amount,
                slot_budget_at_entry=slot_budget,
                alloc_ratio=alloc_ratio,
                exit_type="ENTRY_H" if self.phase_h_mode else ("ENTRY" if self.phase_g_mode else None),
            )

    def run(self) -> PortfolioResult:
        for day_idx in range(self._sim_start_idx, self._sim_end_idx + 1):
            trade_date = pd.Timestamp(self.bdays[day_idx]).normalize()
            self._day_deployed_cash = 0.0

            self._process_exits(day_idx)
            self._expire_stale_tracked(day_idx)

            day_frame = self.day_frames[day_idx]
            market_snap = _market_snapshot_for_scan(day_frame)
            if self.phase_i_mode:
                universe_codes = scan_phase_i_kosdaq_universe(
                    market_snap,
                    self._marcap_kosdaq,
                    self._kosdaq_codes,
                    min_anchor_trade_krw=self.phase_i_min_anchor_trade_krw,
                    top_n=self.phase_i_anchor_top_n,
                )
            else:
                universe_codes = scan_smart_money_universe(market_snap)
            if self.allowed_codes is not None:
                universe_codes = [
                    c for c in universe_codes if str(c).zfill(6) in self.allowed_codes
                ]
            self._register_universe(universe_codes, day_idx)

            ranked = market_snap.copy()
            ranked["close"] = pd.to_numeric(ranked["close"], errors="coerce").astype("float64")
            ranked["volume"] = pd.to_numeric(ranked["volume"], errors="coerce").astype("float64")
            ranked["trading_value"] = ranked["close"] * ranked["volume"]
            rank_map = {
                str(row["code"]).zfill(6): float(row["trading_value"])
                for _, row in ranked.iterrows()
            }
            candidate_codes = sorted(
                [
                    c for c, t in self.tracked.items()
                    if not t.completed
                    and c not in self.positions
                    and self._code_allowed(c)
                ],
                key=lambda c: rank_map.get(c, 0.0),
                reverse=True,
            )
            for code in candidate_codes:
                self._append_history_bar(code, day_idx)
            self._process_entries(day_idx, candidate_codes)

            total_equity = self._total_equity(day_idx)
            self.equity_rows.append({
                "date": trade_date.strftime("%Y-%m-%d"),
                "cash": self.cash,
                "positions_value": total_equity - self.cash,
                "total_equity": total_equity,
                "open_slots": self.open_slot_count,
            })

        equity_curve = pd.DataFrame(self.equity_rows)
        trades = pd.DataFrame(self.trade_rows)
        if self.trade_detail_rows:
            trades_detail = pd.DataFrame(self.trade_detail_rows)[TRADES_DETAIL_COLUMNS]
        else:
            trades_detail = pd.DataFrame(columns=TRADES_DETAIL_COLUMNS)
        metrics = self._compute_metrics(equity_curve, trades, self.initial_equity)
        return PortfolioResult(
            metrics=metrics,
            equity_curve=equity_curve,
            trades=trades,
            trades_detail=trades_detail,
            pass_logs=self.pass_logs,
        )

    @staticmethod
    def _compute_metrics(
        equity_curve: pd.DataFrame,
        trades: pd.DataFrame,
        initial_equity: float,
    ) -> dict[str, Any]:
        base = float(initial_equity)
        if equity_curve.empty:
            final_equity = base
            mdd_pct = 0.0
        else:
            eq = pd.to_numeric(equity_curve["total_equity"], errors="coerce")
            final_equity = float(eq.iloc[-1])
            peak = eq.cummax()
            dd = (eq / peak) - 1.0
            mdd_pct = float(dd.min() * 100.0)

        total_trades = int(len(trades))
        if total_trades == 0:
            return {
                "total_trades": 0,
                "win_rate_pct": 0.0,
                "profit_factor": 0.0,
                "final_equity": final_equity,
                "cumulative_return_pct": (final_equity / base - 1.0) * 100.0,
                "mdd_pct": mdd_pct,
            }

        pnl_amounts = pd.to_numeric(trades["pnl_amount"], errors="coerce").fillna(0.0)
        wins = int((pnl_amounts > 0).sum())
        gross_profit = float(pnl_amounts[pnl_amounts > 0].sum())
        gross_loss = float(abs(pnl_amounts[pnl_amounts < 0].sum()))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        return {
            "total_trades": total_trades,
            "win_rate_pct": wins / total_trades * 100.0,
            "profit_factor": profit_factor,
            "final_equity": final_equity,
            "cumulative_return_pct": (final_equity / base - 1.0) * 100.0,
            "mdd_pct": mdd_pct,
        }
