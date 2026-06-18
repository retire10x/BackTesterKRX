"""
v11.2 라이브 ORB 데이트레이딩 전략 코어.

09:00~09:15 분봉 15개로 기준선 확정 → 09:16~10:30 돌파 진입 → 분 단위 TP/SL 감시.
"""
from __future__ import annotations

import csv
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Callable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from pykrx import stock as pykrx_stock

from src.engine.orb_strategy_v11 import (
    ORBSetup,
    detect_orb_breakout,
    evaluate_orb_exit,
    passes_ma5_alignment,
)
from src.live.minute_bar import MinuteBar
from src.live.paper_trading_broker import PaperTradingBroker

if TYPE_CHECKING:
    from src.live.kis_paper_adapter import KisPaperBrokerAdapter
    from src.live.live_db_manager import LiveDbManager
    from src.utils.telegram_notifier import TelegramNotifier

logger = logging.getLogger("LiveORB")
KST = ZoneInfo("Asia/Seoul")

ORB_SETUP_END = (9, 15)
ENTRY_START = (9, 16)
ENTRY_END = (10, 30)
TIME_STOP = (15, 20)
EOD_SETTLE = (15, 30)

DEFAULT_WATCH_SIZE = 25
TOP_N_TURNOVER = 100


def _hm_tuple(dt: datetime) -> tuple[int, int]:
    return dt.hour, dt.minute


def _at_or_after(dt: datetime, hm: tuple[int, int]) -> bool:
    return _hm_tuple(dt) >= hm


def _at_or_before(dt: datetime, hm: tuple[int, int]) -> bool:
    return _hm_tuple(dt) <= hm


def _in_window(dt: datetime, start: tuple[int, int], end: tuple[int, int]) -> bool:
    return _at_or_after(dt, start) and _at_or_before(dt, end)


def build_orb_setup_from_minutes(bars: list[MinuteBar]) -> ORBSetup | None:
    """09:00~09:15 분봉으로 ORB 기준선 확정 — 상단=고가 최대, 하단=저가 최소."""
    if len(bars) < 15:
        return None
    range_bars = bars[:15]
    open_px = range_bars[0].open
    if open_px <= 0:
        return None
    orb_high = max(b.high for b in range_bars)
    orb_low = min(b.low for b in range_bars)
    if orb_high <= open_px or orb_low <= 0:
        return None
    return ORBSetup(orb_high=orb_high, orb_low=orb_low, open_px=open_px)


def _day_bars_as_ohlc(bars: list[MinuteBar]) -> dict[str, float]:
    if not bars:
        return {"open": 0, "high": 0, "low": 0, "close": 0, "volume": 0}
    return {
        "open": bars[0].open,
        "high": max(b.high for b in bars),
        "low": min(b.low for b in bars),
        "close": bars[-1].close,
        "volume": sum(b.volume for b in bars),
    }


def fetch_prev_day_turnover_universe(
    *,
    as_of: datetime | None = None,
    top_n: int = TOP_N_TURNOVER,
    watch_size: int = DEFAULT_WATCH_SIZE,
    require_ma5: bool = True,
) -> list[str]:
    """전일 거래대금 Top N → MA5 필터 → 감시 20~30종."""
    now = as_of or datetime.now(KST)
    probe = now
    for _ in range(7):
        probe -= timedelta(days=1)
        if probe.weekday() < 5:
            break
    ymd = probe.strftime("%Y%m%d")

    rows: list[tuple[str, float]] = []
    for market in ("KOSPI", "KOSDAQ"):
        try:
            ohlcv = pykrx_stock.get_market_ohlcv_by_ticker(ymd, market=market)
        except Exception:
            continue
        if ohlcv is None or ohlcv.empty:
            continue
        for ticker, row in ohlcv.iterrows():
            c6 = str(ticker).zfill(6)
            close_px = float(row.get("종가") or row.get("close") or 0)
            vol = float(row.get("거래량") or row.get("volume") or 0)
            if close_px > 0 and vol > 0:
                rows.append((c6, close_px * vol))

    rows.sort(key=lambda x: x[1], reverse=True)
    candidates = [c for c, _ in rows[:top_n]]

    if not require_ma5:
        return candidates[:watch_size]

    filtered: list[str] = []
    for c6 in candidates:
        try:
            hist = pykrx_stock.get_market_ohlcv_by_date(
                (probe - timedelta(days=30)).strftime("%Y%m%d"),
                ymd,
                c6,
            )
        except Exception:
            continue
        if hist is None or len(hist) < 6:
            continue
        closes = hist["종가"] if "종가" in hist.columns else hist.iloc[:, 3]
        if passes_ma5_alignment(closes):
            filtered.append(c6)
        if len(filtered) >= watch_size:
            break
    return filtered[:watch_size]


