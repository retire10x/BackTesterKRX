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
from src.engine.smart_money_cascade import (
    MAX_HOLD_DAYS,
    STAGE_ALLOCATIONS,
    evaluate_daily_exit,
    scan_smart_money_universe,
    stage_entry_triggered,
)

INITIAL_EQUITY = 30_000_000
MAX_SLOTS = 3
WARM_BDAYS = 30
_CACHE_ROOT = Path(__file__).resolve().parents[2] / "data" / "cache"


@dataclass
class OpenPosition:
    code: str
    stage: int
    entry_date: pd.Timestamp
    entry_price: float
    invest_amount: float
    hold_days: int = 0


@dataclass
class TrackedStock:
    code: str
    stage: int = 1
    smart_money_date: pd.Timestamp | None = None
    next_entry_day_idx: int = 0
    completed: bool = False


@dataclass
class PortfolioResult:
    metrics: dict[str, Any]
    equity_curve: pd.DataFrame
    trades: pd.DataFrame
    pass_logs: list[str] = field(default_factory=list)


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
        initial_equity: float = INITIAL_EQUITY,
        max_slots: int = MAX_SLOTS,
    ):
        self.day_frames = day_frames
        self.bdays = pd.DatetimeIndex(bdays).normalize()
        self.start_date = pd.Timestamp(str(start_date).strip()[:10]).normalize()
        self.end_date = pd.Timestamp(str(end_date).strip()[:10]).normalize()
        self.initial_equity = float(initial_equity)
        self.max_slots = int(max_slots)

        self.cash = float(initial_equity)
        self.positions: dict[str, OpenPosition] = {}
        self.tracked: dict[str, TrackedStock] = {}
        self.stock_history: dict[str, pd.DataFrame] = {}

        self.trade_rows: list[dict[str, Any]] = []
        self.equity_rows: list[dict[str, Any]] = []
        self.pass_logs: list[str] = []

        self._sim_start_idx = int(self.bdays.get_indexer([self.start_date], method="bfill")[0])
        self._sim_end_idx = int(self.bdays.get_indexer([self.end_date], method="ffill")[0])
        if self._sim_start_idx < 0 or self._sim_end_idx < 0:
            raise ValueError("시뮬레이션 기간이 벌크 데이터 범위 밖입니다.")

    @property
    def open_slot_count(self) -> int:
        return len(self.positions)

    @property
    def available_slots(self) -> int:
        return max(0, self.max_slots - self.open_slot_count)

    def _slot_budget(self, total_equity: float) -> float:
        return total_equity / self.max_slots

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
        trade_date = pd.Timestamp(self.bdays[day_idx]).normalize()
        closed_codes: list[str] = []

        for code, pos in list(self.positions.items()):
            c6 = str(code).zfill(6)
            day_frame = self.day_frames[day_idx]
            if c6 not in day_frame.index:
                continue

            pos.hold_days += 1
            row = day_frame.loc[c6]
            high = float(row["High"])
            close = float(row["Close"])
            exit_info = evaluate_daily_exit(high, close, pos.entry_price, pos.hold_days)
            if exit_info is None:
                continue

            exit_price, pnl_rate, exit_type = exit_info
            proceeds = pos.invest_amount * (1.0 + pnl_rate)
            self.cash += proceeds

            self.trade_rows.append({
                "code": c6,
                "stage": pos.stage,
                "entry_date": pos.entry_date.strftime("%Y-%m-%d"),
                "exit_date": trade_date.strftime("%Y-%m-%d"),
                "entry_price": pos.entry_price,
                "exit_price": exit_price,
                "invest_amount": pos.invest_amount,
                "pnl_amount": proceeds - pos.invest_amount,
                "pnl_rate": pnl_rate,
                "exit_type": exit_type,
            })

            tracked = self.tracked.get(c6)
            if tracked is not None:
                if tracked.stage >= 4:
                    tracked.completed = True
                else:
                    tracked.stage += 1
                    tracked.next_entry_day_idx = day_idx + 1

            closed_codes.append(c6)

        for code in closed_codes:
            self.positions.pop(code, None)

    def _register_universe(self, codes: list[str], day_idx: int) -> None:
        trade_date = pd.Timestamp(self.bdays[day_idx]).normalize()
        for code in codes:
            c6 = str(code).zfill(6)
            if c6 in self.tracked and not self.tracked[c6].completed:
                continue
            if c6 not in self.tracked:
                self.tracked[c6] = TrackedStock(
                    code=c6,
                    stage=1,
                    smart_money_date=trade_date,
                    next_entry_day_idx=day_idx + 1,
                )

    def _process_entries(self, day_idx: int, candidate_codes: list[str]) -> None:
        if self.available_slots <= 0:
            return

        total_equity = self._total_equity(day_idx)
        slot_budget = self._slot_budget(total_equity)
        trade_date = pd.Timestamp(self.bdays[day_idx]).normalize()

        for code in candidate_codes:
            if self.available_slots <= 0:
                break

            c6 = str(code).zfill(6)
            if c6 in self.positions:
                continue

            tracked = self.tracked.get(c6)
            if tracked is None or tracked.completed:
                continue
            if day_idx < tracked.next_entry_day_idx:
                continue

            hist = self.stock_history.get(c6)
            if hist is None or len(hist) < 2:
                continue
            if not stage_entry_triggered(hist, tracked.stage):
                continue

            alloc_ratio = STAGE_ALLOCATIONS[tracked.stage]
            invest_amount = slot_budget * alloc_ratio
            if invest_amount <= 0:
                continue
            if self.cash < invest_amount:
                self.pass_logs.append(
                    f"{trade_date.date()} {c6} {tracked.stage}회차 Pass — "
                    f"현금 부족 (필요 {invest_amount:,.0f} / 보유 {self.cash:,.0f})"
                )
                continue

            day_frame = self.day_frames[day_idx]
            if c6 not in day_frame.index:
                continue
            entry_price = float(day_frame.loc[c6, "Close"])
            if not np.isfinite(entry_price) or entry_price <= 0:
                continue

            self.cash -= invest_amount
            self.positions[c6] = OpenPosition(
                code=c6,
                stage=tracked.stage,
                entry_date=trade_date,
                entry_price=entry_price,
                invest_amount=invest_amount,
                hold_days=0,
            )

    def run(self) -> PortfolioResult:
        for day_idx in range(self._sim_start_idx, self._sim_end_idx + 1):
            trade_date = pd.Timestamp(self.bdays[day_idx]).normalize()

            self._process_exits(day_idx)

            day_frame = self.day_frames[day_idx]
            market_snap = _market_snapshot_for_scan(day_frame)
            universe_codes = scan_smart_money_universe(market_snap)
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
                    if not t.completed and c not in self.positions
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
        metrics = self._compute_metrics(equity_curve, trades)
        return PortfolioResult(
            metrics=metrics,
            equity_curve=equity_curve,
            trades=trades,
            pass_logs=self.pass_logs,
        )

    @staticmethod
    def _compute_metrics(equity_curve: pd.DataFrame, trades: pd.DataFrame) -> dict[str, Any]:
        if equity_curve.empty:
            final_equity = INITIAL_EQUITY
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
                "cumulative_return_pct": (final_equity / INITIAL_EQUITY - 1.0) * 100.0,
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
            "cumulative_return_pct": (final_equity / INITIAL_EQUITY - 1.0) * 100.0,
            "mdd_pct": mdd_pct,
        }
