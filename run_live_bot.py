"""
v5.5.2 코스닥 스나이퍼 — 완전 자동 마스터 봇.

  python run_live_bot.py              # 종일 자동 (15:15 스캔 · 15:20 진입 · 장중 감시)
  python run_live_bot.py master       # 위와 동일
  python run_live_bot.py screener     # 스캔만 (수동·디버그)
  python run_live_bot.py entry        # 진입만
  python run_live_bot.py watch        # 감시만
  python run_live_bot.py daily --force --dry-run  # 일괄 시뮬

.env: KRX_ID/KRX_PW · KIS_* · LIVE_DRY_RUN=1 (실주문 전 필수)
운영 SOP: docs/live_SOP_guide.md
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


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
from src.live.live_master import run_master  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="v5.5.2 코스닥 스나이퍼 라이브 봇")
    p.add_argument(
        "command",
        nargs="?",
        default="master",
        choices=("master", "screener", "entry", "watch", "daily"),
        help="기본=master (완전 자동)",
    )
    p.add_argument("--dry-run", action="store_true", help="KIS 주문 없이 로그만")
    p.add_argument("--force", action="store_true", help="시각 조건 무시 즉시 실행")
    p.add_argument("--once", action="store_true", help="watch 1회만")
    args = p.parse_args()

    if args.command == "master":
        dry = True if args.dry_run else None
        run_master(dry_run=dry, project_root=project_root)
        return

    engine = LiveTradingEngine(dry_run=args.dry_run if args.dry_run else None, project_root=project_root)
    print(f"🤖 v5.5.2 라이브 봇 · {args.command} · dry_run={engine.gateway.dry_run}")

    if args.command == "screener":
        engine.run_screener_if_due(force=True)  # SOP 15:10 선제 스캔 (--force 호환)
    elif args.command == "entry":
        # SOP: 15:10 선제 스캔 후 저장된 유니버스만 사용 (entry 시 재스캔 금지)
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
