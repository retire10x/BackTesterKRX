"""
v10.1 마스터 — 15:15 지수 Fact 기반 자동 장세 판정 (인간 --preset 불필요).

  # 자동 장세 (권장)
  python run_v10_master.py --capital 2000000

  # 수동 프리셋 고정 (v10.0 호환)
  python run_v10_master.py --preset swing --capital 2000000

  # 긴급 전량 현금화
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


def _build_engine(regime: str, *, capital: int, slots: int, dry_run: bool | None, preset_override: str | None):
    from src.engine.fib_swing_strategy import FibSwingEngine
    from src.engine.high_tight_flag_strategy import MomentumEngine

    common = dict(
        capital=capital,
        slots=slots,
        project_root=project_root,
        dry_run=dry_run,
        preset_override=preset_override,
    )
    if regime == "momentum":
        return MomentumEngine(**common)
    return FibSwingEngine(**common)


def main(argv: list[str] | None = None) -> int:
    from src.engine.market_classifier import check_market_regime, describe_regime
    from src.engine.v10_live_core import liquidate_all_positions

    parser = argparse.ArgumentParser(description="v10.1 자동 장세 마스터")
    parser.add_argument(
        "--preset",
        choices=["momentum", "swing", "cash"],
        default=None,
        help="수동 고정 (미지정 시 15:15 지수 Fact 자동 판정)",
    )
    parser.add_argument("--capital", type=int, default=2_000_000, help="운용 자금 (원)")
    parser.add_argument("--slots", type=int, default=4, help="최대 슬롯 수")
    parser.add_argument("--dry-run", action="store_true", help="KIS 주문 없이 시뮬")
    parser.add_argument("--once", action="store_true", help="스캔+진입 1회만 (루프 미가동)")
    args = parser.parse_args(argv)

    dry = True if args.dry_run else None

    if args.preset == "cash":
        print("[⚡ 하락장 긴급 명령] 보유 종목 전량 시장가 청산 및 자금 보호 모드 돌입.")
        liquidate_all_positions(project_root=project_root, dry_run=dry)
        return 0

    if args.preset:
        regime = args.preset
        print(f"=== [v10.1 마스터] 수동 프리셋: {regime.upper()} ===")
    else:
        regime = check_market_regime()
        print(f"=== [v10.1 마스터] 자동 장세 판정: {regime.upper()} ===")
        print(f"    {describe_regime(regime)}")

    if regime == "cash":
        print("[🛡️ Blackout] 하락/위험장 — 오늘 종가 신규 매수 전면 차단 (기존 보유는 장중 -4% 손절 감시).")
        if args.once:
            return 0
        engine = _build_engine("swing", capital=args.capital, slots=args.slots, dry_run=dry, preset_override=None)
        live = engine._build_live_engine()
        live.entry_blackout = True
        from src.engine.v10_live_core import V10MasterRunner

        runner = V10MasterRunner(
            live,
            capital=args.capital,
            slots=args.slots,
            dry_run=dry,
            preset_override=None,
        )
        runner._regime = "cash"
        runner._entry_blackout = True
        runner.run_forever()
        return 0

    if regime == "momentum":
        print("[🚀 상승장 모멘텀] 52주 신고가 대형주 정추세 눌림목 감시...")
    else:
        print("[🛡️ 횡보장 스윙] 우량 대형주 피보나치 바닥 낚시...")

    engine = _build_engine(
        regime,
        capital=args.capital,
        slots=args.slots,
        dry_run=dry,
        preset_override=args.preset,
    )

    if args.once:
        live = engine._build_live_engine()
        live.execute_market_scanner()
        live.calculate_entry_signals()
        return 0

    engine.run_1520_routine()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
