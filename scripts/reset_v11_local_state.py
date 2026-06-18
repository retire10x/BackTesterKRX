"""
v11 로컬 가상 계좌·거래 DB 초기화.

  python scripts/reset_v11_local_state.py

KIS 모의투자 이관 전 로컬 PaperTradingBroker 상태를 삭제한다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    ROOT / "config" / "v11_paper_broker.json",
    ROOT / "config" / "v11_kis_position_meta.json",
    ROOT / "data" / "live_trading.db",
    ROOT / "data" / "live_trading.db-wal",
    ROOT / "data" / "live_trading.db-shm",
]

def main() -> int:
    deleted = 0
    for path in TARGETS:
        if path.exists():
            path.unlink()
            print(f"Deleted: {path.relative_to(ROOT)}")
            deleted += 1
        else:
            print(f"Skip (not found): {path.relative_to(ROOT)}")

    from src.live.live_db_manager import init_v11_schema
    init_v11_schema(project_root=ROOT)
    print("[OK] live_equity · live_trades schema created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
