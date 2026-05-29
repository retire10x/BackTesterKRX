#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v3.13: 비교 검증 진입점. 실제 검증 로직은 `src.overnight_parity.run_overnight_parity_check`."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def main() -> int:
    from src.data_loader import default_backtest_period_range, load_config
    from src.overnight_parity import prime_project_dotenv_from_root, run_overnight_parity_check

    prime_project_dotenv_from_root(ROOT)

    parser = argparse.ArgumentParser(description="v3.13 CLI/GUI parity (same universe_limit + anchor).")
    parser.add_argument("--end", type=str, default="", help="YYYY-MM-DD")
    parser.add_argument("--market", type=str, default="", help="KOSPI|KOSDAQ")
    args = parser.parse_args()

    cfg = load_config()
    uni = cfg.get("universe") or {}
    market = str(args.market.strip() or uni.get("market") or "KOSPI").strip().upper()
    if market not in ("KOSPI", "KOSDAQ"):
        market = "KOSPI"
    v3_cfg = cfg.get("v3_0") or {}
    limit = max(20, min(300, int(v3_cfg.get("universe_limit", 300))))

    if str(args.end or "").strip():
        end_eff = str(args.end).strip()[:10]
    else:
        period = cfg.get("period") or {}
        end_eff = str(period.get("end_date") or "").strip()[:10]
        if not end_eff:
            _ds, ed = default_backtest_period_range()
            end_eff = ed.strftime("%Y-%m-%d")

    code, lines = run_overnight_parity_check(requested_end=end_eff, market=market, universe_limit=limit)
    print("\n".join(lines))
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
