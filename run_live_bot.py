"""
v5.5.2 코스닥 스나이퍼 라이브 매매 봇.

  python run_live_bot.py screener          # 15:15 유니버스 스캔
  python run_live_bot.py entry             # 15:20 변곡 진입
  python run_live_bot.py watch             # 장중 청산 감시
  python run_live_bot.py daily --dry-run   # screener→entry (시각 무시·시뮬)
  python run_live_bot.py daily             # 장 운영 일과 (시각 대기)

.env: KIS_APP_KEY, KIS_APP_SECRET, KIS_CANO, KIS_ACNT_PRDT_CD
      LIVE_DRY_RUN=1  (API 없이 로그만)
"""
from __future__ import annotations

import argparse
import os
import sys

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


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

from src.live.live_engine import LiveTradingEngine  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="v5.5.2 코스닥 스나이퍼 라이브 봇")
    p.add_argument(
        "command",
        choices=("screener", "entry", "watch", "daily"),
        help="screener | entry | watch | daily",
    )
    p.add_argument("--dry-run", action="store_true", help="KIS 주문 없이 로그만")
    p.add_argument("--force", action="store_true", help="시각 조건 무시 즉시 실행")
    p.add_argument("--once", action="store_true", help="watch 1회만")
    args = p.parse_args()

    engine = LiveTradingEngine(dry_run=args.dry_run)
    print(f"🤖 v5.5.2 라이브 봇 · {args.command} · dry_run={engine.gateway.dry_run}")

    if args.command == "screener":
        engine.run_screener_if_due(force=True)
    elif args.command == "entry":
        engine.run_screener_if_due(force=args.force)
        engine.run_entry_scan(force=args.force)
    elif args.command == "watch":
        engine.run_watch_loop(once=args.once)
    elif args.command == "daily":
        engine.run_screener_if_due(force=args.force)
        engine.run_entry_scan(force=args.force)
        if args.force:
            engine.run_watch_loop(once=True)


if __name__ == "__main__":
    main()
