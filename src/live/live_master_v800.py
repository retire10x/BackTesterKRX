"""
v8.0.0 ORB 마스터 스케줄러 — 08:50 기동 · 09:00~09:30 돌파 진입 · 14:50 강제청산 · 15:00 종료.

v5.5.2 15:20 일봉 진입 스케줄과 분리된 전용 루프.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.live.live_engine import LiveTradingEngine, _now_kst
from src.live.live_master import (
    LiveMasterRunner,
    STARTUP_SYNC_TIME,
    _in_market_session,
    _in_trigger_window,
    _load_state,
    _parse_hm,
    _save_state,
)
from src.live.live_orb import LiveOrbEngine, ORB_ENTRY_POLL_SEC, PREMARKET_SCAN_HM
from src.overnight_parity import prime_project_dotenv_from_root

KST = ZoneInfo("Asia/Seoul")
logger = logging.getLogger("LiveMasterV800")

MASTER_SHUTDOWN_HM = "15:00"
OPENING_HIGH_LOCK_HM = "09:05"
ORB_ENTRY_END_HM = "09:30"


class LiveMasterV800(LiveMasterRunner):
    """v8 ORB 타임라인 — 08:50~15:00."""

    def __init__(self, engine: LiveTradingEngine):
        super().__init__(engine)
        self.orb = LiveOrbEngine(engine)
        self._orb_entry_active = False

    def _run_premarket_scan(self) -> None:
        if self._already_ran("orb_premarket_date"):
            return
        logger.info("🔔 [ORB ROUTINE 1] %s 프리마켓 유니버스 (500억+ & 갭 +2~7%%)", PREMARKET_SCAN_HM)
        self.orb.reset_daily_state(_now_kst().strftime("%Y-%m-%d"))
        # TODO: pykrx/FDR + KIS 시세로 rows 채워 build_premarket_universe 호출
        self._mark_ran("orb_premarket_date")

    def _run_opening_high_lock(self) -> None:
        if self._already_ran("orb_opening_high_date"):
            return
        logger.info("📐 [ORB ROUTINE 2] %s Opening High(첫 5분봉 고가) 확정", OPENING_HIGH_LOCK_HM)
        # TODO: 분봉 수집 파이프라인 연결 후 update_opening_highs_from_minute_bars
        self.orb.state.opening_high_locked = True
        self._mark_ran("orb_opening_high_date")

    def _run_orb_entry_watch(self) -> None:
        if self._already_ran("orb_entry_date"):
            return
        result = self.orb.scan_breakout_entries()
        if int(result.get("executed_count", 0)) > 0:
            self.engine.save_daily_asset_snapshot()
            self._mark_ran("orb_entry_date")

    def tick(self) -> float:
        now = _now_kst()
        if now.weekday() >= 5:
            return 3600.0

        cfg = self.engine.cfg
        open_hm = cfg.watch.market_open
        poll = ORB_ENTRY_POLL_SEC

        self._ensure_kis_sync_catchup(now)

        if _in_trigger_window(now, STARTUP_SYNC_TIME):
            self._run_startup_sync_routine()
            return 10.0

        if _in_trigger_window(now, PREMARKET_SCAN_HM):
            self._run_premarket_scan()
            return 10.0

        if _in_trigger_window(now, OPENING_HIGH_LOCK_HM):
            self._run_opening_high_lock()
            return 5.0

        h_e, m_e = _parse_hm(ORB_ENTRY_END_HM)
        h_o, m_o = _parse_hm(OPENING_HIGH_LOCK_HM)
        entry_active = (
            (now.hour > h_o or (now.hour == h_o and now.minute >= m_o))
            and (now.hour < h_e or (now.hour == h_e and now.minute <= m_e))
        )
        if entry_active and not self._already_ran("orb_entry_date"):
            self._run_orb_entry_watch()
            return poll

        in_session = _in_market_session(now, open_hm, MASTER_SHUTDOWN_HM)
        self.engine.set_monitor_active(in_session)
        if in_session:
            self.orb.monitor_hit_and_run_exits(now)
            return cfg.watch.poll_interval_sec

        return 30.0


def run_master_v800(*, dry_run: bool | None = None, project_root: str | None = None) -> None:
    prime_project_dotenv_from_root(project_root)
    engine = LiveTradingEngine(dry_run=dry_run, project_root=project_root)
    master = LiveMasterV800(engine)
    logger.info("🚀 v8.0 ORB 마스터 기동 — 08:50~15:00 (일봉 15:20 진입 폐기)")
    while True:
        sleep_sec = master.tick()
        h, m = _parse_hm(MASTER_SHUTDOWN_HM)
        now = _now_kst()
        if now.hour > h or (now.hour == h and now.minute >= m):
            if now.weekday() < 5:
                logger.info("🛑 [ORB MASTER] %s 강제 종료", MASTER_SHUTDOWN_HM)
            time.sleep(max(sleep_sec, 60.0))
            continue
        time.sleep(sleep_sec)
