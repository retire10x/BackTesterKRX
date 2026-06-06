"""
v5.5.2 완전 자동 마스터 스케줄러 — 08:50 KIS동기화 · 15:15 스캔 · 15:20 진입 · 15:30 정산 · 장중 감시.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_HEARTBEAT_INTERVAL_SEC = 600  # 10분

from src.live.live_engine import LiveTradingEngine, _load_positions, _now_kst
from src.live.live_screener import LiveScreener
from src.overnight_parity import prime_project_dotenv_from_root

KST = ZoneInfo("Asia/Seoul")
logger = logging.getLogger("LiveMaster")

STATE_REL = "config/live_master_state.json"
STARTUP_SYNC_TIME = "08:50"


def _parse_hm(hm: str) -> tuple[int, int]:
    h, m = hm.strip().split(":")
    return int(h), int(m)


def _in_trigger_window(now: datetime, hm: str, *, window_sec: int = 90) -> bool:
    h, m = _parse_hm(hm)
    start = now.replace(hour=h, minute=m, second=0, microsecond=0, tzinfo=KST)
    end = start + timedelta(seconds=window_sec)
    return start <= now < end


def _in_market_session(now: datetime, open_hm: str, close_hm: str) -> bool:
    o_h, o_m = _parse_hm(open_hm)
    c_h, c_m = _parse_hm(close_hm)
    cur = now.hour * 60 + now.minute
    return o_h * 60 + o_m <= cur <= c_h * 60 + c_m


def _load_state(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_state(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


class LiveMasterRunner:
    """출근 후 `python run_live_bot.py` 한 줄로 종일 자동 운영."""

    def __init__(
        self,
        engine: LiveTradingEngine,
        *,
        screener: LiveScreener | None = None,
    ):
        self.engine = engine
        self.screener = screener or LiveScreener(engine.cfg, project_root=engine.root)
        self.state_path = str(Path(engine.root) / STATE_REL)
        self._state = _load_state(self.state_path)
        self._last_heartbeat_ts = 0.0  # monotonic

    def _today_key(self) -> str:
        return _now_kst().strftime("%Y-%m-%d")

    def _already_ran(self, key: str) -> bool:
        return self._state.get(key) == self._today_key()

    def _mark_ran(self, key: str) -> None:
        self._state[key] = self._today_key()
        _save_state(self.state_path, self._state)

    def _has_positions(self) -> bool:
        return bool(_load_positions(self.engine))

    def _run_startup_sync_routine(self) -> None:
        if self._already_ran("kis_sync_date"):
            return
        logger.info("🔔 [ROUTINE 0] %s 장개시 전 한투 잔고 동기화", STARTUP_SYNC_TIME)
        self.engine.sync_positions_from_kis()
        self._mark_ran("kis_sync_date")

    def _ensure_kis_sync_catchup(self, now: datetime) -> None:
        """08:50 이후 늦게 기동된 봇 — 당일 미동기화 시 즉시 1회 보정."""
        if self._already_ran("kis_sync_date"):
            return
        h, m = _parse_hm(STARTUP_SYNC_TIME)
        sync_at = now.replace(hour=h, minute=m, second=0, microsecond=0, tzinfo=KST)
        if now >= sync_at:
            logger.info("📡 [동기화 보정] %s 경과 · 한투 잔고 즉시 동기화", STARTUP_SYNC_TIME)
            self.engine.sync_positions_from_kis()
            self._mark_ran("kis_sync_date")

    def _run_screener_routine(self) -> None:
        if self._already_ran("screener_date"):
            return
        logger.info("🔔 [ROUTINE 1] %s 주도주 스캔 자동 실행", self.engine.cfg.screener.screener_time)
        codes = self.engine.execute_market_scanner()
        self._mark_ran("screener_date")
        logger.info("   스캔 완료 — %d종", len(codes))
        if not codes:
            logger.warning("   0종 — 거래대금 미집계 시 15:18 이후 재시도 가능(당일 1회 제한 해제는 수동)")

    def _run_entry_routine(self) -> None:
        if self._already_ran("entry_date"):
            return
        logger.info("⏰ [ROUTINE 2] %s 황금 타점 자동 매수 개시", self.engine.cfg.strategy.entry_time)
        result = self.engine.calculate_entry_signals()
        executed = int(result.get("executed_count", 0))
        rejected = int(result.get("rejected_count", 0))
        if rejected > 0 and executed == 0:
            logger.error(
                "❌ [자동 매수 거부] %s",
                result.get("last_rejection_msg", "한투 정규 매매시간이 아닙니다."),
            )
        elif executed > 0:
            self.engine.save_daily_asset_snapshot()
            logger.info("✅ [자동 매수 완료] %d종 체결 · 장부·스냅샷 반영", executed)
        self._mark_ran("entry_date")

    def _run_close_routine(self) -> None:
        if self._already_ran("close_settlement_date"):
            return
        self.engine.execute_market_close_processing()
        self._mark_ran("close_settlement_date")

    def _emit_heartbeat_if_due(self) -> None:
        """10분마다 터미널·로그파일에 엔진 생존 로그 1줄 강제 기록."""
        mono = time.monotonic()
        if mono - self._last_heartbeat_ts >= _HEARTBEAT_INTERVAL_SEC:
            self._last_heartbeat_ts = mono
            logger.info(
                "💓 [Engine Heartbeat] %s | 봇 엔진 이상 없음 (구동 중)",
                datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
            )

    def tick(self) -> float:
        """
        마스터 루프 1회. 반환값 = 다음 sleep 초.
        """
        self._emit_heartbeat_if_due()
        now = _now_kst()

        if now.weekday() >= 5:
            logger.info("💤 주말 휴장 — 대기 모드 (1시간)")
            return 3600.0

        cfg = self.engine.cfg
        screener_hm = cfg.screener.screener_time
        entry_hm = cfg.strategy.entry_time
        open_hm = cfg.watch.market_open
        close_hm = cfg.watch.market_close
        poll = cfg.watch.poll_interval_sec

        self._ensure_kis_sync_catchup(now)

        if _in_trigger_window(now, STARTUP_SYNC_TIME):
            self._run_startup_sync_routine()
            return 10.0

        if _in_trigger_window(now, screener_hm):
            self._run_screener_routine()
            return 10.0

        if _in_trigger_window(now, entry_hm):
            self._run_entry_routine()
            return 10.0

        if _in_trigger_window(now, close_hm):
            self._run_close_routine()
            return 10.0

        in_session = _in_market_session(now, open_hm, close_hm)
        has_pos = self._has_positions()
        self.engine.set_monitor_active(in_session and has_pos)

        if in_session and has_pos:
            self.engine.monitor_market_realtime()
            return poll

        self.engine.set_monitor_active(False)
        return 1.0

    def _log_startup_watch_status(self) -> None:
        positions = _load_positions(self.engine)
        codes = [p.code for p in positions]
        if not self.engine.gateway.dry_run:
            try:
                self.engine.gateway._ensure_token()
            except Exception as e:
                logger.warning("⚠️ OAuth2 선행 실패(장중 재시도): %s", e)
        if codes:
            logger.info(
                "[실시간 실전 감시 시작] 현재 추적 중인 종목: %s · poll=%ss",
                codes,
                self.engine.cfg.watch.poll_interval_sec,
            )
        else:
            logger.info(
                "[실시간 감시 대기] 보유 종목 없음 — %s 스캔 · %s 진입 예정",
                self.engine.cfg.screener.screener_time,
                self.engine.cfg.strategy.entry_time,
            )

    def run_forever(self) -> None:
        logger.info("🚀 [무인 자동화 봇] v5.5.2 코스닥 스나이퍼 완전 자동 마스터 엔진")
        logger.info(
            "   스케줄 KST: %s KIS동기화 · %s 스캔 · %s 진입 · %s 정산 · %s~%s 감시(%ss) · dry_run=%s",
            STARTUP_SYNC_TIME,
            self.engine.cfg.screener.screener_time,
            self.engine.cfg.strategy.entry_time,
            self.engine.cfg.watch.market_close,
            self.engine.cfg.watch.market_open,
            self.engine.cfg.watch.market_close,
            self.engine.cfg.watch.poll_interval_sec,
            self.engine.gateway.dry_run,
        )
        self._log_startup_watch_status()
        while True:
            try:
                delay = self.tick()
            except KeyboardInterrupt:
                logger.info("⏹️ 사용자 중단 — 마스터 엔진 종료")
                raise
            except Exception as e:
                logger.exception("❌ 마스터 틱 오류(복구 대기): %s", e)
                delay = 5.0
            time.sleep(delay)


def run_master(*, dry_run: bool | None = None, project_root: str | None = None) -> None:
    root = project_root or str(Path(__file__).resolve().parents[2])
    prime_project_dotenv_from_root(Path(root))
    engine = LiveTradingEngine(project_root=root, dry_run=dry_run)
    LiveMasterRunner(engine).run_forever()