def _avg_volume_5d(code: str, as_of: datetime) -> float:
    ymd = as_of.strftime("%Y%m%d")
    try:
        hist = pykrx_stock.get_market_ohlcv_by_date(
            (as_of - timedelta(days=14)).strftime("%Y%m%d"),
            ymd,
            str(code).zfill(6),
        )
    except Exception:
        return 0.0
    if hist is None or len(hist) < 2:
        return 0.0
    vol_col = "거래량" if "거래량" in hist.columns else hist.columns[-1]
    vols = hist[vol_col].iloc[:-1]
    if len(vols) >= 5:
        vols = vols.iloc[-5:]
    return float(vols.mean()) if not vols.empty else 0.0


@dataclass
class LiveORBStrategy:
    """분봉 기반 ORB 라이브 전략."""

    broker: PaperTradingBroker | KisPaperBrokerAdapter
    name_lookup: Callable[[str], str] = field(default=lambda c: c)
    setups: dict[str, ORBSetup] = field(default_factory=dict)
    entered_today: set[str] = field(default_factory=set)
    avg_volumes: dict[str, float] = field(default_factory=dict)
    trades_csv: Path | None = None
    notifier: TelegramNotifier | None = None
    db_manager: LiveDbManager | None = None
    _orb_locked: bool = False
    _last_bar_hm: dict[str, str] = field(default_factory=dict)

    UNIVERSE_PREP_DELAY_SEC = 0.5

    def prepare_universe(self, codes: list[str], *, as_of: datetime | None = None) -> None:
        """감시 종목별 5일 평균 거래량 — 종목당 딜레이·실패 시 스킵."""
        now = as_of or datetime.now(KST)
        volumes: dict[str, float] = {}
        total = len(codes)
        for idx, code in enumerate(codes):
            c6 = str(code).zfill(6)
            try:
                volumes[c6] = _avg_volume_5d(c6, now)
            except Exception as exc:
                logger.warning("prepare_universe 스킵 %s — %s", c6, exc)
                volumes[c6] = 0.0
            if idx + 1 < total:
                time.sleep(self.UNIVERSE_PREP_DELAY_SEC)
        self.avg_volumes = volumes
        logger.info("prepare_universe 완료 — %d/%d종", len(volumes), total)

    def lock_orb_setups(self, bars_by_code: dict[str, list[MinuteBar]]) -> int:
        """09:15 이후 ORB 기준선 확정."""
        if self._orb_locked:
            return len(self.setups)
        count = 0
        for code, bars in bars_by_code.items():
            setup = build_orb_setup_from_minutes(bars)
            if setup is not None:
                # 👇 [임시] 돌파 기준선을 시가의 반값으로 떡락시킵니다.
                setup.orb_high = setup.open_px * 0.5

                self.setups[code] = setup
                count += 1
                logger.info(
                    "📐 [%s] ORB 기준선 확정 — 고점 %.0f원 (시가 %.0f원)",
                    self.name_lookup(code),
                    setup.orb_high,
                    setup.open_px,
                )
        if count > 0:
            self._orb_locked = True
        return count

    def on_minute_bar(self, bar: MinuteBar, day_bars: list[MinuteBar]) -> None:
        """매 분봉 수신 시 진입·청산 판단."""
        self._last_bar_hm[bar.code] = bar.hm
        self.broker.update_quote(bar.code, bar.close)
        hm = _hm_tuple(bar.dt)

        if hm <= ORB_SETUP_END and not self._orb_locked:
            return

        if _in_window(bar.dt, ENTRY_START, ENTRY_END):
            self._try_entry(bar, day_bars)

        if bar.code in self.broker.positions or bar.code in self.entered_today:
            self._try_exit(bar, day_bars, force_eod=False)

    def on_time_stop(self, bars_by_code: dict[str, list[MinuteBar]]) -> None:
        for code in list(self.broker.positions.keys()):
            bars = bars_by_code.get(code, [])
            if not bars:
                continue
            bar = bars[-1]
            self.broker.update_quote(code, bar.close)
            self._try_exit(bar, bars, force_eod=True)

    def _try_entry(self, bar: MinuteBar, day_bars: list[MinuteBar]) -> None:
        code = bar.code
        if code in self.broker.positions or code in self.entered_today:
            return
        if self.broker.available_slots <= 0:
            return
        setup = self.setups.get(code)
        if setup is None:
            return

        ohlc = _day_bars_as_ohlc(day_bars)
        avg_vol = self.avg_volumes.get(code, 0.0)
        if not detect_orb_breakout(
            open_px=ohlc["open"],
            high_px=ohlc["high"],
            low_px=ohlc["low"],
            close_px=ohlc["close"],
            volume=ohlc["volume"],
            avg_volume_5d=avg_vol,
            setup=setup,
        ):
            return

        fill = self.broker.place_market_buy(
            code,
            price=bar.close,
            orb_high=setup.orb_high,
            note="ORB_BREAKOUT",
        )
        if fill:
            self.entered_today.add(code)
            nm = self.name_lookup(code)
            logger.info(
                "[%s] %s 1분봉 돌파! KIS 모의 매수 체결 — 현재가 %s원 (수량 %d)",
                bar.hm,
                nm,
                f"{bar.close:,.0f}",
                fill.qty,
            )
            self._append_trade_csv(fill, exit_type="ORB_BREAKOUT")
            self._notify_buy(fill, bar.hm)

    def _notify_buy(self, fill, hm: str) -> None:
        if self.notifier:
            self.notifier.notify_buy(
                name=self.name_lookup(fill.code),
                code=fill.code,
                price=fill.price,
                hm=hm,
            )

    def _try_exit(self, bar: MinuteBar, day_bars: list[MinuteBar], *, force_eod: bool) -> None:
        code = bar.code
        pos = self.broker.positions.get(code)
        if pos is None:
            return

        ohlc = _day_bars_as_ohlc(day_bars)
        decision = evaluate_orb_exit(
            entry_price=pos.entry_price,
            open_px=ohlc["open"],
            high_px=ohlc["high"],
            low_px=ohlc["low"],
            close_px=ohlc["close"],
            partial_tp_done=pos.partial_tp_done,
            risk_free=pos.risk_free,
            breakeven_stop=pos.breakeven_stop,
            force_eod=force_eod,
        )
        if decision is None:
            return

        sell_ratio = decision.sell_ratio
        fill = self.broker.place_market_sell(
            code,
            price=decision.exit_price,
            sell_ratio=sell_ratio,
            note=decision.exit_type,
        )
        if not fill:
            return

        if decision.exit_type == "PARTIAL_TP_50" and code in self.broker.positions:
            p = self.broker.positions[code]
            p.partial_tp_done = True
            p.risk_free = True
            p.breakeven_stop = p.entry_price

        nm = self.name_lookup(code)
        logger.info(
            "[%s] %s 청산 (%s) — %.0f원 × %d주",
            bar.hm,
            nm,
            decision.exit_type,
            decision.exit_price,
            fill.qty,
        )
        self._append_trade_csv(fill, exit_type=decision.exit_type, entry_price=pos.entry_price)
        self._notify_sell(fill, decision.exit_type, bar.hm)

    def _notify_sell(self, fill, exit_type: str, hm: str) -> None:
        if self.notifier:
            self.notifier.notify_sell(
                name=self.name_lookup(fill.code),
                code=fill.code,
                pnl_rate=fill.pnl_rate,
                reason=exit_type,
                hm=hm,
            )
        if self.db_manager and fill.side == "SELL":
            self.db_manager.insert_trade(
                entry_time=fill.entry_time or fill.timestamp,
                exit_time=fill.timestamp,
                code=fill.code,
                name=self.name_lookup(fill.code),
                buy_price=fill.entry_price,
                sell_price=fill.price,
                qty=fill.qty,
                pnl_rate=fill.pnl_rate,
                exit_reason=exit_type,
            )

    def reset_day(self) -> None:
        self.setups.clear()
        self.entered_today.clear()
        self.avg_volumes.clear()
        self._orb_locked = False
        self._last_bar_hm.clear()
        self.broker.reset_day_flags()

    def _append_trade_csv(
        self,
        fill,
        *,
        exit_type: str,
        entry_price: float | None = None,
    ) -> None:
        if not self.trades_csv:
            return
        os.makedirs(self.trades_csv.parent, exist_ok=True)
        new_file = not self.trades_csv.is_file()
        with open(self.trades_csv, "a", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            if new_file:
                w.writerow([
                    "timestamp", "code", "side", "qty", "price",
                    "fee", "slippage_cost", "net_cash_delta", "exit_type", "entry_price",
                ])
            w.writerow([
                fill.timestamp,
                fill.code,
                fill.side,
                fill.qty,
                f"{fill.price:.2f}",
                f"{fill.fee:.2f}",
                f"{fill.slippage_cost:.2f}",
                f"{fill.net_cash_delta:.2f}",
                exit_type,
                "" if entry_price is None else f"{entry_price:.2f}",
            ])
