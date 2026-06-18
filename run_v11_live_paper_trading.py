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
from src.live.paper_trading_broker import PaperTradingBroker  # noqa: E402
from src.naver_minute_crawler import MockMinuteStreamer, NaverMinuteCrawler  # noqa: E402
from src.utils.telegram_notifier import TelegramNotifier  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
logger = logging.getLogger("V11LivePaper")

STATE_REL = "config/v11_paper_state.json"
BROKER_STATE_REL = "config/v11_paper_broker.json"
TRADES_CSV_REL = "outputs/v11_live_trades.csv"
LEDGER_CSV_REL = "outputs/v11_live_daily_ledger.csv"

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
    def __init__(self, *, mock: bool = False, mock_speed: float = 0.05):
        self.mock = mock
        self.mock_speed = mock_speed
        self.root = Path(project_root)
        self.state_path = self.root / STATE_REL
        self.state = _load_state(self.state_path)
        self.capital_buffer = load_capital_buffer(project_root=self.root)
        self.db = LiveDbManager(project_root=self.root)
        self.telegram = TelegramNotifier(project_root=self.root)
        self.broker = PaperTradingBroker(
            state_path=self.root / BROKER_STATE_REL,
        )
        self.strategy = LiveORBStrategy(
            broker=self.broker,
            name_lookup=_stock_name,
            trades_csv=self.root / TRADES_CSV_REL,
            notifier=self.telegram,
            db_manager=self.db,
        )
        self.watch_codes: list[str] = []
        self.crawler: NaverMinuteCrawler | MockMinuteStreamer | None = None
        self._running = True
        self._mock_pass = False

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
        eq = self.broker.total_equity()
        cash = self.broker.cash
        used = max(0.0, eq - cash)
        ts = (sim_now or _now_kst()).strftime("%Y-%m-%d %H:%M:%S")
        self.db.insert_equity_snapshot(
            total_equity=eq,
            safe_vault=self.capital_buffer.safe_vault,
            used_cash=used,
            timestamp=ts,
        )

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
        self.strategy.reset_day()
        self.watch_codes = self._build_universe()
        self.strategy.prepare_universe(self.watch_codes)
        preview = ", ".join(_stock_name(c) for c in self.watch_codes[:3])
        self.telegram.notify_startup(watch_count=len(self.watch_codes), watch_preview=preview)
        logger.info("📋 감시 유니버스 %d종: %s", len(self.watch_codes), ", ".join(self.watch_codes[:5]) + "...")

        crawler = NaverMinuteCrawler(self.watch_codes)
        self.crawler = crawler
        last_minute: tuple[int, int] | None = None
        last_equity_minute: tuple[int, int] | None = None

        # 09:00 개장 직후 첫 스냅샷 선기록 (ORB 관망 구간 차트 공백 방지)
        open_now = _now_kst()
        if _is_market_hours(open_now):
            last_equity_minute = self._force_equity_log_if_new_minute(open_now, last_equity_minute)

        while self._running:
            now = _now_kst()
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

            await asyncio.sleep(1)

    def run_live(self) -> None:
        logger.info("🚀 v11.2 ORB 실시간 모의투자 시작 — 09:00~15:30 KST")
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
    args = parser.parse_args()

    _setup_logging()
    runner = V11LivePaperRunner(mock=args.mock, mock_speed=args.speed)

    if args.mock:
        ok = runner.run_mock()
        return 0 if ok else 1

    runner.run_live()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
