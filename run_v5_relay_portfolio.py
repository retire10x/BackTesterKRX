"""
v5.3 릴레이(6개월) 동적 유니버스 백테스트.

7구간 독립 시뮬 → 자산 이월 · 구간 말 PERIOD_RESET · 마스터 equity/trades 병합.
전략 설계 단계: 스캔·실행 전 Y/N 확인 ( --yes 만 무질문).
"""
from __future__ import annotations

import argparse
import os
import sys

import pandas as pd

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _load_env_file(path: str) -> None:
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except Exception:
        return
    for raw in lines:
        s = str(raw).strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        key = k.strip()
        val = v.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        if key and key not in os.environ:
            os.environ[key] = val


_load_env_file(os.path.join(project_root, ".env"))

from run_v4_portfolio import START_DATE, END_DATE, validate_phase_a_trades  # noqa: E402
from src.engine.portfolio_manager import (  # noqa: E402
    PortfolioManager as _PortfolioManagerV4,
    load_merged_market_day_frames,
)
from src.engine.portfolio_manager_v5 import PortfolioManagerV5  # noqa: E402
from src.v5_config import DEFAULT_V5_RELAY_SECTION, load_v5_relay_config  # noqa: E402
from src.v5_relay_screener import (  # noqa: E402
    RELAY_BACKTEST_END,
    RELAY_BACKTEST_START,
    RELAY_PHASES,
    load_relay_universe_codes,
    scan_all_relay_universes,
    _resolve_universe_dir,
)

EQUITY_CSV = os.path.join(project_root, "outputs", "v5_relay_equity_curve.csv")
TRADES_CSV = os.path.join(project_root, "outputs", "v5_relay_trades.csv")
PASS_LOG_TXT = os.path.join(project_root, "outputs", "v5_relay_pass_log.txt")


def _prompt_yes_no(question: str, *, default: str | None = None) -> bool:
    suffix = " [Y/n]" if default in (None, "y", "Y") else " [y/N]" if default in ("n", "N") else " [y/n]"
    while True:
        try:
            raw = input(f"{question}{suffix}: ").strip()
        except EOFError:
            return False
        if not raw and default is not None:
            raw = default
        low = raw.lower()
        if low in ("y", "yes", "예", "ㅇ"):
            return True
        if low in ("n", "no", "아니오", "ㄴ"):
            return False
        print("  → y 또는 n 으로 답해 주세요.")


def _entry_plan_line(strat) -> str:
    base = (
        f"MA{strat.lookback_window} 변곡 (어제≤MA · 오늘>20영업일전종가)"
    )
    mf = strat.macro_trend_filter
    if mf is None or not mf.enabled:
        return base
    parts = [base]
    if mf.uses_dual_slope:
        slopes = " AND ".join(f"MA{w}↑" for w in mf.dual_slope_alignment)
        parts.append(slopes)
        if mf.check_prices_above_ma:
            parts.append(f"종가>MA{mf.check_prices_above_ma}")
    elif mf.ma_window:
        parts.append(f"종가>MA{mf.ma_window}")
    return " AND ".join(parts)


def _print_relay_plan(v5, universe_dir: str) -> None:
    strat = v5.strategy
    env = v5.environment
    port = v5.portfolio
    sc = v5.screener
    print(f"\n--- v5 릴레이 백테스트 계획 ({v5.section}) ---")
    print(f"  전략     : {strat.strategy_name}")
    print(f"  기간     : {RELAY_BACKTEST_START} ~ {RELAY_BACKTEST_END} ({len(RELAY_PHASES)}구간)")
    print(f"  자본     : {env.initial_cash:,.0f}원 시작 · 구간 종료 시 이월")
    print(f"  슬롯     : {port.max_slots} × {port.slot_invest_amount:,.0f}원")
    print(f"  진입     : {_entry_plan_line(strat)}")
    print(
        f"  청산     : 익절 +{strat.target_profit_ratio:.0%} · "
        f"손절 -{strat.stop_loss_ratio:.0%} · {strat.max_hold_days}일 · 구간말 PERIOD_RESET"
    )
    if sc:
        print(
            f"  스크리너 : {sc.market} 시총 {sc.min_mcap_krw/1e8:.0f}~{sc.max_mcap_krw/1e8:.0f}억 · "
            f"거래대금≥{sc.min_trade_krw/1e8:.0f}억 · Top{sc.top_n}"
        )
    print(f"  유니버스 : {universe_dir}")
    for ph in RELAY_PHASES:
        print(
            f"    [{ph.phase_id}] {ph.segment_start}~{ph.segment_end} "
            f"← 락 {ph.lock_date_nominal} ({ph.json_basename})"
        )
    print("-------------------------------------------\n")


