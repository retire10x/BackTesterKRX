"""KIS 당일분봉 API 연동 테스트 (네이버 크롤링 미사용)."""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dataclasses import replace

from src.live.live_account import LiveAccountGateway
from src.live.live_config import load_live_config

KST = ZoneInfo("Asia/Seoul")


def fetch_latest_close(symbol: str = "005930") -> float | None:
    """KIS 당일분봉 최신 종가. 실패 시 None (하드코딩 없음)."""
    cfg = load_live_config()
    account = replace(cfg.account, mode="paper")
    gw = LiveAccountGateway(account, dry_run=os.getenv("LIVE_DRY_RUN", "1") == "1")
    bars = gw.fetch_today_minute_bars(symbol, end_dt=datetime.now(KST))
    if not bars:
        return None
    return float(bars[-1].close)


if __name__ == "__main__":
    px = fetch_latest_close("005930")
    if px is None:
        print("[WARN] KIS 분봉 데이터 없음 (장외 또는 dry_run)")
    else:
        print(f"005930 latest close: {px:,.0f}")
