#!/usr/bin/env python3
"""entry_date 보정 + 익절 조건 충족 종목 즉시 청산 (1회성 운영 스크립트)."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.live.live_db import (
    default_db_path,
    init_schema,
    load_holding_positions,
    patch_holding_entry,
    record_entry_ledger,
)
from src.live.live_engine import LiveTradingEngine

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("FixEntryExit")

# 6/11 15:20 체결 — KIS sync 오류로 entry_date가 6/12로 리셋된 종목
CORRECTIONS: dict[str, str] = {
    "126730": "2026-06-11",
    "456010": "2026-06-11",
}


def main() -> int:
    db_path = default_db_path(ROOT)
    init_schema(db_path, project_root=ROOT)

    for symbol, entry_date in CORRECTIONS.items():
        ok = patch_holding_entry(db_path, symbol, entry_date=entry_date, hold_days=1)
        if ok:
            logger.info("✅ entry_date 보정: %s → %s (hold_days=1)", symbol, entry_date)
        else:
            logger.warning("⚠️ 보유 없음 — 스킵: %s", symbol)

    # 삼지전자 등 기존 보유도 ledger 백필
    for pos in load_holding_positions(db_path):
        record_entry_ledger(db_path, pos)

    engine = LiveTradingEngine(project_root=str(ROOT))
    logger.info("👁️ 익절 감시 1틱 실행 (KIS 실시간 시세)...")
    remaining = engine.monitor_market_realtime()
    logger.info("📊 잔여 포지션: %d종", remaining)
    engine.print_positions_snapshot()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
