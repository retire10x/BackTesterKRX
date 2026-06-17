"""
v10.1 프리셋 라이브 공통 코어 — 장세 자동 분류 + 프리셋 엔진.

Momentum / Swing / Cash 3종. v10.1부터 market_classifier가 15:15 Fact 기반으로 프리셋 결정.
"""
from __future__ import annotations

import json
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from pykrx import stock

from src.live.live_config import LiveTradingConfig, load_live_config, resolve_live_paths
from src.live.live_engine import LiveTradingEngine, _load_positions, _now_kst, _save_positions
from src.live.live_master import LiveMasterRunner, _in_trigger_window, _in_market_session
from src.overnight_parity import prime_project_dotenv_from_root
from src.utils.date_helper import resolve_overnight_scan_anchor

KST = ZoneInfo("Asia/Seoul")
logger = logging.getLogger("V10LiveCore")
STATE_REL = "config/v10_position_state.json"
STARTUP_SYNC_TIME = "08:50"
REGIME_CHECK_TIME = "15:15"
INTRADAY_STOP_RATIO = 0.04  # 평단 대비 -4% 장중 즉시 탈출


def _parse_hm(hm: str) -> tuple[int, int]:
    h, m = hm.strip().split(":")
    return int(h), int(m)


def _project_root(project_root: str | None) -> Path:
    return Path(project_root or Path(__file__).resolve().parents[2])


