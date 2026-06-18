"""
v11.2 실시간 ORB 모의투자 마스터 러너.

  python run_v11_live_paper_trading.py --mock          # Mock 분봉 스트리밍 검증
  python run_v11_live_paper_trading.py                   # 실시간 대기 (09:00~15:30 KST)
  python run_v11_live_paper_trading.py --mock --speed 0  # Mock 즉시 완주

09:00~09:15 ORB 기준선 · 09:16~10:30 돌파 진입 · 15:20 타임스탑 · 15:30 EOD 정산
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from src.engine.capital_buffer_manager import (  # noqa: E402
    load_capital_buffer,
    save_capital_buffer,
)
from src.live.live_db_manager import LiveDbManager, dump_eod_reports  # noqa: E402
from src.live.live_orb_strategy import (  # noqa: E402
    EOD_SETTLE,
    ENTRY_END,
    ENTRY_START,
    ORB_SETUP_END,
    TIME_STOP,
    LiveORBStrategy,
    fetch_prev_day_turnover_universe,
)
from src.live.paper_trading_broker import (  # noqa: E402
    DEFAULT_INITIAL_CASH,
    MAX_SLOTS,
    SLOT_BUDGET,
    PaperTradingBroker,
)
from src.live.kis_minute_crawler import KisMinuteCrawler, MockMinuteStreamer  # noqa: E402
from src.utils.telegram_notifier import TelegramNotifier  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
logger = logging.getLogger("V11LivePaper")

STATE_REL = "config/v11_paper_state.json"
BROKER_STATE_REL = "config/v11_paper_broker.json"
KIS_META_REL = "config/v11_kis_position_meta.json"
DASHBOARD_SNAPSHOT_REL = "config/v11_dashboard_snapshot.json"
TRADES_CSV_REL = "outputs/v11_live_trades.csv"
LEDGER_CSV_REL = "outputs/v11_live_daily_ledger.csv"
STARTUP_SYNC_TIME = "08:50"

MOCK_UNIVERSE = [
    "005930", "000660", "035420", "051910", "006400",
    "035720", "005380", "068270", "028260", "105560",
]


def _load_env_file(path: str) -> None:
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh.read().splitlines():
                s = str(raw).strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                key, val = k.strip(), v.strip().strip("\"'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception:
        pass


_load_env_file(os.path.join(project_root, ".env"))


def _setup_logging(log_name: str = "v11_live_paper.log") -> None:
    log_dir = os.path.join(project_root, "logs")
    os.makedirs(log_dir, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    fh = logging.FileHandler(os.path.join(log_dir, log_name), encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)


def _now_kst() -> datetime:
    return datetime.now(KST)


def _is_weekday(dt: datetime) -> bool:
    return dt.weekday() < 5


def _is_market_hours(dt: datetime) -> bool:
    if not _is_weekday(dt):
        return False
    hm = (dt.hour, dt.minute)
    return (9, 0) <= hm <= (15, 30)


def _load_state(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _stock_name(code: str) -> str:
    try:
        from pykrx import stock as pykrx_stock
        nm = pykrx_stock.get_market_ticker_name(str(code).zfill(6))
        return nm or code
    except Exception:
        return code


def _append_ledger_row(row: dict) -> None:
    path = Path(project_root) / LEDGER_CSV_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.is_file()
    import csv
    with open(path, "a", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=[
                "date", "equity", "cash", "positions", "event",
                "amount_moved", "safe_vault", "had_trades",
            ],
        )
        if new_file:
            w.writeheader()
        w.writerow(row)


class V11LivePaperRunner:
    def __init__(
        self,
        *,
        mock: bool = False,
        mock_speed: float = 0.05,
        local: bool = False,
        dry_run: bool | None = None,
    ):
        self.mock = mock
        self.mock_speed = mock_speed
        self.local = local
        self.dry_run = dry_run
        self.root = Path(project_root)
        self.state_path = self.root / STATE_REL
        self.state = _load_state(self.state_path)
        self.capital_buffer = load_capital_buffer(project_root=self.root)
        self.telegram = TelegramNotifier(project_root=self.root)
        self._purge_legacy_local_state()
        self.db = LiveDbManager(project_root=self.root)
        self.gateway = self._build_gateway()
        self.broker = self._build_broker()
        self.strategy = LiveORBStrategy(
            broker=self.broker,
            name_lookup=_stock_name,
            trades_csv=self.root / TRADES_CSV_REL,
            notifier=self.telegram,
            db_manager=self.db,
        )
        self.watch_codes: list[str] = []
        self.crawler: KisMinuteCrawler | MockMinuteStreamer | None = None
        self._running = True
        self._mock_pass = False

    def _purge_legacy_local_state(self) -> None:
        """KIS 이관 — 로컬 가상계좌·오염 DB 자동 정리 (1회)."""
        if self.mock:
            return
        if self.state.get("kis_legacy_purged"):
            return
        targets = [
            self.root / BROKER_STATE_REL,
            self.root / "data" / "live_trading.db",
        ]
        for path in targets:
            if path.is_file():
                path.unlink()
                logger.info("🗑 레거시 삭제 — %s", path.name)
        self.state["kis_legacy_purged"] = True
        _save_state(self.state_path, self.state)

    def _build_gateway(self):
        if self.mock:
            return None

        from dataclasses import replace

        from src.live.live_account import LiveAccountGateway
        from src.live.live_config import load_live_config

        cfg = load_live_config()
        account = replace(
            cfg.account,
            mode="paper",
            bet_amount_per_slot=SLOT_BUDGET,
            max_slots_limit=MAX_SLOTS,
            min_slots_limit=1,
        )
        gateway = LiveAccountGateway(account, dry_run=self.dry_run)
        if gateway.mode != "paper":
            raise RuntimeError("v11 KIS 연동은 paper 모드만 허용합니다.")
        logger.info(
            "📡 KIS Gateway — 계좌 %s · dry_run=%s",
            gateway.account_number,
            gateway.dry_run,
        )
        return gateway

    def _build_broker(self):
        if self.mock or self.local:
            return PaperTradingBroker(
                state_path=None if self.mock else self.root / BROKER_STATE_REL,
            )

        from src.live.kis_paper_adapter import KisPaperBrokerAdapter

        if self.gateway is None:
            raise RuntimeError("KIS Gateway 미초기화")
        logger.info("💰 KIS 모의투자 어댑터 — 예산 %s원", f"{DEFAULT_INITIAL_CASH:,.0f}")
        return KisPaperBrokerAdapter(
            gateway=self.gateway,
            initial_capital=DEFAULT_INITIAL_CASH,
            max_slots=MAX_SLOTS,
            slot_budget=SLOT_BUDGET,
            meta_path=self.root / KIS_META_REL,
            project_root=self.root,
        )

    def _sync_kis_if_needed(self) -> None:
        if hasattr(self.broker, "sync_positions"):
            if self._already_done("kis_sync_date"):
                return
            try:
                self.broker.sync_positions()
            except Exception as exc:
                logger.error("❌ KIS 동기화 실패 (무시하고 진행): %s", exc)
                return
            self._mark_done("kis_sync_date")

    def _ensure_kis_sync_catchup(self, now: datetime) -> None:
        if not hasattr(self.broker, "sync_positions"):
            return
        if self._already_done("kis_sync_date"):
            return
        h, m = map(int, STARTUP_SYNC_TIME.split(":"))
        sync_at = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if now >= sync_at:
            logger.info("📡 [동기화 보정] %s 경과 · KIS 잔고 즉시 동기화", STARTUP_SYNC_TIME)
            try:
                self.broker.sync_positions()
            except Exception as exc:
                logger.error("❌ KIS 동기화 보정 실패 (무시하고 진행): %s", exc)
                return
            self._mark_done("kis_sync_date")

    def _today_key(self) -> str:
        return _now_kst().strftime("%Y-%m-%d")

    def _already_done(self, key: str) -> bool:
        return self.state.get(key) == self._today_key()

    def _mark_done(self, key: str) -> None:
        self.state[key] = self._today_key()
        _save_state(self.state_path, self.state)

    def _build_universe(self) -> list[str]:
        if self.mock:
            return MOCK_UNIVERSE[:10]
        codes = fetch_prev_day_turnover_universe(watch_size=25)
        if not codes:
            logger.warning("유니버스 조회 실패 — Mock 종목 폴백")
            return MOCK_UNIVERSE[:10]
        return codes

    def _wait_until_market_open(self) -> None:
        while self._running:
            now = _now_kst()
            if _is_market_hours(now):
                return
            if not _is_weekday(now):
                nxt = (now + timedelta(days=1)).replace(hour=8, minute=55, second=0, microsecond=0)
                while nxt.weekday() >= 5:
                    nxt += timedelta(days=1)
            else:
                nxt = now.replace(hour=9, minute=0, second=0, microsecond=0)
                if now >= nxt:
                    nxt = now.replace(hour=15, minute=31, second=0, microsecond=0)
                    if now >= nxt:
                        nxt = (now + timedelta(days=1)).replace(hour=8, minute=55, second=0, microsecond=0)
                        while nxt.weekday() >= 5:
                            nxt += timedelta(days=1)
            wait_sec = max(1, int((nxt - now).total_seconds()))
            logger.info(
                "⏳ 장 외 대기 — %s KST 개장까지 %d분 %d초",
                nxt.strftime("%Y-%m-%d %H:%M"),
                wait_sec // 60,
                wait_sec % 60,
            )
            for _ in range(min(wait_sec, 300)):
                if not self._running:
                    return
                time.sleep(1)
            if wait_sec > 300:
                time.sleep(wait_sec - 300)

    def _run_eod_settlement(self) -> None:
        if self._already_done("eod_settle_date"):
            return
        eq = self.broker.total_equity()
        result = self.capital_buffer.rebalance(
            eq,
            has_realized_pnl=self.broker.had_trades_today,
        )
        if result.cash_delta:
            self.broker.cash += result.cash_delta
            if hasattr(self.broker, "_persist"):
                self.broker._persist()
        _append_ledger_row({
            "date": self._today_key(),
            "equity": f"{eq:,.0f}",
            "cash": f"{self.broker.cash:,.0f}",
            "positions": len(self.broker.positions),
            "event": result.event,
            "amount_moved": f"{result.amount_moved:,.0f}",
            "safe_vault": f"{self.capital_buffer.safe_vault:,.0f}",
            "had_trades": self.broker.had_trades_today,
        })
        save_capital_buffer(self.capital_buffer, project_root=self.root)
        self._mark_done("eod_settle_date")
        dump_eod_reports(
            self.db,
            project_root=self.root,
            safe_vault=self.capital_buffer.safe_vault,
            total_equity=eq,
            rebalance_event=result.event,
        )
        self.telegram.notify_eod(
            total_equity=eq,
            safe_vault=self.capital_buffer.safe_vault,
            event=result.event,
            amount_moved=result.amount_moved,
        )
        logger.info(
            "📊 [15:30 EOD] 총자산 %s원 · Safe Vault %s원 · 이벤트=%s",
            f"{eq:,.0f}",
            f"{self.capital_buffer.safe_vault:,.0f}",
            result.event,
        )

    def _record_equity_snapshot(self, sim_now: datetime | None = None) -> None:
        now = sim_now or _now_kst()
        eq = self.broker.total_equity()
        cash = self.broker.cash
        used = max(0.0, eq - cash)
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        self.db.insert_equity_snapshot(
            total_equity=eq,
            safe_vault=self.capital_buffer.safe_vault,
            used_cash=used,
            timestamp=ts,
        )
        self._persist_dashboard_snapshot(now)

    def _persist_dashboard_snapshot(self, now: datetime | None = None) -> None:
        """대시보드 SSOT — 감시 유니버스·보유 포지션 스냅샷 (매 분 갱신)."""
        ts = (now or _now_kst()).strftime("%Y-%m-%d %H:%M:%S")
        last_prices = getattr(self.broker, "_last_prices", {}) or {}
        positions: list[dict] = []
        for code, pos in self.broker.positions.items():
            c6 = str(code).zfill(6)
            entry = float(pos.entry_price)
            last_px = float(last_prices.get(c6) or entry)
            pnl_rate = (last_px - entry) / entry if entry > 0 else 0.0
            positions.append({
                "code": c6,
                "name": _stock_name(c6),
                "entry_price": entry,
                "qty": int(pos.qty),
                "current_price": last_px,
                "pnl_rate": pnl_rate,
            })
        watch_items = [
            {
                "rank": idx,
                "code": str(code).zfill(6),
                "name": _stock_name(str(code).zfill(6)),
                "avg_volume": float(self.strategy.avg_volumes.get(str(code).zfill(6), 0)),
            }
            for idx, code in enumerate(self.watch_codes, 1)
        ]
        payload = {
            "updated_at": ts,
            "watch_count": len(self.watch_codes),
            "watch_items": watch_items,
            "positions": positions,
            "open_slot_count": len(positions),
            "max_slots": getattr(self.broker, "max_slots", 4),
            "available_cash": float(self.broker.cash),
            "total_equity": float(self.broker.total_equity()),
        }
        _save_state(self.root / DASHBOARD_SNAPSHOT_REL, payload)

    def _process_bars(self, bars_by_code: dict[str, list], sim_now: datetime | None = None) -> None:
        now = sim_now or _now_kst()
        hm = (now.hour, now.minute)

        if hm >= ORB_SETUP_END and not self.strategy._orb_locked:
            self.strategy.lock_orb_setups(bars_by_code)

        for code, bars in bars_by_code.items():
            if not bars:
                continue
            latest = bars[-1]
            self.strategy.on_minute_bar(latest, bars)

        if hm >= TIME_STOP and self.broker.positions:
            self.strategy.on_time_stop(bars_by_code)

        if hm >= EOD_SETTLE:
            self._run_eod_settlement()

    def _force_equity_log_if_new_minute(
        self,
        now: datetime,
        last_equity_minute: tuple[int, int] | None,
    ) -> tuple[int, int] | None:
        """장중(09:00~15:30) 매 분 equity 스냅샷 강제 INSERT — ORB 관망 구간 포함."""
        if not _is_market_hours(now):
            return last_equity_minute
        hm = (now.hour, now.minute)
        if hm == last_equity_minute:
            return last_equity_minute
        self._record_equity_snapshot(sim_now=now)
        logger.info(
            "📸 equity 스냅샷 기록 — %s · %s원",
            now.strftime("%H:%M:%S"),
            f"{self.broker.total_equity():,.0f}",
        )
        return hm

    def run_mock(self) -> bool:
        """Mock 분봉 하루치 스트리밍 — PASS/FAIL 반환."""
        logger.info("🧪 [MOCK] v11.2 ORB 모의투자 검증 시작 (속도=%.3fs/분)", self.mock_speed)
        self.state = {}
        _save_state(self.state_path, self.state)
        self.broker = PaperTradingBroker(state_path=None)
        self.strategy = LiveORBStrategy(
            broker=self.broker,
            name_lookup=_stock_name,
            trades_csv=self.root / TRADES_CSV_REL,
            notifier=self.telegram,
            db_manager=self.db,
        )
        self.strategy.reset_day()
        self.watch_codes = self._build_universe()
        self.strategy.prepare_universe(self.watch_codes)
        self._persist_dashboard_snapshot()
        preview = ", ".join(_stock_name(c) for c in self.watch_codes[:3])
        self.telegram.notify_startup(watch_count=len(self.watch_codes), watch_preview=preview)
        streamer = MockMinuteStreamer(self.watch_codes, speed_sec=self.mock_speed)
        self.crawler = streamer

        initial_eq = self.broker.total_equity()
        steps = 0
        had_entry = False

        while streamer.step():
            steps += 1
            sim_now = streamer.current_sim_time()
            if sim_now is None:
                continue
            bars_by_code = {c: streamer.bars_for(c) for c in self.watch_codes}
            self._process_bars(bars_by_code, sim_now=sim_now)
            self._record_equity_snapshot(sim_now=sim_now)
            if self.broker.fills and any(f.side == "BUY" for f in self.broker.fills):
                had_entry = True
            if self.mock_speed > 0:
                time.sleep(self.mock_speed)

        self._run_eod_settlement()
        final_eq = self.broker.total_equity()
        orb_locked = self.strategy._orb_locked
        had_trades = self.broker.had_trades_today

        checks = {
            "orb_setup_locked": orb_locked,
            "minute_steps": steps > 100,
            "eod_settled": self._already_done("eod_settle_date"),
        }
        passed = all(checks.values())
        logger.info("🧪 [MOCK] 검증 결과: %s", "PASS ✅" if passed else "FAIL ❌")
        for k, v in checks.items():
            logger.info("  - %s: %s", k, "OK" if v else "NG")
        logger.info(
            "  - 초기자산 %s → 최종 %s · 매매=%s · 진입시도=%s",
            f"{initial_eq:,.0f}",
            f"{final_eq:,.0f}",
            "Y" if had_trades else "N",
            "Y" if had_entry else "N",
        )
        self._mock_pass = passed
        return passed

    async def _live_loop_async(self) -> None:
        self._sync_kis_if_needed()
        self.strategy.reset_day()

        # 👇 [수정] 어디서 멈추는지 확인하기 위한 추적 로그 추가
        logger.info("⏳ KRX 유니버스(코스피200) 데이터 수집 시작...")
        self.watch_codes = self._build_universe()
        logger.info(f"✅ 유니버스 수집 완료! (총 {len(self.watch_codes)}종목)")

        self.strategy.prepare_universe(self.watch_codes)
        self._persist_dashboard_snapshot()
        preview = ", ".join(_stock_name(c) for c in self.watch_codes[:3]) if self.watch_codes else "None"

        logger.info("⏳ 텔레그램 시작 알림 전송 중...")
        try:
            self.telegram.notify_startup(watch_count=len(self.watch_codes), watch_preview=preview)
            logger.info("✅ 텔레그램 알림 전송 완료!")
        except Exception as e:
            logger.error(f"❌ 텔레그램 전송 실패 (무시하고 진행): {e}")

        logger.info("📋 감시 유니버스 %d종: %s", len(self.watch_codes), ", ".join(self.watch_codes[:5]) + "...")

        if self.gateway is None:
            raise RuntimeError("KIS Gateway 필요 — mock 모드가 아닌 경우 gateway가 없습니다.")
        crawler = KisMinuteCrawler(self.watch_codes, gateway=self.gateway)
        self.crawler = crawler
        last_minute: tuple[int, int] | None = None
        last_equity_minute: tuple[int, int] | None = None

        # 09:00 개장 직후 첫 스냅샷 선기록 (ORB 관망 구간 차트 공백 방지)
        open_now = _now_kst()
        if _is_market_hours(open_now):
            last_equity_minute = self._force_equity_log_if_new_minute(open_now, last_equity_minute)

        while self._running:
            now = _now_kst()
            self._ensure_kis_sync_catchup(now)
            last_equity_minute = self._force_equity_log_if_new_minute(now, last_equity_minute)

            if not _is_market_hours(now):
                if now.hour > 15 or (now.hour == 15 and now.minute > 30):
                    if not self._already_done("eod_settle_date"):
                        bars_by_code = {c: crawler.bars_for(c) for c in self.watch_codes}
                        self._process_bars(bars_by_code, sim_now=now)
                        self._record_equity_snapshot(sim_now=now)
                    logger.info("🏁 장 마감 — 내일 09:00까지 대기")
                    self._wait_until_market_open()
                    self.strategy.reset_day()
                    self.state = _load_state(self.state_path)
                    last_minute = None
                    continue
                await asyncio.sleep(30)
                continue

            hm = (now.hour, now.minute)
            if hm != last_minute and now.second >= 3:
                crawler.poll_once(now=now)
                bars_by_code = {c: crawler.bars_for(c) for c in self.watch_codes}
                self._process_bars(bars_by_code, sim_now=now)
                last_minute = hm

                # 👇 [여기에 하트비트 로그 추가] 👇
                logger.info(f"👀 {now.strftime('%H:%M')} 장중 감시 중... (현재 유니버스 {len(self.watch_codes)}종목 추적 중)")

            await asyncio.sleep(1)

    def run_live(self) -> None:
        broker_mode = "KIS 모의투자" if hasattr(self.broker, "sync_positions") else "로컬 가상"
        quote_mode = "KIS 분봉"
        logger.info(
            "🚀 v11.2 ORB 실시간 모의투자 (%s · %s) — 09:00~15:30 KST",
            broker_mode,
            quote_mode,
        )
        self._wait_until_market_open()
        try:
            asyncio.run(self._live_loop_async())
        except KeyboardInterrupt:
            logger.info("⏹ 사용자 중단")
            self._running = False


def main() -> int:
    parser = argparse.ArgumentParser(description="v11.2 ORB Live Paper Trading")
    parser.add_argument("--mock", action="store_true", help="Mock 분봉 스트리밍 검증")
    parser.add_argument("--speed", type=float, default=0.05, help="Mock 분당 지연(초). 0=즉시")
    parser.add_argument("--local", action="store_true", help="로컬 PaperTradingBroker 사용 (KIS 미연동)")
    parser.add_argument("--dry-run", action="store_true", help="KIS 주문 없이 시뮬 (LIVE_DRY_RUN=1)")
    parser.add_argument("--reset", action="store_true", help="로컬 가상계좌·DB 초기화 후 종료")
    args = parser.parse_args()

    if args.reset:
        from src.live.live_db_manager import init_v11_schema

        targets = [
            Path(project_root) / "config" / "v11_paper_broker.json",
            Path(project_root) / "config" / "v11_kis_position_meta.json",
            Path(project_root) / "data" / "live_trading.db",
            Path(project_root) / "data" / "live_trading.db-wal",
            Path(project_root) / "data" / "live_trading.db-shm",
        ]
        for path in targets:
            if path.exists():
                path.unlink()
                print(f"Deleted: {path.name}")
        init_v11_schema(project_root=project_root)
        print("[OK] v11 DB reset complete — live_equity · live_trades schema created.")
        return 0

    _setup_logging()
    dry = True if args.dry_run else None
    runner = V11LivePaperRunner(
        mock=args.mock,
        mock_speed=args.speed,
        local=args.local,
        dry_run=dry,
    )

    if args.mock:
        ok = runner.run_mock()
        return 0 if ok else 1

    runner.run_live()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