def _all_universe_files_exist(universe_dir: str) -> bool:
    for ph in RELAY_PHASES:
        if not os.path.isfile(os.path.join(universe_dir, ph.json_basename)):
            return False
    return True


def run_v5_relay_backtest(
    *,
    section: str = DEFAULT_V5_RELAY_SECTION,
    scan_universes: bool | None = None,
    skip_prompts: bool = False,
) -> None:
    v5 = load_v5_relay_config(section=section)
    universe_dir = _resolve_universe_dir(v5, project_root)
    os.makedirs(universe_dir, exist_ok=True)

    if scan_universes is None and not skip_prompts:
        scan_universes = _prompt_yes_no(
            f"7구간 릴레이 유니버스를 lock_date 기준으로 일괄 스캔할까요? ({universe_dir})",
            default="n",
        )
    if scan_universes:
        scan_all_relay_universes(v5=v5, project_root=project_root)
    elif not _all_universe_files_exist(universe_dir):
        raise FileNotFoundError(
            f"릴레이 유니버스 JSON이 없습니다: {universe_dir}\n"
            "먼저 스캔하세요: python run_v5_relay_portfolio.py --scan-universes"
        )

    _print_relay_plan(v5, universe_dir)

    if not skip_prompts:
        if not _prompt_yes_no("위 설정으로 릴레이 백테스트를 실행할까요?", default="n"):
            print("백테스트를 취소했습니다.")
            return

    bulk_start = min(RELAY_BACKTEST_START, START_DATE)
    bulk_end = max(RELAY_BACKTEST_END, END_DATE)
    print(f"🚀 v5.3 릴레이 벌크 로딩 ({bulk_start} ~ {bulk_end})…")
    day_frames, bdays = load_merged_market_day_frames(bulk_start, bulk_end, force_bulk=True)
    print(f"📊 벌크 로드 완료: {len(day_frames)} 영업일")

    cash = float(v5.environment.initial_cash)
    base_initial = cash
    trade_id_offset = 0
    all_equity: list[pd.DataFrame] = []
    all_trades: list[pd.DataFrame] = []
    all_detail: list[pd.DataFrame] = []
    all_pass: list[str] = []

    for phase in RELAY_PHASES:
        codes = load_relay_universe_codes(phase, universe_dir=universe_dir)
        print(
            f"\n▶ {phase.phase_id}구간 {phase.segment_start}~{phase.segment_end} "
            f"· {len(codes)}종 · 시작자산 {cash:,.0f}원"
        )
        manager = PortfolioManagerV5(
            day_frames,
            bdays,
            start_date=phase.segment_start,
            end_date=phase.segment_end,
            v5_config=v5,
            target_universe=frozenset(codes),
            starting_cash=cash,
            period_end_date=phase.segment_end,
            trade_id_offset=trade_id_offset,
        )
        result = manager.run()

        if not result.equity_curve.empty:
            eq = result.equity_curve.copy()
            eq["relay_phase"] = phase.phase_id
            all_equity.append(eq)
        if not result.trades.empty:
            tr = result.trades.copy()
            tr["relay_phase"] = phase.phase_id
            all_trades.append(tr)
        if not result.trades_detail.empty:
            td = result.trades_detail.copy()
            td["relay_phase"] = phase.phase_id
            all_detail.append(td)
        for line in result.pass_logs:
            all_pass.append(f"[P{phase.phase_id}] {line}")

        if not result.equity_curve.empty:
            cash = float(result.equity_curve["total_equity"].iloc[-1])
        else:
            cash = float(manager.cash)
        trade_id_offset = manager._trade_id_counter
        m = result.metrics
        print(
            f"   구간 종료: {cash:,.0f}원 · SELL {m['total_trades']}건 · "
            f"승률 {m['win_rate_pct']:.1f}% · PF {m['profit_factor']:.2f}"
        )

    equity_merged = (
        pd.concat(all_equity, ignore_index=True) if all_equity else pd.DataFrame()
    )
    trades_merged = (
        pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    )
    detail_merged = (
        pd.concat(all_detail, ignore_index=True) if all_detail else pd.DataFrame()
    )

    metrics = _PortfolioManagerV4._compute_metrics(
        equity_merged, trades_merged, base_initial
    )

    out_dir = os.path.dirname(EQUITY_CSV)
    os.makedirs(out_dir, exist_ok=True)
    equity_merged.to_csv(EQUITY_CSV, index=False, encoding="utf-8-sig")
    detail_merged.to_csv(TRADES_CSV, index=False, encoding="utf-8-sig")
    if all_pass:
        with open(PASS_LOG_TXT, "w", encoding="utf-8") as fh:
            fh.write("\n".join(all_pass))

    phase_a = validate_phase_a_trades(detail_merged, trades_merged)
    buy_count = int((detail_merged["side"] == "BUY").sum()) if not detail_merged.empty else 0
    sell_count = int((detail_merged["side"] == "SELL").sum()) if not detail_merged.empty else 0

    print("\n========================================================")
    print(f"📈 v5 릴레이 통합 성적표 ({section})")
    print("========================================================")
    print(f"기간              : {RELAY_BACKTEST_START} ~ {RELAY_BACKTEST_END}")
    print(f"초기 자산         : {base_initial:,.0f} 원")
    print(f"최종 자산         : {metrics['final_equity']:,.0f} 원")
    print(f"누적 수익률       : {metrics['cumulative_return_pct']:.2f} %")
    print(f"총 거래 횟수      : {metrics['total_trades']} 회")
    print(f"승률              : {metrics['win_rate_pct']:.2f} %")
    pf = metrics["profit_factor"]
    pf_text = "∞ (손실 없음)" if pf == float("inf") else f"{pf:.2f}"
    print(f"프로핏 팩터       : {pf_text}")
    print(f"포트폴리오 MDD    : {metrics['mdd_pct']:.2f} %")
    print(f"Equity Curve CSV  : {EQUITY_CSV}")
    print(f"Trades Detail CSV : {TRADES_CSV}")
    print(f"  BUY {buy_count} / SELL {sell_count} 행")
    if detail_merged.empty:
        print("⚠️ 거래 0건")
    elif phase_a["ok"]:
        print("✅ Phase A DoD 통과")
    else:
        for msg in phase_a.get("issues", []):
            print(f"   - {msg}")
    print("========================================================")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="v5 릴레이 백테스트 — 7구간 동적 유니버스")
    p.add_argument(
        "--section",
        default=DEFAULT_V5_RELAY_SECTION,
        choices=("v5_3", "v5_4", "v5_5"),
        help=f"YAML 섹션 (기본 {DEFAULT_V5_RELAY_SECTION})",
    )
    p.add_argument(
        "--scan-universes",
        action="store_true",
        help="7구간 유니버스 JSON/meta 일괄 스캔 후 종료",
    )
    p.add_argument(
        "--scan-only",
        action="store_true",
        help="스캔만 수행 (백테스트 없음)",
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="확인 질문 생략",
    )
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    if args.scan_only:
        v5 = load_v5_relay_config(section=args.section)
        scan_all_relay_universes(v5=v5, project_root=project_root)
        sys.exit(0)

    scan_flag: bool | None = None
    if args.scan_universes:
        v5 = load_v5_relay_config(section=args.section)
        scan_all_relay_universes(v5=v5, project_root=project_root)
        scan_flag = False
    run_v5_relay_backtest(
        section=args.section,
        scan_universes=scan_flag,
        skip_prompts=args.yes,
    )
