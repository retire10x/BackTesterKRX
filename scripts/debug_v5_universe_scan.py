"""일회성: lock_date OHLCV·필터 진단."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

env_path = os.path.join(ROOT, ".env")
if os.path.isfile(env_path):
    for raw in open(env_path, encoding="utf-8"):
        s = str(raw).strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        key = k.strip()
        val = v.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val

import pandas as pd

from src.v5_universe import (
    _fetch_lock_day_ohlcv_frame,
    _fetch_pykrx_listed_shares_by_code,
    _resolve_lock_trading_day,
)

LOCK = "2022-12-30"
raw = _fetch_lock_day_ohlcv_frame(LOCK)
print("ohlcv rows", len(raw), "cols", list(raw.columns) if not raw.empty else [])
if not raw.empty:
    print(raw.head(2))
    for col in ("Close", "Volume", "Amount"):
        if col in raw.columns:
            s = pd.to_numeric(raw[col], errors="coerce")
            print(col, "max", s.max(), "nz", (s > 0).sum())

actual, snap, amt = _resolve_lock_trading_day(LOCK)
print("amount map", len(amt))
print("snap rows", len(snap), "close nz", (pd.to_numeric(snap["close"]) > 0).sum())

sh = _fetch_pykrx_listed_shares_by_code(LOCK)
print("shares", len(sh))

try:
    import FinanceDataReader as fdr

    f = fdr.DataReader("035720", "2022-12-27", "2022-12-30")
    print("FDR 035720 tail:\n", f.tail())
except Exception as e:
    print("FDR err", e)

from src.v5_config import load_v5_config
from src.v5_universe import _rank_codes_at_lock

lock = load_v5_config().environment.universe_lock
codes, meta, _ = _rank_codes_at_lock(lock)
print("SCAN OK:", len(codes), meta["source"], meta["total_scanned_count"])
print("top1:", meta["scanned_items_report"][0])
