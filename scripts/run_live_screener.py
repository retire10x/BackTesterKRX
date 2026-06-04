"""
라이브 유니버스 스캐너 — 수동 실행용 (자동 스케줄 없음).

  python scripts/run_live_screener.py
  python scripts/run_live_screener.py --date 20250603
  python scripts/run_live_screener.py --show
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.overnight_parity import prime_project_dotenv_from_root  # noqa: E402


def main() -> None:
    prime_project_dotenv_from_root(Path(ROOT))
    p = argparse.ArgumentParser(description="v5.5.2 라이브 코스닥 스크리너 (수동)")
    p.add_argument("--date", help="기준일 YYYYMMDD (미지정=오늘 KST)")
    p.add_argument("--show", action="store_true", help="스캔 없이 기존 JSON/meta만 출력")
    args = p.parse_args()

    from src.live.live_config import load_live_config, resolve_live_paths
    from src.live.live_screener import LiveScreener, load_live_universe

    cfg = load_live_config()
    paths = resolve_live_paths(cfg, ROOT)

    if args.show:
        codes = load_live_universe(config=cfg, project_root=ROOT)
        print(f"📂 {paths['universe_json']} — {len(codes)}종")
        for i, c in enumerate(codes[:10], 1):
            print(f"   {i:2d}. {c}")
        if len(codes) > 10:
            print(f"   ... 외 {len(codes) - 10}종")
        meta_path = paths["universe_meta"]
        if os.path.isfile(meta_path):
            with open(meta_path, encoding="utf-8") as fh:
                meta = json.load(fh)
            print(f"\n📋 meta: scan_time={meta.get('scan_time')} base={meta.get('base_date_actual')}")
            for item in (meta.get("scanned_items_report") or [])[:5]:
                print(
                    f"   #{item.get('rank')} {item.get('code')} {item.get('name')} "
                    f"시총 {item.get('market_cap')} 거래대금 {item.get('volume_amt')}"
                )
        return

    scanner = LiveScreener(cfg, project_root=ROOT)
    codes = scanner.execute_daily_scan(force_date=args.date)
    print(f"\n✅ 완료 — {len(codes)}종 → {paths['universe_json']}")
    if not codes:
        print("   (0종) 장 마감 후 --date 로 최근 영업일을 지정해 다시 시도하세요.")


if __name__ == "__main__":
    main()