def build_v10_live_config(*, capital: int, slots: int) -> LiveTradingConfig:
    """자본·슬롯 기반 live_settings 오버라이드."""
    base = load_live_config()
    slot_bet = max(int(capital) // max(int(slots), 1), 1)
    return replace(
        base,
        account=replace(
            base.account,
            bet_amount_per_slot=float(slot_bet),
            max_slots_limit=int(slots),
            min_slots_limit=1,
            minimum_operational_capital=min(float(slot_bet) * 0.2, 100_000.0),
        ),
        strategy=replace(
            base.strategy,
            entry_time="15:20",
            max_hold_days=9999,
            target_profit_ratio=999.0,
            stop_loss_ratio=0.99,
        ),
    )


def scan_large_cap_universe(
    *,
    min_mcap: float,
    min_trade_amt: float,
    markets: tuple[str, ...] = ("KOSPI", "KOSDAQ"),
    top_n: int = 40,
    project_root: str | None = None,
    preset_label: str = "v10",
) -> list[str]:
    """시총·거래대금 필터 대형주 유니버스 스캔 → live_today_universe.json."""
    root = _project_root(project_root)
    prime_project_dotenv_from_root(root)
    cfg = build_v10_live_config(capital=2_000_000, slots=4)
    paths = resolve_live_paths(cfg, str(root))
    out_path = paths["universe_json"]
    meta_path = paths["universe_meta"]

    now_kst = datetime.now(KST)
    anchor = resolve_overnight_scan_anchor(now_kst.date(), reference_now=now_kst)
    target_date = pd.Timestamp(anchor.anchor_date).strftime("%Y%m%d")

    frames: list[pd.DataFrame] = []
    for market in markets:
        try:
            df = stock.get_market_cap_by_ticker(target_date, market=market)
            if df is not None and not df.empty:
                df = df.reset_index()
                df["market"] = market
                frames.append(df)
        except Exception as e:
            logger.warning("시장 %s 스캔 실패: %s", market, e)

    if not frames:
        logger.warning("유니버스 데이터 없음")
        return []

    merged = pd.concat(frames, ignore_index=True)
    rename: dict[str, str] = {str(merged.columns[0]): "code"}
    for col in merged.columns[1:]:
        label = str(col)
        if "시가총" in label:
            rename[col] = "market_cap"
        elif "거래대금" in label:
            rename[col] = "volume_amt"
    merged = merged.rename(columns=rename)
    merged["code"] = merged["code"].astype(str).str.zfill(6)

    filtered = merged[
        (merged["market_cap"] >= min_mcap) & (merged["volume_amt"] >= min_trade_amt)
    ].sort_values("volume_amt", ascending=False)
    top_df = filtered.head(top_n)
    codes = top_df["code"].tolist()

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(codes, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    report_items = []
    for _, row in top_df.iterrows():
        code = str(row["code"]).zfill(6)
        try:
            name = stock.get_market_ticker_name(code)
        except Exception:
            name = code
        report_items.append({
            "rank": len(report_items) + 1,
            "code": code,
            "name": name,
            "market_cap": f"{round(row['market_cap'] / 100_000_000, 1)}억 원",
            "volume_amt": f"{round(row['volume_amt'] / 100_000_000, 1)}억 원",
        })

    meta = {
        "scan_time": now_kst.strftime("%Y-%m-%d %H:%M:%S"),
        "base_date_actual": target_date,
        "preset": preset_label,
        "filters_applied": {
            "min_market_cap": f"{int(min_mcap / 100_000_000)}억 원",
            "min_volume_amt": f"{int(min_trade_amt / 100_000_000)}억 원",
            "markets": list(markets),
        },
        "total_scanned_count": len(codes),
        "scanned_items_report": report_items,
    }
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    logger.info("📁 [%s] 유니버스 %d종 → %s", preset_label, len(codes), out_path)
    return codes


def _load_v10_state(path: str) -> dict[str, dict[str, Any]]:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_v10_state(path: str, state: dict[str, dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def liquidate_all_positions(*, project_root: str | None = None, dry_run: bool | None = None) -> int:
    """보유 종목 전량 시장가 청산 (cash 프리셋)."""
    root = _project_root(project_root)
    prime_project_dotenv_from_root(root)
    engine = LiveTradingEngine(dry_run=dry_run, project_root=str(root))
    positions = _load_positions(engine)
    if not positions:
        logger.info("[⚡ 하락장] 청산할 보유 종목 없음")
        return 0

    today_s = _now_kst().strftime("%Y-%m-%d")
    sold = 0
    remaining = []
    for pos in positions:
        sell_result = engine.gateway.sell_all(
            pos.code,
            pos.qty,
            exit_type="V10_CASH_PRESET",
            dry_run_note="전량 현금화",
        )
        rt = str(sell_result.get("rt_cd", "1") if isinstance(sell_result, dict) else "1")
        if rt == "0" or engine.gateway.dry_run:
            engine._record_exit(
                pos,
                exit_price=pos.entry_price,
                exit_type="V10_CASH_PRESET",
                exit_date=today_s,
                name=engine._resolve_stock_name(pos.code),
            )
            sold += 1
        else:
            remaining.append(pos)
            logger.error("청산 실패 %s — %s", pos.code, sell_result)

    _save_positions(engine, remaining)
    state_path = str(Path(root) / STATE_REL)
    state = _load_v10_state(state_path)
    for pos in positions:
        state.pop(pos.code, None)
    _save_v10_state(state_path, state)
    logger.info("[⚡ 하락장] %d/%d종 전량 청산 완료", sold, len(positions))
    return sold


def evaluate_intraday_stop_loss(*, entry_price: float, low: float) -> tuple[str, float] | None:
    """평단 대비 -4% 장중 손절 (15:20 대기 없이 즉시 탈출)."""
    if not np.isfinite(entry_price) or entry_price <= 0 or not np.isfinite(low):
        return None
    stop_px = entry_price * (1.0 - INTRADAY_STOP_RATIO)
    if low <= stop_px:
        return ("V10_INTRADAY_STOP_4PCT", stop_px)
    return None


class V10LiveTradingEngine(LiveTradingEngine):
    """프리셋별 진입·청산 훅을 주입받는 v10 라이브 엔진."""

    def __init__(self, preset_engine: "V10PresetEngineBase", **kwargs: Any):
        super().__init__(**kwargs)
        self.preset_engine = preset_engine
        self.v10_state_path = str(Path(self.root) / STATE_REL)
        self.entry_blackout = False

    def execute_market_scanner(self) -> list[str]:
        return self.preset_engine.scan_universe()

    def run_entry_scan(self, *, force: bool = False) -> dict[str, Any]:
        from src.live.live_account import LivePosition, SlotLockError
        from src.live.live_engine import (
            _at_or_after,
            fetch_ohlcv_history,
            load_live_universe,
            snapshot_after_local_fill,
        )
        from src.live.live_db import record_entry_ledger, use_json_fallback
        from src.automation.telegram_client import build_entry_message, tg_client

        now = _now_kst()
        entry_hm = self.preset_engine.entry_time
        if self.entry_blackout:
            logger.info("🛡️ [Blackout] cash 장세 — 15:20 신규 매수 스킵")
            return {"executed_count": 0, "rejected_count": 0, "last_rejection_msg": None}
        if not force and not _at_or_after(now, entry_hm):
            logger.info("⏳ 진입 시각 대기 (%s KST)", entry_hm)
            return {"executed_count": 0, "rejected_count": 0, "last_rejection_msg": None}

        codes = load_live_universe(config=self.cfg, project_root=self.root)
        names = self._load_all_names()
        positions = _load_positions(self)
        v10_state = _load_v10_state(self.v10_state_path)
        executed = 0
        rejected = 0
        last_rejection_msg: str | None = None
        slot_budget = float(self.cfg.account.bet_amount_per_slot)
        buy_cost_ratio = float(self.cfg.account.buy_cost_ratio)

        try:
            snap = self.gateway.get_snapshot(positions)
        except SlotLockError as e:
            logger.warning("🛡️ %s", e)
            return {"executed_count": 0, "rejected_count": 0, "last_rejection_msg": None}

        held = {p.code for p in positions}

        def _last_close(hist: pd.DataFrame) -> float:
            return float(
                hist.iloc[-1]["Close"] if "Close" in hist.columns else hist.iloc[-1]["close"]
            )

        def _position_invested(pos: LivePosition) -> float:
            return float(pos.qty) * float(pos.entry_price) * (1.0 + buy_cost_ratio)

        def _execute_buy(
            *,
            c6: str,
            label: str,
            hist: pd.DataFrame,
            amount_krw: float,
            reason: str,
            pos: LivePosition | None,
            state: dict[str, Any] | None,
            is_add: bool,
        ) -> bool:
            nonlocal snap, executed, rejected, last_rejection_msg, positions, v10_state

            last_close = _last_close(hist)
            qty = int(amount_krw // last_close)
            if qty < 1:
                return False

            if is_add and pos is not None:
                if _position_invested(pos) + amount_krw * (1.0 + buy_cost_ratio) > slot_budget * 1.001:
                    logger.info("[추격 스킵] %s — 슬롯 예산 초과", label)
                    return False
            elif not is_add:
                try:
                    self.gateway.check_dynamic_slot_lock(snapshot=snap, local_positions=positions)
                except SlotLockError as sle:
                    logger.warning("🛡️ %s", sle)
                    return False

            tag = "추격" if is_add else "진입"
            logger.info(
                "🔥 [v10 %s] %s @ %s x%d — %s (%.0f원)",
                tag,
                label,
                f"{last_close:,.0f}",
                qty,
                reason,
                amount_krw,
            )
            res = self.send_order_kis(c6, qty, last_close)
            if str(res.get("rt_cd", "1")) != "0":
                rejected += 1
                last_rejection_msg = str(res.get("msg1", ""))
                return False

            spent = qty * last_close * (1.0 + buy_cost_ratio)
            if is_add and pos is not None:
                old_qty = pos.qty
                new_qty = old_qty + qty
                pos.entry_price = (old_qty * pos.entry_price + qty * last_close) / new_qty
                pos.qty = new_qty
                prev_tf = int(state.get("tranches_filled", 1)) if state else 1
                v10_state[c6] = self.preset_engine.on_tranche_fill(
                    state or {},
                    tranches_filled=prev_tf + 1,
                )
            else:
                new_pos = LivePosition(
                    code=c6,
                    qty=qty,
                    entry_price=last_close,
                    entry_date=now.strftime("%Y-%m-%d"),
                    hold_days=0,
                )
                positions.append(new_pos)
                v10_state[c6] = self.preset_engine.init_position_state(hist)
                if not use_json_fallback():
                    record_entry_ledger(self.db_path, new_pos, name=label)

            executed += 1
            _save_positions(self, positions, names=names)
            _save_v10_state(self.v10_state_path, v10_state)
            snap = snapshot_after_local_fill(
                snap, positions, cash_spent=spent, account=self.cfg.account
            )
            tg_client.send_message(
                build_entry_message(code=c6, name=label, entry_price=last_close, quantity=qty)
            )
            return True

        for code in codes:
            c6 = str(code).zfill(6)
            label = self._resolve_stock_name(c6, names)
            if c6 in held:
                continue

            hist = fetch_ohlcv_history(c6)
            if hist.empty:
                continue

            passed, reason, amount_krw = self.preset_engine.resolve_entry_order(hist)
            if not passed:
                logger.info("[탈락] %s — %s", label, reason)
                continue

            if _execute_buy(
                c6=c6,
                label=label,
                hist=hist,
                amount_krw=amount_krw,
                reason=reason,
                pos=None,
                state=None,
                is_add=False,
            ):
                held.add(c6)

        if self.preset_engine.supports_tranche_add():
            for pos in list(positions):
                c6 = pos.code
                state = v10_state.get(c6, {})
                tranches_filled = int(state.get("tranches_filled", 1))
                if tranches_filled >= 3:
                    continue

                hist = fetch_ohlcv_history(c6)
                if hist.empty:
                    continue

                passed, reason, amount_krw = self.preset_engine.resolve_entry_order(
                    hist, state=state
                )
                if not passed:
                    continue

                label = self._resolve_stock_name(c6, names)
                _execute_buy(
                    c6=c6,
                    label=label,
                    hist=hist,
                    amount_krw=amount_krw,
                    reason=reason,
                    pos=pos,
                    state=state,
                    is_add=True,
                )

        _save_v10_state(self.v10_state_path, v10_state)
        return {
            "executed_count": executed,
            "rejected_count": rejected,
            "last_rejection_msg": last_rejection_msg,
        }

    def monitor_market_realtime(self) -> int:
        from src.live.live_engine import _blocks_intraday_exit, fetch_intraday_bar, fetch_ohlcv_history

        positions = _load_positions(self)
        if not positions:
            return 0

        now = _now_kst()
        today_s = now.strftime("%Y-%m-%d")
        names = self._load_all_names()
        v10_state = _load_v10_state(self.v10_state_path)
        remaining = []

        for pos in positions:
            if _blocks_intraday_exit(pos, now, self.preset_engine.entry_time):
                remaining.append(pos)
                continue

            bar = fetch_intraday_bar(pos.code, gateway=self.gateway)
            if bar is None:
                remaining.append(pos)
                continue

            stop_hit = evaluate_intraday_stop_loss(entry_price=pos.entry_price, low=bar["low"])
            if stop_hit is not None:
                exit_type, exit_px = stop_hit
                sell_result = self.gateway.sell_all(
                    pos.code,
                    pos.qty,
                    exit_type=exit_type,
                    dry_run_note=f"@ {exit_px:,.0f}",
                )
                rt = str(sell_result.get("rt_cd", "1") if isinstance(sell_result, dict) else "1")
                if rt != "0" and not self.gateway.dry_run:
                    remaining.append(pos)
                    continue
                self._record_exit(
                    pos,
                    exit_price=exit_px,
                    exit_type=exit_type,
                    exit_date=today_s,
                    name=self._resolve_stock_name(pos.code, names),
                )
                v10_state.pop(pos.code, None)
                logger.info("   장중 손절 %s %s", pos.code, exit_type)
                continue

            hist = fetch_ohlcv_history(pos.code)
            state = v10_state.get(pos.code, {})
            exit_info = self.preset_engine.evaluate_exit(
                ohlcv_df=hist,
                entry_price=pos.entry_price,
                state=state,
                bar=bar,
            )
            if exit_info is None:
                remaining.append(pos)
                continue

            exit_type, exit_px, sell_ratio = exit_info
            sell_qty = pos.qty if sell_ratio >= 1.0 else max(1, int(pos.qty * sell_ratio))
            sell_result = self.gateway.sell_all(
                pos.code,
                sell_qty,
                exit_type=exit_type,
                dry_run_note=f"@ {exit_px:,.0f}",
            )
            rt = str(sell_result.get("rt_cd", "1") if isinstance(sell_result, dict) else "1")
            if rt != "0" and not self.gateway.dry_run:
                remaining.append(pos)
                continue

            if sell_ratio < 1.0 and sell_qty < pos.qty:
                pos.qty -= sell_qty
                v10_state[pos.code] = self.preset_engine.on_partial_exit(state, pos.entry_price)
                remaining.append(pos)
                logger.info("   부분청산 %s %s %d주 잔량 %d", pos.code, exit_type, sell_qty, pos.qty)
            else:
                self._record_exit(
                    pos,
                    exit_price=exit_px,
                    exit_type=exit_type,
                    exit_date=today_s,
                    name=self._resolve_stock_name(pos.code, names),
                )
                v10_state.pop(pos.code, None)
                logger.info("   청산 %s %s", pos.code, exit_type)

        _save_positions(self, remaining, names=names)
        _save_v10_state(self.v10_state_path, v10_state)
        return len(remaining)


class V10MasterRunner(LiveMasterRunner):
    """v10.1 프리셋 마스터 — 15:15 장세 자동 판정 후 스캔·15:20 진입."""

    def __init__(
        self,
        engine: V10LiveTradingEngine,
        *,
        capital: int,
        slots: int,
        dry_run: bool | None,
        preset_override: str | None = None,
    ):
        super().__init__(engine)
        self._capital = int(capital)
        self._slots = int(slots)
        self._dry_run = dry_run
        self._preset_override = preset_override
        self._regime: str | None = preset_override
        self._entry_blackout = preset_override == "cash"
        from src.engine.capital_buffer_manager import load_capital_buffer

        self._capital_buffer = load_capital_buffer(
            project_root=self.engine.root,
            target_capital=float(capital),
        )

    def _swap_preset_engine(self, regime: str) -> None:
        from src.engine.fib_swing_strategy import FibSwingEngine
        from src.engine.high_tight_flag_strategy import MomentumEngine

        common = dict(
            capital=self._capital,
            slots=self._slots,
            project_root=self.engine.root,
            dry_run=self._dry_run,
            preset_override=self._preset_override,
        )
        if regime == "momentum":
            pe = MomentumEngine(**common)
        else:
            pe = FibSwingEngine(**common)
        self.engine.preset_engine = pe

    def _run_regime_and_screener_routine(self) -> None:
        if self._already_ran("regime_date"):
            return

        from src.engine.market_classifier import check_market_regime, describe_regime

        if self._preset_override:
            regime = self._preset_override
            logger.info("📊 [v10.1] 수동 프리셋 고정: %s", regime.upper())
        else:
            regime = check_market_regime(gateway=self.engine.gateway)

        self._regime = regime
        self._mark_ran("regime_date")
        logger.info("   → %s", describe_regime(regime))

        if regime == "cash":
            self._entry_blackout = True
            self.engine.entry_blackout = True
            self._mark_ran("screener_date")
            logger.info("🛡️ [Blackout] 신규 매수·유니버스 스캔 스킵 — 현금 대기")
            return

        self._entry_blackout = False
        self.engine.entry_blackout = False
        self._swap_preset_engine(regime)

        if self._already_ran("screener_date"):
            return
        logger.info("🔔 [ROUTINE 1] %s 유니버스 스캔 (%s)", REGIME_CHECK_TIME, regime.upper())
        codes = self.engine.execute_market_scanner()
        self._mark_ran("screener_date")
        logger.info("   스캔 완료 — %d종", len(codes))

    def _run_screener_routine(self) -> None:
        self._run_regime_and_screener_routine()

    def _run_entry_routine(self) -> None:
        if self._entry_blackout:
            if not self._already_ran("entry_date"):
                logger.info("⏭️ [Blackout] cash 장세 — 15:20 신규 매수 스킵")
                self._mark_ran("entry_date")
            return
        super()._run_entry_routine()

    def _run_close_routine(self) -> None:
        super()._run_close_routine()
        if self._already_ran("capital_buffer_date"):
            return
        from src.engine.capital_buffer_manager import save_capital_buffer
        from src.live.live_engine import _load_positions

        positions = _load_positions(self.engine)
        try:
            balances = self.engine.gateway.get_inquire_balance(positions)
            total_asset = float(balances.get("total_asset") or 0)
        except Exception as exc:
            logger.warning("⚠️ [v10.2] Safe Vault 정산 생략(잔고 조회 실패): %s", exc)
            return
        if total_asset <= 0:
            return
        self._capital_buffer.rebalance(total_asset)
        save_capital_buffer(self._capital_buffer, project_root=self.engine.root)
        self._mark_ran("capital_buffer_date")
        s = self._capital_buffer.summary()
        logger.info(
            "🏦 [v10.2 Safe Vault] 금고 %s원 · 수확 %d · 전량수혈 %d · 부분수혈 %d",
            f"{s['safe_vault']:,.0f}",
            s["harvest_count"],
            s["refill_full_count"],
            s["refill_partial_count"],
        )

    def run_forever(self) -> None:
        pe = self.engine.preset_engine
        logger.info(
            "🚀 [v10.1 마스터] override=%s · 자금=%s · 슬롯=%d · dry_run=%s",
            (self._preset_override or "AUTO").upper(),
            f"{self.engine.cfg.account.bet_amount_per_slot * self.engine.cfg.account.max_slots_limit:,.0f}",
            self.engine.cfg.account.max_slots_limit,
            self.engine.gateway.dry_run,
        )
        if self._preset_override and self._preset_override != "cash":
            logger.info("   프리셋=%s (수동)", pe.preset.upper())
        else:
            logger.info("   15:15 KOSPI/KOSDAQ Fact 기반 자동 장세 판정")
        super().run_forever()


@dataclass
class V10PresetEngineBase(ABC):
    """Momentum / Swing 프리셋 엔진 공통 베이스."""

    capital: int = 2_000_000
    slots: int = 4
    project_root: str | None = None
    dry_run: bool | None = None
    preset: str = "base"
    entry_time: str = "15:20"
    preset_override: str | None = None

    def _build_live_engine(self) -> V10LiveTradingEngine:
        root = _project_root(self.project_root)
        prime_project_dotenv_from_root(root)
        cfg = build_v10_live_config(capital=self.capital, slots=self.slots)
        return V10LiveTradingEngine(
            self,
            config=cfg,
            dry_run=self.dry_run,
            project_root=str(root),
        )

    @abstractmethod
    def entry_signal(self, ohlcv_df: pd.DataFrame) -> tuple[bool, str]:
        ...

    def supports_tranche_add(self) -> bool:
        return False

    def resolve_entry_order(
        self,
        ohlcv_df: pd.DataFrame,
        *,
        state: dict[str, Any] | None = None,
    ) -> tuple[bool, str, float]:
        ok, msg = self.entry_signal(ohlcv_df)
        if not ok:
            return False, msg, 0.0
        slot = max(int(self.capital) // max(int(self.slots), 1), 1)
        return True, msg, float(slot)

    def on_tranche_fill(self, state: dict[str, Any], *, tranches_filled: int) -> dict[str, Any]:
        return state

    @abstractmethod
    def scan_universe(self) -> list[str]:
        ...

    @abstractmethod
    def evaluate_exit(
        self,
        *,
        ohlcv_df: pd.DataFrame,
        entry_price: float,
        state: dict[str, Any],
        bar: dict[str, float],
    ) -> tuple[str, float, float] | None:
        ...

    def init_position_state(self, ohlcv_df: pd.DataFrame) -> dict[str, Any]:
        return {}

    def on_partial_exit(self, state: dict[str, Any], entry_price: float) -> dict[str, Any]:
        return state

    def run_master_loop(self) -> None:
        engine = self._build_live_engine()
        runner = V10MasterRunner(
            engine,
            capital=self.capital,
            slots=self.slots,
            dry_run=self.dry_run,
            preset_override=self.preset_override,
        )
        runner.run_forever()

    @abstractmethod
    def run_1520_routine(self) -> None:
        ...
