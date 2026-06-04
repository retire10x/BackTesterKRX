"""
v5.5.2 완전 자동 마스터 스케줄러 — 15:15 스캔 · 15:20 진입 · 장중 0.5초 감시.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from src.live.live_engine import LiveTradingEngine, _load_positions, _now_kst
from src.live.live_screener import LiveScreener
from src.overnight_parity import prime_project_dotenv_from_root

KST = ZoneInfo("Asia/Seoul")
logger = logging.getLogger("LiveMaster")

STATE_REL = "config/live_master_state.json"


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

    def _today_key(self) -> str:
        return _now_kst().strftime("%Y-%m-%d")

    def _already_ran(self, key: str) -> bool:
        return self._state.get(key) == self._today_key()

    def _mark_ran(self, key: str) -> None:
        self._state[key] = self._today_key()
        _save_state(self.state_path, self._state)

    def _has_positions(self) -> bool:
        return bool(_load_positions(self.engine.paths["positions_json"]))

    def _run_screener_routine(self) -> None:
        if self._already_ran("screener_date"):
            return
        logger.info("🔔 [ROUTINE 1] 15:15 주도주 스캔 자동 실행")
        codes = self.screener.execute_daily_scan()
        self._mark_ran("screener_date")
        logger.info("   스캔 완료 — %d종", len(codes))
        if not codes:
            logger.warning("   0종 — 거래대금 미집계 시 15:18 이후 재시도 가능(당일 1회 제한 해제는 수동)")

    def _run_entry_routine(self) -> None:
        if self._already_ran("entry_date"):
            return
        logger.info("🔔 [ROUTINE 2] 15:20 변곡·듀얼 MA 진입 및 종가 주문")
        self.engine.calculate_entry_signals()
        self._mark_ran("entry_date")

    def tick(self) -> float:
        """
        마스터 루프 1회. 반환값 = 다음 sleep 초.
        """
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

        if _in_trigger_window(now, screener_hm):
            self._run_screener_routine()
            return 10.0

        if _in_trigger_window(now, entry_hm):
            self._run_entry_routine()
            return 10.0

        if _in_market_session(now, open_hm, close_hm) and self._has_positions():
            self.engine.monitor_market_realtime()
            return poll

        return 1.0

    def _log_startup_watch_status(self) -> None:
        positions = _load_positions(self.engine.paths["positions_json"])
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
        logger.info("🚀 [시스템 가동] v5.5.2 코스닥 스나이퍼 완전 자동 마스터 엔진")
        logger.info(
            "   스케줄 KST: %s 스캔 · %s 진입 · %s~%s 감시(%ss) · dry_run=%s",
            self.engine.cfg.screener.screener_time,
            self.engine.cfg.strategy.entry_time,
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
