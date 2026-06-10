"""
v5.5.2 장중 감시 — 진입(15:20) · Hit&Run 청산 루프 · 마스터 스케줄러 연동.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
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
from src.live.live_db import (
    _load_position_names,
    ensure_db_ready,
    insert_trading_history,
    load_holding_positions,
    persist_universe_candidates_from_meta,
    save_holding_positions,
    upsert_daily_snapshot,
    use_json_fallback,
)
from src.live.live_signals import evaluate_hit_and_run_exit, explain_entry_signal
from src.live.live_screener import load_live_universe, run_live_screener
from src.automation.telegram_client import (
    tg_client,
    build_entry_message,
    build_exit_message,
)

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


def _norm_date_key(value: str) -> str:
    """YYYY-MM-DD · YYYYMMDD → YYYYMMDD 비교 키."""
    return str(value).strip().replace("-", "")[:8]


def _is_same_day_exit_protected(pos: LivePosition, today_s: str) -> bool:
    """
    익일 매도 원칙 인터록 — 당일 매수 종목은 장중 청산 감시 대상에서 제외.
    매수일=오늘 이면 패스. (hold_days의 0 체크는 동기화 정합성 이슈 유발하므로 제거).
    """
    return _norm_date_key(pos.entry_date) == _norm_date_key(today_s)


def _load_positions_json(path: str) -> list[LivePosition]:
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


def _save_positions_json(path: str, positions: list[LivePosition]) -> None:
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


def _load_positions(engine: "LiveTradingEngine") -> list[LivePosition]:
    if use_json_fallback():
        return _load_positions_json(engine.paths["positions_json"])
    return load_holding_positions(engine.db_path)


def _save_positions(
    engine: "LiveTradingEngine",
    positions: list[LivePosition],
    *,
    names: dict[str, str] | None = None,
) -> None:
    if use_json_fallback():
        _save_positions_json(engine.paths["positions_json"], positions)
        return
    save_holding_positions(engine.db_path, positions, names=names)


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
        if use_json_fallback():
            self.db_path = self.paths["db_path"]
            logger.warning("⚠️ [JSON 장부] LIVE_USE_JSON_LEDGER=1 — DB 대신 JSON 사용")
        else:
            self.db_path = ensure_db_ready(self.root, self.paths["positions_json"])
        self.gateway = LiveAccountGateway(self.cfg.account, dry_run=dry_run)
        self.strat = self.cfg.strategy
        self.on_entry_filled: Callable[[LivePosition, str], None] | None = None
        self.on_exit_recorded: Callable[[LivePosition, str, float, str], None] | None = None
        self._monitor_active = False

    @property
    def is_monitor_running(self) -> bool:
        """장중 감시 루프 가동 여부 (마스터 스케줄러가 설정)."""
        return self._monitor_active

    def set_monitor_active(self, active: bool) -> None:
        self._monitor_active = active

    def sync_positions_from_kis(self) -> dict[str, Any]:
        """한투 API 실잔고·보유종목 → 로컬 SQLite 장부 강제 동기화."""
        logger.info("📡 [장개시 전 전산 동기화] 한투 잔고 조회 시작")
        local = {p.code: p for p in _load_positions(self)}
        names = self._load_all_names()
        today = _now_kst().strftime("%Y-%m-%d")

        try:
            balances = self.gateway.get_inquire_balance(list(local.values()))
        except Exception as e:
            logger.error("❌ [KIS 동기화 실패] %s", e)
            return {"synced_count": 0, "error": str(e)}

        synced: list[LivePosition] = []
        for item in balances.get("positions") or []:
            symbol = str(item.get("symbol", "")).zfill(6)
            qty = int(item.get("quantity") or 0)
            if qty <= 0:
                continue
            avg = float(item.get("entry_price") or 0)
            prev = local.get(symbol)
            if prev:
                synced.append(
                    LivePosition(
                        code=symbol,
                        qty=qty,
                        entry_price=avg,
                        entry_date=prev.entry_date,
                        hold_days=prev.hold_days,
                    )
                )
            else:
                synced.append(
                    LivePosition(
                        code=symbol,
                        qty=qty,
                        entry_price=avg,
                        entry_date=today,
                        hold_days=0,
                    )
                )
            item_name = str(item.get("name") or "").strip()
            if item_name:
                names[symbol] = item_name

        _save_positions(self, synced, names=names)

        if not use_json_fallback():
            upsert_daily_snapshot(
                self.db_path,
                base_date=today,
                available_cash=float(balances.get("available_cash") or 0),
                total_evaluation=float(balances.get("total_evaluation") or 0),
                total_asset=float(balances.get("total_asset") or 0),
            )

        logger.info(
            "✅ [KIS 동기화 완료] 보유 %d종 · 총자산 %s원 · 예수금 %s원",
            len(synced),
            f"{float(balances.get('total_asset') or 0):,.0f}",
            f"{float(balances.get('available_cash') or 0):,.0f}",
        )
        return {
            "synced_count": len(synced),
            "total_asset": balances.get("total_asset"),
            "available_cash": balances.get("available_cash"),
            "source": balances.get("source", "kis"),
        }

    def execute_market_scanner(self) -> list[str]:
        """15:15 주도주 스캔 — 유니버스 JSON·DB 후보 저장."""
        codes = run_live_screener(config=self.cfg, project_root=self.root)
        if not use_json_fallback():
            meta_path = self.paths.get("universe_meta") or str(
                Path(self.paths["universe_json"]).with_suffix(".meta.json")
            )
            persist_universe_candidates_from_meta(self.db_path, meta_path)
        logger.info("🔔 [스캔 마감] %d종 유니버스 확정", len(codes))
        return codes

    def execute_market_close_processing(self) -> dict[str, Any]:
        """15:30 장마감 — hold_days 일괄 +1 · KIS 자산 스냅샷."""
        logger.info("⏰ [스케줄러 트리거] 15:30 정규장 마감 정산")
        positions = _load_positions(self)
        names = self._load_all_names()
        bumped = 0
        for pos in positions:
            pos.hold_days += 1
            bumped += 1
        if positions:
            _save_positions(self, positions, names=names)
            logger.info("📅 [hold_days 벌크업] %d종 보유일수 +1 반영", bumped)

        # KIS 동시호가 체결 처리 및 전산 반영 지연(약 15초) 감안하여 대기 후 동기화 진행
        logger.info("⏳ KIS 정규장 마감 체결 전산 반영 대기 (15초)...")
        time.sleep(15.0)

        self.sync_positions_from_kis()
        self.print_positions_snapshot()
        return {"bumped_count": bumped, "position_count": len(positions)}

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

    def _load_all_names(self) -> dict[str, str]:
        """유니버스 메타 + DB 보유종목명 병합."""
        names = self._load_universe_names()
        if use_json_fallback():
            return names
        for c6, raw in _load_position_names(self.db_path).items():
            n = str(raw or "").strip()
            if n and n != c6:
                names.setdefault(c6, n)
        return names

    def _resolve_stock_name(self, code: str, names: dict[str, str] | None = None) -> str:
        """종목명 조회 — 메타/DB/pykrx 순 폴백 (텔레그램·장부 표기용)."""
        c6 = str(code).zfill(6)
        pool = names if names is not None else self._load_all_names()
        hit = str(pool.get(c6) or "").strip()
        if hit and hit != c6:
            return hit
        if not use_json_fallback():
            db_hit = str(_load_position_names(self.db_path).get(c6) or "").strip()
            if db_hit and db_hit != c6:
                return db_hit
        try:
            from pykrx import stock

            py_hit = str(stock.get_market_ticker_name(c6) or "").strip()
            if py_hit:
                return py_hit
        except Exception:
            pass
        return c6

    def send_order_kis(self, code: str, qty: int, price: float) -> dict[str, Any]:
        """KIS 시장가 주문 — 실전(LIVE_DRY_RUN=0) 시 원본 응답 패킷 100% 노출."""
        logger.info("🚀 [주문 전송] %s x%d @ 시장가 (%s)", code, qty, f"{price:,.0f}")
        response = self.gateway.place_market_order(code=code, qty=qty)
        logger.info("📡 [한투 API Response Raw Data] -> %s", response)
        if isinstance(response, dict):
            return response
        return {"rt_cd": "1", "msg1": str(response)}

    def _record_exit(
        self,
        pos: LivePosition,
        *,
        exit_price: float,
        exit_type: str,
        exit_date: str,
        name: str = "",
    ) -> None:
        if use_json_fallback():
            return
        # 순수 호가 수익률: 손절 트리거(-3.00%)와 기록값이 일치하도록 수수료 미반영.
        # evaluate_hit_and_run_exit 도 순수 가격 비교로 발화하므로 동일 기준 유지.
        if pos.entry_price > 0:
            profit_rate = (exit_price - pos.entry_price) / pos.entry_price
        else:
            profit_rate = 0.0
        insert_trading_history(
            self.db_path,
            symbol=pos.code,
            name=name,
            entry_date=pos.entry_date,
            entry_price=pos.entry_price,
            exit_date=exit_date,
            exit_price=exit_price,
            quantity=pos.qty,
            profit_rate=profit_rate,
            reason=exit_type,
        )
        # 📱 트리거 3: 청산 텔레그램 알림
        tg_client.send_message(
            build_exit_message(
                code=pos.code,
                name=name or self._resolve_stock_name(pos.code),
                entry_price=pos.entry_price,
                exit_price=exit_price,
                quantity=pos.qty,
                profit_rate=profit_rate,
                reason=exit_type,
            )
        )
        if self.on_exit_recorded:
            try:
                self.on_exit_recorded(pos, name, exit_price, exit_type)
            except Exception:
                logger.exception("on_exit_recorded 콜백 오류")

    def run_entry_scan(self, *, force: bool = False) -> dict[str, Any]:
        """진입 연산. 반환=체결·거부 집계 dict."""
        now = _now_kst()
        if not force and not _at_or_after(now, self.strat.entry_time):
            logger.info("⏳ 진입 시각 대기 (%s KST)", self.strat.entry_time)
            return {"executed_count": 0, "rejected_count": 0, "last_rejection_msg": None}

        uni_path = self.paths["universe_json"]
        logger.info("📂 유니버스 로드 — %s (재스캔 없음)", uni_path)
        codes = load_live_universe(config=self.cfg, project_root=self.root)
        names = self._load_all_names()
        positions = _load_positions(self)
        executed = 0
        rejected = 0
        last_rejection_msg: str | None = None
        try:
            snap = self.gateway.get_snapshot(positions)
        except SlotLockError as e:
            logger.warning("🛡️ [안전장치 트리거] %s -> 금일 매수 진입을 종료합니다.", e)
            return {"executed_count": 0, "rejected_count": 0, "last_rejection_msg": None}

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
            label = self._resolve_stock_name(c6, names)

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
                if not self.gateway.dry_run and snap.cash < qty * last_close:
                    raise SlotLockError(
                        f"예수금 부족 (필요 {qty * last_close:,} / 보유 {snap.cash:,.0f})"
                    )

                res_packet = self.send_order_kis(c6, qty, last_close)
                rt_cd = str(res_packet.get("rt_cd", "1"))
                msg_text = str(res_packet.get("msg1", "장외 주문 차단"))

                if rt_cd != "0":
                    rejected += 1
                    last_rejection_msg = msg_text
                    logger.error(
                        "❌ [장부 차단] 한투 전산 거부 (코드: %s) | 사유: %s",
                        rt_cd,
                        msg_text,
                    )
                    logger.warning("⚠️ 실제 잔고 변동 없음 -> SQLite 저장 취소")
                    continue

                new_pos = LivePosition(
                    code=c6,
                    qty=qty,
                    entry_price=last_close,
                    entry_date=now.strftime("%Y-%m-%d"),
                    hold_days=0,
                )
                positions.append(new_pos)
                executed += 1
                _save_positions(self, positions, names=names)
                spent = qty * last_close * (1.0 + self.cfg.account.buy_cost_ratio)
                snap = snapshot_after_local_fill(
                    snap, positions, cash_spent=spent, account=self.cfg.account
                )
                logger.info("✅ [체결 완료] %s 장부 동기화 완료.", label)
                logger.info(
                    "   ✅ [체결 기록] %s (%s) @ %s x%d (로컬 장부 저장·잔고 API 생략)",
                    c6,
                    label,
                    f"{last_close:,.0f}",
                    qty,
                )
                # 📱 트리거 2: 매수 체결 텔레그램 알림
                tg_client.send_message(
                    build_entry_message(
                        code=c6,
                        name=label,
                        entry_price=last_close,
                        quantity=qty,
                    )
                )
                if self.on_entry_filled:
                    try:
                        self.on_entry_filled(new_pos, label)
                    except Exception:
                        logger.exception("on_entry_filled 콜백 오류")
            except SlotLockError as e:
                logger.warning(
                    "🛡️ [안전장치 트리거] %s (%s) — %s -> 금일 매수 진입 종료",
                    c6,
                    label,
                    e,
                )
                break

        logger.info(
            "🏁 [진입 연산 종료] 최종 보유 %d종 · 신규 체결 %d종 · 거부 %d건",
            len(positions),
            executed,
            rejected,
        )
        _save_positions(self, positions, names=names)
        return {
            "executed_count": executed,
            "rejected_count": rejected,
            "last_rejection_msg": last_rejection_msg,
        }

    def calculate_entry_signals(self) -> dict[str, Any]:
        """마스터 ROUTINE 2 — 15:20 듀얼 MA·변곡 진입 및 KIS 종가 주문."""
        return self.run_entry_scan(force=True)

    def save_daily_asset_snapshot(self) -> None:
        """당일 자산 스냅샷 — KIS inquire_balance SSOT."""
        if use_json_fallback():
            return
        positions = _load_positions(self)
        try:
            balances = self.gateway.get_inquire_balance(positions)
        except Exception as e:
            logger.warning("⚠️ 자산 스냅샷 생략(잔고 조회 실패): %s", e)
            return
        today = _now_kst().strftime("%Y-%m-%d")
        upsert_daily_snapshot(
            self.db_path,
            base_date=today,
            available_cash=float(balances.get("available_cash") or 0),
            total_evaluation=float(balances.get("total_evaluation") or 0),
            total_asset=float(balances.get("total_asset") or 0),
        )
        logger.info(
            "📸 [자산 스냅샷] %s — 총자산 %s원",
            today,
            f"{float(balances.get('total_asset') or 0):,.0f}",
        )

    def monitor_market_realtime(self) -> int:
        """마스터 ROUTINE 3 — 장중 1틱(+8%/-3%/타임스탑) 감시. 반환=잔여 포지션 수."""
        positions = _load_positions(self)
        if not positions:
            return 0

        now = _now_kst()
        today_s = now.strftime("%Y-%m-%d")
        names = self._load_all_names()
        remaining: list[LivePosition] = []

        for pos in positions:
            if _is_same_day_exit_protected(pos, today_s):
                remaining.append(pos)
                continue

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
            self._record_exit(
                pos,
                exit_price=exit_px,
                exit_type=exit_type,
                exit_date=today_s,
                name=self._resolve_stock_name(pos.code, names),
            )
            logger.info("   청산 %s %s hold=%dd", pos.code, exit_type, pos.hold_days)

        positions = remaining
        _save_positions(self, positions, names=names)

        if positions and _at_or_after(now, self.cfg.watch.market_close):
            eod_remaining: list[LivePosition] = []
            for pos in positions:
                if _is_same_day_exit_protected(pos, today_s):
                    eod_remaining.append(pos)
                    continue
                bar = fetch_intraday_bar(pos.code)
                exit_px = float(bar["close"]) if bar else pos.entry_price
                self.gateway.sell_all(
                    pos.code,
                    pos.qty,
                    exit_type="TIME_STOP_EOD",
                    dry_run_note="장마감",
                )
                self._record_exit(
                    pos,
                    exit_price=exit_px,
                    exit_type="TIME_STOP_EOD",
                    exit_date=today_s,
                    name=self._resolve_stock_name(pos.code, names),
                )
            _save_positions(self, eod_remaining, names=names)
            return len(eod_remaining)

        return len(positions)

    def print_positions_snapshot(self) -> None:
        """SOP 장마감 — 당일 포지션 스냅샷 1회 출력."""
        positions = _load_positions(self)
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
        if not _load_positions(self):
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
