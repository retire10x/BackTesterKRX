"""
v10.0 마스터 — 사용자 프리셋 스위칭 (지수 연산·인터록 없음).

  # 상승장: 52주 신고가 대형주 모멘텀
  python run_v10_master.py --preset momentum --capital 2000000

  # 횡보장: KOSPI200/KOSDAQ150 피보나치 스윙
  python run_v10_master.py --preset swing --capital 2000000

  # 하락장: 전량 현금화 후 종료
  python run_v10_master.py --preset cash

옵션:
  --dry-run   KIS 주문 없이 로그만
  --once      15:20 1회 진입·스캔만 (마스터 루프 미가동)
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


def _setup_logging() -> None:
    log_dir = os.path.join(project_root, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "live_bot.log")
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)


_setup_logging()


def main(argv: list[str] | None = None) -> int:
    from src.engine.fib_swing_strategy import FibSwingEngine
    from src.engine.high_tight_flag_strategy import MomentumEngine
    from src.engine.v10_live_core import liquidate_all_positions

    parser = argparse.ArgumentParser(description="v10.0 프리셋 마스터")
    parser.add_argument(
        "--preset",
        choices=["momentum", "swing", "cash"],
        required=True,
        help="momentum=상승장 | swing=횡보장 | cash=하락장 전량청산",
    )
    parser.add_argument("--capital", type=int, default=2_000_000, help="운용 자금 (원)")
    parser.add_argument("--slots", type=int, default=4, help="최대 슬롯 수")
    parser.add_argument("--dry-run", action="store_true", help="KIS 주문 없이 시뮬")
    parser.add_argument("--once", action="store_true", help="스캔+진입 1회만 (루프 미가동)")
    args = parser.parse_args(argv)

    print(f"=== [v10.0 마스터] 사용자가 선택한 프리셋: {args.preset.upper()} ===")

    dry = True if args.dry_run else None

    if args.preset == "cash":
        print("[⚡ 하락장 긴급 명령] 보유 종목 전량 시장가 청산 및 자금 보호 모드 돌입.")
        liquidate_all_positions(project_root=project_root, dry_run=dry)
        return 0

    common = dict(
        capital=args.capital,
        slots=args.slots,
        project_root=project_root,
        dry_run=dry,
    )

    if args.preset == "momentum":
        print("[🚀 상승장 모멘텀 가동] 52주 신고가 대형주 정추세 눌림목 감시 시작...")
        engine = MomentumEngine(**common)
    else:
        print("[🛡️ 횡보장 스윙 가동] 우량 대형주 피보나치 바닥 낚시질 시작...")
        engine = FibSwingEngine(**common)

    if args.once:
        live = engine._build_live_engine()
        live.execute_market_scanner()
        live.calculate_entry_signals()
        return 0

    engine.run_1520_routine()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
