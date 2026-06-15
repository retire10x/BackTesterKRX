"""
v8.0.0 ORB 검증 러너 (스켈레톤).

분봉 백테스트는 데이터량·슬리피지 한계로 별도 인프라 구축 후 활성화.
현재: orb_strategy 순수 로직 smoke test + 설계 리포트 출력.

실행:
  python run_v8_00_orb_research.py
"""
from __future__ import annotations

import os
import sys

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from src.engine.orb_strategy import (
    OrbConfig,
    OrbExitReason,
    compute_opening_high,
    evaluate_orb_exit,
    gap_ratio,
    passes_premarket_universe,
    should_enter_breakout,
)

KST = ZoneInfo("Asia/Seoul")
OUT_DIR = os.path.join(project_root, "outputs")
REPORT_MD = os.path.join(OUT_DIR, "v8_00_orb_design_report.md")


def _smoke_test() -> list[str]:
    lines: list[str] = []
    cfg = OrbConfig()

    ok = passes_premarket_universe(
        prior_trading_value_krw=60_000_000_000,
        today_open=10_500,
        prior_close=10_000,
        cfg=cfg,
    )
    lines.append(f"- premarket 500억+ & 갭+5%: {'PASS' if ok else 'FAIL'}")

    bad_gap = passes_premarket_universe(
        prior_trading_value_krw=60_000_000_000,
        today_open=10_900,
        prior_close=10_000,
        cfg=cfg,
    )
    lines.append(f"- premarket 갭+9% 제외: {'PASS' if not bad_gap else 'FAIL'}")

    idx = pd.date_range("2026-06-16 09:00", periods=5, freq="1min", tz=KST)
    bars = pd.DataFrame(
        {"open": [100, 101, 102, 103, 104], "high": [101, 103, 105, 106, 107], "low": [99] * 5, "close": [100.5] * 5},
        index=idx,
    )
    oh = compute_opening_high(bars, cfg=cfg)
    lines.append(f"- opening high (첫 5분): {oh} (expect 107)")

    now = datetime(2026, 6, 16, 9, 10, tzinfo=KST)
    br = should_enter_breakout(current_price=108, opening_high=107, now=now, cfg=cfg)
    lines.append(f"- breakout 09:10 @108>107: {'PASS' if br else 'FAIL'}")

    late = datetime(2026, 6, 16, 9, 35, tzinfo=KST)
    late_br = should_enter_breakout(current_price=108, opening_high=107, now=late, cfg=cfg)
    lines.append(f"- breakout 09:35 차단: {'PASS' if not late_br else 'FAIL'}")

    tp = evaluate_orb_exit(entry_price=100, current_price=106, now=now, cfg=cfg)
    lines.append(f"- exit +6% -> {tp} (expect TAKE_PROFIT)")

    sl = evaluate_orb_exit(entry_price=100, current_price=97, now=now, cfg=cfg)
    lines.append(f"- exit -3% -> {sl} (expect STOP_LOSS)")

    ts = datetime(2026, 6, 16, 14, 55, tzinfo=KST)
    time_stop = evaluate_orb_exit(entry_price=100, current_price=101, now=ts, cfg=cfg)
    lines.append(f"- exit 14:55 -> {time_stop} (expect TIME_STOP)")

    gr = gap_ratio(10_300, 10_000)
    lines.append(f"- gap_ratio +3%: {gr:.2%}")

    return lines


def main() -> int:
    print("--- v8.0.0 ORB 설계 검증 (분봉 백테스트 인프라 대기) ---", flush=True)
    print("  전술: 08:55 프리마켓 · 09:05 Opening High · 09:30까지 돌파 진입", flush=True)
    print("  청산: +5% / -2% / 14:50 당일 강제청산", flush=True)
    print("  스케줄: 08:50 기동 · 15:00 마스터 종료", flush=True)
    print("", flush=True)

    results = _smoke_test()
    for line in results:
        print(line, flush=True)

    os.makedirs(OUT_DIR, exist_ok=True)
    report = "\n".join([
        "# v8.0.0 ORB 설계 검증 리포트",
        "",
        "## Smoke test",
        "",
        *results,
        "",
        "## 백테스트 한계 고지",
        "",
        "- 분봉 단위 백테스트는 일봉 대비 데이터량이 수백 배이며 틱 슬리피지 재현이 어렵습니다.",
        "- `PortfolioManagerV800.run()`은 분봉 파이프라인 구축 후 활성화 예정입니다.",
        "- 실전 검증은 `src/live/live_master_v800.py` + `config/live_settings_v800.yaml` 경로를 사용하세요.",
        "",
        f"- 설계도: `docs/v8_00_orb_design.md`",
        "",
    ])
    with open(REPORT_MD, "w", encoding="utf-8") as fh:
        fh.write(report + "\n")
    print(f"\n리포트: {REPORT_MD}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
