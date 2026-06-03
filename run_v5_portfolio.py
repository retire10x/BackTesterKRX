"""
v5.x 20일선 변곡점 스나이퍼 백테스트.

SSOT: config/settings.yaml v5_2 (기본) / v5_1 / v5_0
전략 설계 단계: 실행 전 유니버스·백테스트 여부를 반드시 질문한다.
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
from src.engine.portfolio_manager import load_merged_market_day_frames  # noqa: E402
from src.engine.portfolio_manager_v5 import PortfolioManagerV5  # noqa: E402
from src.v5_config import (  # noqa: E402
    DEFAULT_V5_SECTION,
    get_effective_universe_lock,
    load_v5_config,
    v5_config_for_universe_scan,
)
from src.v5_universe import load_v5_target_universe, scan_and_write_kosdaq_sniper_universe  # noqa: E402

EQUITY_CSV = os.path.join(project_root, "outputs", "v5_equity_curve.csv")
TRADES_CSV = os.path.join(project_root, "outputs", "v5_trades.csv")
PASS_LOG_TXT = os.path.join(project_root, "outputs", "v5_pass_log.txt")


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


def _format_entry_plan(strat) -> str:
    base = f"MA{strat.lookback_window} 변곡 (어제≤MA · 오늘>20영업일전종가)"
    mf = strat.macro_trend_filter
    if mf is None or not mf.enabled:
        return base
    parts = [base]
    if mf.uses_dual_slope:
        parts.append(" AND ".join(f"MA{w}↑" for w in mf.dual_slope_alignment))
        if mf.check_prices_above_ma:
            parts.append(f"종가>MA{mf.check_prices_above_ma}")
    elif mf.ma_window:
        parts.append(f"종가>MA{mf.ma_window}")
    return " AND ".join(parts)


def _print_strategy_plan(v5, universe_codes: list[str] | None) -> None:
    env = v5.environment
    port = v5.portfolio
    strat = v5.strategy
    costs = port.trading_costs
    print(f"\n--- v5 백테스트 실행 계획 ({v5.section}) ---")
    print(f"  전략     : {strat.strategy_name}")
    print(f"  기간     : {START_DATE} ~ {END_DATE}")
    print(f"  자본     : {env.initial_cash:,.0f}원 · 슬롯 {port.max_slots} × {port.slot_invest_amount:,.0f}원")
    entry = _format_entry_plan(strat)
    print(f"  진입     : {entry}")
    if strat.use_hit_and_run_exit:
        print(
            f"  청산     : 익절 +{strat.target_profit_ratio:.0%} · "
            f"손절 -{strat.stop_loss_ratio:.0%} · {strat.max_hold_days}일 타임스탑 (장중 H/L)"
        )
    else:
        ew = strat.exit_ma_window or strat.lookback_window
        print(f"  청산     : MA{ew} 종가 이탈")
    if strat.price_floor is not None and strat.price_ceiling is not None:
        print(f"  가격필터 : {strat.price_floor:,.0f}~{strat.price_ceiling:,.0f}원")
    else:
        print("  가격필터 : 없음 (고정 유니버스)")
    if env.universe_profile:
        print(f"  유니버스 : {env.universe_profile} ({len(universe_codes or [])}종)")
        if env.universe_lock is not None:
            lk = env.universe_lock
            from src.v5_universe import format_krw_eok

            print(
                f"  유니버스락: {lk.lock_date} 시점 박제 "
                f"(시총 {format_krw_eok(lk.min_mcap_krw)}~{format_krw_eok(lk.max_mcap_krw)} · "
                f"거래대금≥{format_krw_eok(lk.min_trade_krw)} · Top{lk.top_n} · {lk.market})"
            )
        if universe_codes:
            print(f"             예: {', '.join(universe_codes[:5])}{'…' if len(universe_codes) > 5 else ''}")
    else:
        print("  유니버스 : KOSPI+KOSDAQ 전종목 (거래대금 순)")
    print(f"  비용     : 매수 {costs.buy_cost_ratio:.4%} / 매도 {costs.sell_cost_ratio:.4%}")
    print("-------------------------------------------\n")


def run_v5_portfolio_backtest(
    *,
    section: str = DEFAULT_V5_SECTION,
    scan_universe: bool | None = None,
    skip_prompts: bool = False,
) -> None:
    """
    scan_universe: True=JSON 재스캔, False=기존 JSON, None=터미널에서 질문.
    skip_prompts: True면 확인 없이 즉시 실행 (자동화·CI 전용).
    """
    v5 = load_v5_config(section=section)
    env = v5.environment
    universe_codes: list[str] | None = None
    target_universe: frozenset[str] | None = None

    if env.universe_profile:
        if scan_universe is None and not skip_prompts:
            lock_hint = ""
            eff_lock = get_effective_universe_lock(v5)
            if eff_lock is not None:
                src = v5.section if env.universe_lock is not None else "v5_1(폴백)"
                lock_hint = f" [락 {eff_lock.lock_date} · {src}]"
            scan_universe = _prompt_yes_no(
                f"코스닥 유니버스를 lock_date 기준으로 박제 스캔할까요?{lock_hint}",
                default="n",
            )
        if scan_universe:
            scan_cfg = v5_config_for_universe_scan(v5)
            if env.universe_lock is None:
                lk = scan_cfg.environment.universe_lock
                assert lk is not None
                print(
                    f"ℹ️ {v5.section} 에 universe_lock 없음 → "
                    f"v5_1 락({lk.lock_date})으로 스캔합니다."
                )
            print("📡 유니버스 락 스캔 중 (과거 스냅샷만 사용, 당일/미래 시총 금지)…")
            universe_codes = scan_and_write_kosdaq_sniper_universe(
                project_root=project_root,
                config=scan_cfg,
            )
        else:
            universe_codes = load_v5_target_universe(project_root=project_root, config=v5)
            print(f"📂 고정 유니버스 로드: {len(universe_codes)}종 ({env.universe_profile})")
        target_universe = frozenset(universe_codes)

    _print_strategy_plan(v5, universe_codes)

    if not skip_prompts:
        if not _prompt_yes_no("위 설정으로 백테스트를 실행할까요?", default="n"):
            print("백테스트를 취소했습니다. (전략 설계 단계 — 실행은 승인 후에만)")
            return

    port = v5.portfolio
    strat = v5.strategy
    costs = port.trading_costs

    print(f"🚀 v5 ({v5.section}) 벌크 로딩 중…")
    day_frames, bdays = load_merged_market_day_frames(START_DATE, END_DATE, force_bulk=True)
    print(f"📊 벌크 로드 완료: {len(day_frames)} 영업일")

    manager = PortfolioManagerV5(
        day_frames,
        bdays,
        start_date=START_DATE,
        end_date=END_DATE,
        v5_config=v5,
        target_universe=target_universe,
    )
    result = manager.run()

    out_dir = os.path.dirname(EQUITY_CSV)
    os.makedirs(out_dir, exist_ok=True)
    result.equity_curve.to_csv(EQUITY_CSV, index=False, encoding="utf-8-sig")
    result.trades_detail.to_csv(TRADES_CSV, index=False, encoding="utf-8-sig")
    if result.pass_logs:
        with open(PASS_LOG_TXT, "w", encoding="utf-8") as fh:
            fh.write("\n".join(result.pass_logs))

    phase_a = validate_phase_a_trades(result.trades_detail, result.trades)
    buy_count = int((result.trades_detail["side"] == "BUY").sum()) if not result.trades_detail.empty else 0
    sell_count = int((result.trades_detail["side"] == "SELL").sum()) if not result.trades_detail.empty else 0
    m = result.metrics

    print("\n========================================================")
    print(f"📈 v5 ({v5.section}) 최종 성적표")
    print("========================================================")
    print(f"기간              : {START_DATE} ~ {END_DATE}")
    print(f"초기 자산         : {env.initial_cash:,.0f} 원")
    print(f"최종 자산         : {m['final_equity']:,.0f} 원")
    print(f"누적 수익률       : {m['cumulative_return_pct']:.2f} %")
    print(f"총 거래 횟수      : {m['total_trades']} 회")
    print(f"승률              : {m['win_rate_pct']:.2f} %")
    pf = m["profit_factor"]
    pf_text = "∞ (손실 없음)" if pf == float("inf") else f"{pf:.2f}"
    print(f"프로핏 팩터       : {pf_text}")
    print(f"포트폴리오 MDD    : {m['mdd_pct']:.2f} %")
    print(f"Equity Curve CSV  : {EQUITY_CSV}")
    print(f"Trades Detail CSV : {TRADES_CSV}")
    print(f"  BUY {buy_count} / SELL {sell_count} 행")
    print("--- Phase A 검증 ---")
    if result.trades_detail.empty:
        print("⚠️ 거래 0건 — 변곡 조건·유니버스·히스토리 점검 필요")
    elif phase_a["ok"]:
        print("✅ Phase A DoD 통과")
    else:
        for msg in phase_a.get("issues", []):
            print(f"   - {msg}")
    print("========================================================")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="v5 변곡점 스나이퍼 — 실행 전 확인 필수")
    p.add_argument(
        "--section",
        default=DEFAULT_V5_SECTION,
        choices=("v5_0", "v5_1", "v5_2", "v5_3", "v5_4", "v5_5"),
        help=f"YAML 섹션 (기본 {DEFAULT_V5_SECTION})",
    )
    p.add_argument(
        "--scan-universe",
        action="store_true",
        help="고정 유니버스 JSON 재스캔 후 저장 (universe_lock SSOT)",
    )
    p.add_argument(
        "--no-scan",
        action="store_true",
        help="기존 JSON 유니버스 사용 (질문 생략)",
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="확인 질문 생략 후 즉시 백테스트 (자동화 전용)",
    )
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    scan: bool | None = None
    if args.scan_universe:
        scan = True
    elif args.no_scan:
        scan = False
    run_v5_portfolio_backtest(
        section=args.section,
        scan_universe=scan,
        skip_prompts=args.yes,
    )
