"""
v6.1 대시보드 실시간 사령탑 — Direct Call + WebSocket 이벤트 3대 모드.
스캔·진입: 동기 HTTP + WS 완료 신호 · 감시: 0.5s 스레드 + WS 청산 신호.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from src.live.live_account import LivePosition
from src.live.live_db import (
    DEFAULT_RESET_CASH,
    compute_profit_rate,
    persist_universe_candidates_from_meta,
    reset_system_database,
    use_json_fallback,
)
from src.live.live_engine import LiveTradingEngine
from src.live.live_screener import LiveScreener
from src.overnight_parity import prime_project_dotenv_from_root
from src.web.ws_hub import ws_broadcast

KST = ZoneInfo("Asia/Seoul")
logger = logging.getLogger("ControlBridge")

_EXIT_ALERT = {
    "TAKE_PROFIT": "익절 완료",
    "STOP_LOSS": "손절 완료",
    "TIME_STOP": "타임스탑 완료",
    "TIME_STOP_EOD": "장마감 청산 완료",
}


class _WatchLoop:
    """장중 0.5초 monitor_market_realtime 백그라운드 루프."""

    def __init__(self, engine: LiveTradingEngine) -> None:
        self._engine = engine
        self._active = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return self._active

    def start(self) -> bool:
        with self._lock:
            if self._active:
                return False
            self._active = True
            self._thread = threading.Thread(target=self._run, name="web-watch", daemon=True)
            self._thread.start()
            return True

    def stop(self) -> bool:
        with self._lock:
            was = self._active
            self._active = False
            return was

    def _run(self) -> None:
        poll = self._engine.cfg.watch.poll_interval_sec
        logger.info("👁️ [웹 감시] 0.5초 루프 가동 (poll=%ss)", poll)
        ws_broadcast({"event": "WATCH_STARTED", "poll_sec": poll})
        while self._active:
            try:
                self._engine.monitor_market_realtime()
            except Exception:
                logger.exception("❌ [웹 감시] 틱 오류")
            time.sleep(poll)
        logger.info("👁️ [웹 감시] 루프 정지")
        ws_broadcast({"event": "WATCH_STOPPED"})


class ControlBridge:
    """대시보드 ↔ 봇 코어 다이렉트 매핑 (싱글톤)."""

    def __init__(self, project_root: str | Path) -> None:
        root = str(project_root)
        prime_project_dotenv_from_root(Path(root))
        self.root = root
        self.engine = LiveTradingEngine(project_root=root, dry_run=None)
        self.screener = LiveScreener(self.engine.cfg, project_root=root)
        self._watch = _WatchLoop(self.engine)
        self._task_lock = threading.Lock()
        self.scan_state: dict[str, Any] = self._idle_state("scan")
        self.entry_state: dict[str, Any] = self._idle_state("entry")
        self.engine.on_entry_filled = self._emit_entry_triggered
        self.engine.on_exit_recorded = self._emit_exit_triggered

    @staticmethod
    def _idle_state(kind: str) -> dict[str, Any]:
        return {
            "kind": kind,
            "running": False,
            "last_at": None,
            "last_count": None,
            "last_error": None,
            "message": "",
        }

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(KST).isoformat()

    @staticmethod
    def _time_hms() -> str:
        return datetime.now(KST).strftime("%H:%M:%S")

    def _emit_entry_triggered(self, pos: LivePosition, name: str) -> None:
        ws_broadcast(
            {
                "event": "ENTRY_TRIGGERED",
                "symbol": pos.code,
                "name": name or pos.code,
                "quantity": pos.qty,
                "entry_price": pos.entry_price,
                "entry_date": pos.entry_date,
                "timestamp": self._time_hms(),
            }
        )

    def _emit_exit_triggered(
        self,
        pos: LivePosition,
        name: str,
        exit_price: float,
        reason: str,
    ) -> None:
        label = _EXIT_ALERT.get(reason, reason)
        display = name or pos.code
        acct = self.engine.cfg.account
        profit_rate = compute_profit_rate(
            entry_price=pos.entry_price,
            exit_price=exit_price,
            quantity=pos.qty,
            buy_cost_ratio=acct.buy_cost_ratio,
            sell_cost_ratio=acct.sell_cost_ratio,
        )
        ws_broadcast(
            {
                "event": "EXIT_TRIGGERED",
                "symbol": pos.code,
                "name": display,
                "reason": reason,
                "reason_label": label,
                "exit_price": exit_price,
                "profit_rate": profit_rate,
                "alert": f"🚨 [{label}] {display}",
                "timestamp": self._time_hms(),
            }
        )

    def run_scan_sync(self) -> dict[str, Any]:
        """동기식 스캔 — 완료 시 WS SCAN_COMPLETED + HTTP 응답."""
        if not self._task_lock.acquire(blocking=False):
            return {"status": "busy", "message": "다른 명령 실행 중입니다.", "timestamp": self._time_hms()}
        self.scan_state["running"] = True
        self.scan_state["last_error"] = None
        try:
            logger.info("⚡ [사령탑 격발] 웹 대시보드로부터 즉시 스캔 명령 하달.")
            codes = self.screener.execute_daily_scan()
            if not use_json_fallback():
                persist_universe_candidates_from_meta(self.engine.db_path, self.screener.meta_path)
            count = len(codes)
            ts = self._time_hms()
            self.scan_state.update(
                running=False,
                last_at=self._now_iso(),
                last_count=count,
                message=f"총 {count}종 주도주 스캔 마감 완료.",
                last_error=None,
            )
            ws_broadcast({"event": "SCAN_COMPLETED", "count": count, "timestamp": ts})
            return {
                "status": "success",
                "message": f"총 {count}종 주도주 스캔 마감 완료.",
                "count": count,
                "timestamp": ts,
            }
        except Exception as e:
            logger.exception("❌ [사령탑] 스캔 실패")
            ts = self._time_hms()
            self.scan_state.update(
                running=False,
                last_at=self._now_iso(),
                last_error=str(e),
                message=f"스캔 실패: {e}",
            )
            ws_broadcast({"event": "SCAN_FAILED", "message": str(e), "timestamp": ts})
            return {"status": "error", "message": f"스캔 실패: {e}", "timestamp": ts}
        finally:
            self.scan_state["running"] = False
            self._task_lock.release()

    def run_entry_sync(self) -> dict[str, Any]:
        """동기식 진입 — 체결마다 WS ENTRY_TRIGGERED · 마감 시 ENTRY_COMPLETED."""
        if not self._task_lock.acquire(blocking=False):
            return {"status": "busy", "message": "다른 명령 실행 중입니다.", "timestamp": self._time_hms()}
        self.entry_state["running"] = True
        self.entry_state["last_error"] = None
        try:
            logger.info("⚡ [사령탑 격발] 웹 대시보드로부터 즉시 진입 명령 하달.")
            result = self.engine.calculate_entry_signals()
            executed_count = int(result.get("executed_count", 0))
            rejected_count = int(result.get("rejected_count", 0))
            rejection_msg = str(
                result.get("last_rejection_msg") or "한투 정규 매매시간이 아닙니다."
            )
            ts = self._time_hms()

            if executed_count == 0 and rejected_count > 0:
                self.entry_state.update(
                    running=False,
                    last_at=self._now_iso(),
                    last_count=0,
                    message=f"한투 주문 거부 — {rejection_msg}",
                    last_error=rejection_msg,
                )
                ws_broadcast(
                    {
                        "event": "ENTRY_REJECTED",
                        "message": rejection_msg,
                        "rejected_count": rejected_count,
                        "timestamp": ts,
                    }
                )
                return {
                    "status": "rejected",
                    "message": rejection_msg,
                    "executed_count": 0,
                    "rejected_count": rejected_count,
                    "timestamp": ts,
                }

            if executed_count > 0:
                self.engine.save_daily_asset_snapshot()

            self.entry_state.update(
                running=False,
                last_at=self._now_iso(),
                last_count=executed_count,
                message=f"총 {executed_count}종목 황금 필터 연산 및 주문 마감 완료.",
                last_error=None,
            )
            ws_broadcast(
                {
                    "event": "ENTRY_COMPLETED",
                    "executed_count": executed_count,
                    "timestamp": ts,
                }
            )
            return {
                "status": "success",
                "message": f"총 {executed_count}종목에 대한 황금 필터 연산 및 주문 마감 완료.",
                "executed_count": executed_count,
                "timestamp": ts,
            }
        except Exception as e:
            logger.exception("❌ [사령탑] 진입 실패")
            ts = self._time_hms()
            self.entry_state.update(
                running=False,
                last_at=self._now_iso(),
                last_error=str(e),
                message=f"진입 실패: {e}",
            )
            ws_broadcast({"event": "ENTRY_FAILED", "message": str(e), "timestamp": ts})
            return {"status": "error", "message": f"진입 실패: {e}", "timestamp": ts}
        finally:
            self.entry_state["running"] = False
            self._task_lock.release()

    def run_reset_sync(self) -> dict[str, Any]:
        """마스터 DB·유니버스 전면 초기화 — 감시 중단 · 장부·후보 세척 · 원금 스냅샷."""
        if not self._task_lock.acquire(blocking=False):
            return {"status": "busy", "message": "다른 명령 실행 중입니다.", "timestamp": self._time_hms()}
        try:
            logger.critical(
                "🚨 [사령탑 직접 개입] 유니버스 후보군을 포함한 전산 전면 초기화 가동!"
            )
            if self._watch.active:
                self._watch.stop()
                render_off = True
            else:
                render_off = False
            if render_off:
                ws_broadcast({"event": "WATCH_STOPPED"})

            paths = self.engine.paths
            reset_system_database(
                self.engine.db_path,
                initial_cash=DEFAULT_RESET_CASH,
                universe_json_path=paths["universe_json"],
                universe_meta_path=paths.get("universe_meta")
                or str(Path(paths["universe_json"]).with_suffix(".meta.json")),
            )
            self.scan_state = self._idle_state("scan")
            self.entry_state = self._idle_state("entry")

            msg = (
                f"유니버스 후보군을 포함한 전 시스템이 원금 {DEFAULT_RESET_CASH:,.0f}원 "
                "청정 상태로 포맷되었습니다."
            )
            ts = self._time_hms()
            ws_broadcast(
                {
                    "event": "RESET_COMPLETE",
                    "message": f"✨ {msg}",
                    "initial_cash": DEFAULT_RESET_CASH,
                    "timestamp": ts,
                }
            )
            return {"status": "success", "message": msg, "timestamp": ts}
        except Exception as e:
            logger.exception("❌ [사령탑] 초기화 실패")
            return {
                "status": "error",
                "message": f"초기화 실패: {e}",
                "timestamp": self._time_hms(),
            }
        finally:
            self._task_lock.release()

    def toggle_watch(self, active: bool) -> dict[str, Any]:
        if active:
            if self._watch.start():
                return {"status": "active", "message": "0.5초 주기 실시간 청산 레이더가 가동되었습니다."}
            return {"status": "active", "message": "실시간 감시가 이미 가동 중입니다."}
        self._watch.stop()
        return {"status": "inactive", "message": "실시간 청산 레이더가 일시 정지되었습니다."}

    def status(self) -> dict[str, Any]:
        return {
            "dry_run": self.engine.gateway.dry_run,
            "watch_active": self._watch.active,
            "poll_interval_sec": self.engine.cfg.watch.poll_interval_sec,
            "scan": dict(self.scan_state),
            "entry": dict(self.entry_state),
            "db_path": self.engine.db_path,
        }


_bridge: ControlBridge | None = None
_bridge_lock = threading.Lock()


def get_control_bridge(project_root: str | Path) -> ControlBridge:
    global _bridge
    with _bridge_lock:
        if _bridge is None:
            _bridge = ControlBridge(project_root)
            logger.info(
                "🎮 사령탑 바인딩 — dry_run=%s · db=%s",
                _bridge.engine.gateway.dry_run,
                _bridge.engine.db_path,
            )
        return _bridge
