"""
v5.5.2 장중 감시 — 진입(15:20) · Hit&Run 청산 루프.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import FinanceDataReader as fdr
import pandas as pd

from src.live.live_account import LiveAccountGateway, LivePosition, SlotLockError
from src.live.live_config import LiveTradingConfig, load_live_config, resolve_live_paths
from src.live.live_signals import evaluate_hit_and_run_exit, is_ma_inflection_entry
from src.live.live_screener import load_live_universe, run_live_screener

KST = ZoneInfo("Asia/Seoul")


def _now_kst() -> datetime:
    return datetime.now(KST)


def _parse_hm(hm: str) -> tuple[int, int]:
    h, m = hm.strip().split(":")
    return int(h), int(m)


def _at_or_after(now: datetime, hm: str) -> bool:
    h, m = _parse_hm(hm)
    return now.hour > h or (now.hour == h and now.minute >= m)


def _load_positions(path: str) -> list[LivePosition]:
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    rows = raw.get("positions", raw) if isinstance(raw, dict) else raw
    out: list[LivePosition] = []
    for r in rows:
        out.append(
            LivePosition(
                code=str(r["code"]).zfill(6),
                qty=int(r["qty"]),
                entry_price=float(r["entry_price"]),
                entry_date=str(r["entry_date"])[:10],
                hold_days=int(r.get("hold_days", 0)),
            )
        )
    return out


def _save_positions(path: str, positions: list[LivePosition]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload = {
        "updated_at": _now_kst().isoformat(),
        "positions": [
            {
                "code": p.code,
                "qty": p.qty,
                "entry_price": p.entry_price,
                "entry_date": p.entry_date,
                "hold_days": p.hold_days,
            }
            for p in positions
        ],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def fetch_ohlcv_history(code: str, *, days: int = 160) -> pd.DataFrame:
    c6 = str(code).zfill(6)
    end = _now_kst().strftime("%Y-%m-%d")
    start = (_now_kst() - timedelta(days=days)).strftime("%Y-%m-%d")
    df = fdr.DataReader(c6, start, end)
    if df is None or df.empty:
        return pd.DataFrame()
    df.index = pd.to_datetime(df.index).normalize()
    return df.sort_index()


def fetch_intraday_bar(code: str) -> dict[str, float] | None:
    """당일 봉 근사 — FDR 최신 일봉(실시간은 KIS 시세 API로 교체 가능)."""
    df = fetch_ohlcv_history(code, days=5)
    if df.empty:
        return None
    row = df.iloc[-1]
    close = float(row.get("Close", row.get("close", 0)))
    high = float(row.get("High", row.get("high", close)))
    low = float(row.get("Low", row.get("low", close)))
    if close <= 0:
        return None
    return {"open": close, "high": high, "low": low, "close": close}


class LiveTradingEngine:
    def __init__(
        self,
        *,
        config: LiveTradingConfig | None = None,
        project_root: str | None = None,
        dry_run: bool | None = None,
    ):
        self.cfg = config if config is not None else load_live_config()
        self.root = project_root or str(Path(__file__).resolve().parents[2])
        self.paths = resolve_live_paths(self.cfg, self.root)
        self.gateway = LiveAccountGateway(self.cfg.account, dry_run=dry_run)
        self.strat = self.cfg.strategy

    def run_screener_if_due(self, *, force: bool = False) -> list[str]:
        now = _now_kst()
        if force or _at_or_after(now, self.cfg.screener.screener_time):
            return run_live_screener(config=self.cfg, project_root=self.root)
        print(f"⏳ 스크리너 시각 대기 ({self.cfg.screener.screener_time} KST)")
        return load_live_universe(config=self.cfg, project_root=self.root)

    def run_entry_scan(self, *, force: bool = False) -> None:
        now = _now_kst()
        if not force and not _at_or_after(now, self.strat.entry_time):
            print(f"⏳ 진입 시각 대기 ({self.strat.entry_time} KST)")
            return

        codes = load_live_universe(config=self.cfg, project_root=self.root)
        positions = _load_positions(self.paths["positions_json"])
        snap = self.gateway.get_snapshot(positions)

        print(f"🎯 진입 스캔 {len(codes)}종 · 보유 {snap.open_slot_count}/{self.cfg.account.max_slots}")

        for code in codes:
            if snap.open_slot_count >= self.cfg.account.max_slots:
                break
            if code in {p.code for p in positions}:
                continue

            hist = fetch_ohlcv_history(code)
            if not is_ma_inflection_entry(hist, self.strat):
                continue

            try:
                last_close = float(hist.iloc[-1]["Close"] if "Close" in hist.columns else hist.iloc[-1]["close"])
                qty = int(self.cfg.account.bet_amount_per_slot // last_close)
                if qty < 1:
                    continue
                self.gateway.buy_close_price(code, snapshot=snap)
                positions.append(
                    LivePosition(
                        code=code,
                        qty=qty,
                        entry_price=last_close,
                        entry_date=now.strftime("%Y-%m-%d"),
                        hold_days=0,
                    )
                )
                snap = self.gateway.get_snapshot(positions)
                print(f"   ✅ 진입 후보 충족 · {code} @ {last_close:,.0f} x{qty}")
            except SlotLockError as e:
                print(f"   ⛔ {code} — {e}")
                break

        _save_positions(self.paths["positions_json"], positions)

    def run_watch_loop(self, *, once: bool = False) -> None:
        """장중 0.5초(설정) 주기 Hit&Run 청산 감시."""
        poll = self.cfg.watch.poll_interval_sec
        positions = _load_positions(self.paths["positions_json"])
        if not positions:
            print("📭 감시할 보유 포지션 없음")
            return

        print(f"👁️ 장중 감시 시작 · {len(positions)}종 · poll {poll}s")
        while True:
            now = _now_kst()
            today_s = now.strftime("%Y-%m-%d")
            remaining: list[LivePosition] = []

            for pos in positions:
                if pos.entry_date < today_s:
                    pos.hold_days += 1

                bar = fetch_intraday_bar(pos.code)
                if bar is None:
                    remaining.append(pos)
                    continue

                exit_info = evaluate_hit_and_run_exit(
                    entry_price=pos.entry_price,
                    high=bar["high"],
                    low=bar["low"],
                    close=bar["close"],
                    hold_days=pos.hold_days,
                    strat=self.strat,
                )
                if exit_info is None:
                    remaining.append(pos)
                    continue

                exit_px, exit_type = exit_info
                self.gateway.sell_all(
                    pos.code,
                    pos.qty,
                    exit_type=exit_type,
                    dry_run_note=f"@ {exit_px:,.0f}",
                )
                print(f"   청산 {pos.code} {exit_type} hold={pos.hold_days}d")

            positions = remaining
            _save_positions(self.paths["positions_json"], positions)

            if once or not positions:
                break
            if _at_or_after(now, self.cfg.watch.market_close):
                for pos in positions:
                    bar = fetch_intraday_bar(pos.code)
                    close_px = bar["close"] if bar else pos.entry_price
                    self.gateway.sell_all(pos.code, pos.qty, exit_type="TIME_STOP_EOD", dry_run_note="장마감")
                _save_positions(self.paths["positions_json"], [])
                break
            time.sleep(poll)

        print("👁️ 장중 감시 종료")
