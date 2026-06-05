"""
v5.5.2 장중 감시 — 진입(15:20) · Hit&Run 청산 루프 · 마스터 스케줄러 연동.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from src.data_loader import load_ohlcv
from src.live.live_account import (
    LiveAccountGateway,
    LivePosition,
    SlotLockError,
    snapshot_after_local_fill,
)
from src.overnight_parity import prime_project_dotenv_from_root
from src.live.live_config import LiveTradingConfig, load_live_config, resolve_live_paths
from src.live.live_signals import evaluate_hit_and_run_exit, explain_entry_signal
from src.live.live_screener import load_live_universe, run_live_screener

KST = ZoneInfo("Asia/Seoul")
logger = logging.getLogger("LiveEngine")


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


def fetch_ohlcv_history(
    code: str,
    *,
    target_date: str | None = None,
    lookback_calendar_days: int = 250,
) -> pd.DataFrame:
    """
    pykrx 기간 OHLCV — target_date 기준 lookback_calendar_days(기본 250일) 전부터 조회.
    MA120·듀얼 MA 연산에 영업일 121봉 이상 확보.
    """
    prime_project_dotenv_from_root(Path(__file__).resolve().parents[2])
    c6 = str(code).zfill(6)

    if target_date:
        raw = str(target_date).strip().replace("-", "")[:8]
        end_ts = pd.Timestamp(raw)
    else:
        end_ts = pd.Timestamp(_now_kst().strftime("%Y-%m-%d"))

    start_ts = end_ts - timedelta(days=lookback_calendar_days)
    start_iso = start_ts.strftime("%Y-%m-%d")
    end_iso = end_ts.strftime("%Y-%m-%d")

    df = load_ohlcv(c6, start_iso, end_iso)
    if df is None or df.empty:
        return pd.DataFrame()
    df.index = pd.to_datetime(df.index).normalize()
    return df.sort_index()


def fetch_intraday_bar(code: str) -> dict[str, float] | None:
    """당일 봉 근사 — FDR 최신 일봉(실시간은 KIS 시세 API로 교체 가능)."""
    df = fetch_ohlcv_history(code, lookback_calendar_days=5)
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

    def _load_universe_names(self) -> dict[str, str]:
        """live_today_universe.meta.json 에서 코드→종목명."""
        meta_path = self.paths.get("universe_meta") or str(
            Path(self.paths["universe_json"]).with_suffix(".meta.json")
        )
        if not os.path.isfile(meta_path):
            return {}
        try:
            with open(meta_path, encoding="utf-8") as fh:
                meta = json.load(fh)
            out: dict[str, str] = {}
            for item in meta.get("scanned_items_report") or []:
                c = str(item.get("code", "")).zfill(6)
                n = str(item.get("name", "")).strip()
                if c and n:
                    out[c] = n
            return out
        except Exception:
            return {}

    def _display_name(self, code: str, names: dict[str, str]) -> str:
        c6 = str(code).zfill(6)
        return names.get(c6) or c6

    def run_entry_scan(self, *, force: bool = False) -> None:
        now = _now_kst()
        if not force and not _at_or_after(now, self.strat.entry_time):
            logger.info("⏳ 진입 시각 대기 (%s KST)", self.strat.entry_time)
            return

        uni_path = self.paths["universe_json"]
        logger.info("📂 유니버스 로드 — %s (재스캔 없음)", uni_path)
        codes = load_live_universe(config=self.cfg, project_root=self.root)
        names = self._load_universe_names()
        positions = _load_positions(self.paths["positions_json"])
        try:
            snap = self.gateway.get_snapshot(positions)
        except SlotLockError as e:
            logger.warning("🛡️ [안전장치 트리거] %s -> 금일 매수 진입을 종료합니다.", e)
            return

        logger.info(
            "🎯 [진입 연산 시작] %d종 · 보유 %d/%d(동적) · 총자산 %s원 · dry_run=%s",
            len(codes),
            snap.open_slot_count,
            snap.dynamic_max_slots,
            f"{snap.total_equity:,.0f}",
            self.gateway.dry_run,
        )

        for idx, code in enumerate(codes, 1):
            c6 = str(code).zfill(6)
            label = self._display_name(c6, names)

            try:
                self.gateway.check_dynamic_slot_lock(snapshot=snap, local_positions=positions)
            except SlotLockError as sle:
                logger.warning(
                    "🛡️ [안전장치 트리거] %s -> 금일 매수 진입을 종료하고 잔고를 보존합니다.",
                    sle,
                )
                break

            if c6 in {p.code for p in positions}:
                logger.info("[탈락] %s (%s) — 이미 보유 중", c6, label)
                continue

            logger.info("[%d/%d] %s (%s) 진입 조건 연산 중…", idx, len(codes), c6, label)

            hist = fetch_ohlcv_history(c6)
            if hist.empty:
                logger.info("[탈락] %s (%s) — OHLCV 조회 실패·데이터 없음", c6, label)
                continue

            passed, reason = explain_entry_signal(hist, self.strat)
            if not passed:
                logger.info("[탈락] %s (%s) — %s", c6, label, reason)
                continue

            last_close = float(
                hist.iloc[-1]["Close"] if "Close" in hist.columns else hist.iloc[-1]["close"]
            )
            qty = int(self.cfg.account.bet_amount_per_slot // last_close)
            if qty < 1:
                logger.info(
                    "[탈락] %s (%s) — 베팅금액 대비 1주 미만 (종가 %s원)",
                    c6,
                    label,
                    f"{last_close:,.0f}",
                )
                continue

            logger.info(
                "🔥 [진입 시그널 포착] %s (%s) @ %s x%d — %s",
                c6,
                label,
                f"{last_close:,.0f}",
                qty,
                reason,
            )

            try:
                self.gateway.check_dynamic_slot_lock(snapshot=snap, local_positions=positions)
                ok = self.gateway.buy_close_price(c6, snapshot=snap)
                if ok is False:
                    logger.info("[탈락] %s (%s) — 주문 거부(게이트웨이)", c6, label)
                    continue
                new_pos = LivePosition(
                    code=c6,
                    qty=qty,
                    entry_price=last_close,
                    entry_date=now.strftime("%Y-%m-%d"),
                    hold_days=0,
                )
                positions.append(new_pos)
                _save_positions(self.paths["positions_json"], positions)
                spent = qty * last_close * (1.0 + self.cfg.account.buy_cost_ratio)
                snap = snapshot_after_local_fill(
                    snap, positions, cash_spent=spent, account=self.cfg.account
                )
                logger.info(
                    "   ✅ [체결 기록] %s (%s) @ %s x%d (로컬 장부 저장·잔고 API 생략)",
                    c6,
                    label,
                    f"{last_close:,.0f}",
                    qty,
                )
            except SlotLockError as e:
                logger.warning(
                    "🛡️ [안전장치 트리거] %s (%s) — %s -> 금일 매수 진입 종료",
                    c6,
                    label,
                    e,
                )
                break

        logger.info("🏁 [진입 연산 종료] 최종 보유 %d종", len(positions))
        _save_positions(self.paths["positions_json"], positions)

    def calculate_entry_signals(self) -> None:
        """마스터 ROUTINE 2 — 15:20 듀얼 MA·변곡 진입 및 KIS 종가 주문."""
        self.run_entry_scan(force=True)

    def monitor_market_realtime(self) -> int:
        """마스터 ROUTINE 3 — 장중 1틱(+8%/-3%/타임스탑) 감시. 반환=잔여 포지션 수."""
        positions = _load_positions(self.paths["positions_json"])
        if not positions:
            return 0

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
            logger.info("   청산 %s %s hold=%dd", pos.code, exit_type, pos.hold_days)

        positions = remaining
        _save_positions(self.paths["positions_json"], positions)

        if positions and _at_or_after(now, self.cfg.watch.market_close):
            for pos in positions:
                bar = fetch_intraday_bar(pos.code)
                self.gateway.sell_all(
                    pos.code,
                    pos.qty,
                    exit_type="TIME_STOP_EOD",
                    dry_run_note="장마감",
                )
            _save_positions(self.paths["positions_json"], [])
            return 0

        return len(positions)

    def print_positions_snapshot(self) -> None:
        """SOP 장마감 — 당일 포지션 스냅샷 1회 출력."""
        positions = _load_positions(self.paths["positions_json"])
        if not positions:
            print("📭 보유 포지션 없음")
            return
        print(f"📊 당일 포지션 스냅샷 ({len(positions)}종)")
        for p in positions:
            bar = fetch_intraday_bar(p.code)
            close = bar["close"] if bar else p.entry_price
            pnl = (close / p.entry_price - 1.0) * 100.0 if p.entry_price > 0 else 0.0
            print(
                f"   {p.code} qty={p.qty} 진입={p.entry_price:,.0f} "
                f"현재≈{close:,.0f} PnL={pnl:+.2f}% hold={p.hold_days}d "
                f"entry_date={p.entry_date}"
            )

    def run_watch_loop(self, *, once: bool = False) -> None:
        """장중 0.5초(설정) 주기 Hit&Run 청산 감시 (단독 명령·테스트용)."""
        poll = self.cfg.watch.poll_interval_sec
        if once:
            self.print_positions_snapshot()
        if not _load_positions(self.paths["positions_json"]):
            if not once:
                print("📭 감시할 보유 포지션 없음")
            return

        print(f"👁️ 장중 감시 시작 · poll {poll}s")
        while True:
            n = self.monitor_market_realtime()
            if once or n == 0:
                break
            time.sleep(poll)
        print("👁️ 장중 감시 종료")


# 마스터 스크립트·문서 호환 별칭
LiveEngine = LiveTradingEngine
