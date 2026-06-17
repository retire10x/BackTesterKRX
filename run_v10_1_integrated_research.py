"""
v10.1.0 통합 백테스트 — 일별 MarketClassifier + Momentum/Swing/Cash 타임머신.

기간: 2023-01-01 ~ 2026-05-31 (3.5년)
자금: 200만 원 · 4슬롯 · 매수 0.015% · 매도 0.20%

실행:
  python run_v10_1_integrated_research.py --prewarm 260 --yes
  python run_v10_1_integrated_research.py --smoke
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace

import numpy as np
import pandas as pd

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from run_v7_10_alpha_research import (  # noqa: E402
    _fmt_metrics,
    _prompt_yes_no,
    _trade_unit_winrate,
)
from src.data_loader import fetch_pykrx_marcap_trade_krw_by_code  # noqa: E402
from src.engine.fib_swing_strategy import load_index_members  # noqa: E402
from src.engine.market_classifier import (  # noqa: E402
    KOSDAQ_INDEX_FDR,
    KOSPI_INDEX_FDR,
    build_regime_schedule,
    load_index_close_series,
    summarize_regime_schedule,
)
from src.engine.portfolio_manager import load_merged_market_day_frames  # noqa: E402
from src.engine.portfolio_manager_v101 import Alpha101Config, PortfolioManagerV101  # noqa: E402
from src.v5_config import V5MacroTrendFilterConfig, load_v5_relay_config  # noqa: E402
from src.v5_universe import _fetch_pykrx_listed_shares_by_code  # noqa: E402

BT_START = "2023-01-01"
BT_END = "2026-05-31"
OUT_DIR = os.path.join(project_root, "outputs")
TRADES_CSV = os.path.join(OUT_DIR, "v10_1_integrated_trades.csv")
EQUITY_CSV = os.path.join(OUT_DIR, "v10_1_integrated_equity.csv")
REGIME_CSV = os.path.join(OUT_DIR, "v10_1_regime_schedule.csv")
REPORT_MD = os.path.join(OUT_DIR, "v10_1_integrated_research_report.md")
DEFAULT_PREWARM = 260

TARGET_MDD_PCT = -7.0
TARGET_PF = 2.0


def _build_v101_config():
    base = load_v5_relay_config(section="v5_5")
    macro_off = V5MacroTrendFilterConfig(enabled=False)
    return replace(
        base,
        section="v10_1",
        environment=replace(base.environment, initial_cash=2_000_000),
        portfolio=replace(
            base.portfolio,
            max_slots=4,
            slot_invest_amount=500_000,
            trading_costs=replace(
                base.portfolio.trading_costs,
                buy_cost_ratio=0.00015,
                sell_cost_ratio=0.0020,
            ),
        ),
        strategy=replace(
            base.strategy,
            strategy_name="v10_1_integrated",
            lookback_window=200,
            stop_loss_ratio=None,
            target_profit_ratio=None,
            max_hold_days=None,
            macro_trend_filter=macro_off,
        ),
    )


def _build_marcap_cache_dual(
    bdays: pd.DatetimeIndex,
    *,
    verbose: bool = True,
) -> dict[tuple[str, str], float]:
    cache: dict[tuple[str, str], float] = {}
    total = len(bdays)
    for i, dt in enumerate(bdays):
        date_s = pd.Timestamp(dt).normalize().strftime("%Y-%m-%d")
        for market in ("KOSPI", "KOSDAQ"):
            snap = fetch_pykrx_marcap_trade_krw_by_code(date_s, market=market)
            for code, (mc, _ta) in snap.items():
                c6 = str(code).zfill(6)
                if mc is not None and np.isfinite(mc) and float(mc) > 0:
                    cache[(date_s, c6)] = float(mc)
        if verbose and (i == 0 or (i + 1) % 60 == 0 or i + 1 == total):
            print(f"   시총 캐시 {i + 1}/{total}일 · {len(cache):,}건", flush=True)
    return cache


def _load_shares_fallback(as_of_date: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for market in ("KOSPI", "KOSDAQ"):
        shares = _fetch_pykrx_listed_shares_by_code(as_of_date, market=market)
        for k, v in shares.items():
            c6 = str(k).zfill(6)
            if np.isfinite(v) and v > 0:
                out[c6] = float(v)
    return out


def _universe_at_start(day_frames, bdays, start_date: str) -> frozenset[str]:
    sd = pd.Timestamp(start_date).normalize()
    pos = int(bdays.get_indexer([sd], method="bfill")[0])
    ref = max(0, pos - 1)
    frame = day_frames[ref]
    return frozenset(str(code).zfill(6) for code in frame.index)


def _fmt_v101_report(
    metrics: dict,
    detail: pd.DataFrame,
    regime_counts: dict[str, int],
    equity: pd.DataFrame,
) -> str:
    tu = _trade_unit_winrate(detail)
    mdd = float(metrics["mdd_pct"])
    pf = float(metrics["profit_factor"])
    mdd_ok = mdd >= TARGET_MDD_PCT
    pf_ok = pf >= TARGET_PF
    total_days = sum(regime_counts.values()) or 1

    lines = [
        _fmt_metrics("V10.1.0 Integrated (Momentum + Swing + Cash)", metrics, detail),
        "### 장세 일수 분포",
        "",
        f"- momentum: {regime_counts.get('momentum', 0)}일 "
        f"({regime_counts.get('momentum', 0) / total_days * 100:.1f}%)",
        f"- swing: {regime_counts.get('swing', 0)}일 "
        f"({regime_counts.get('swing', 0) / total_days * 100:.1f}%)",
        f"- cash (Blackout): {regime_counts.get('cash', 0)}일 "
        f"({regime_counts.get('cash', 0) / total_days * 100:.1f}%)",
        "",
        "### Cut-off 검증 (사령탑 3대 지표)",
        "",
        f"- MDD: **{mdd:.2f}%** (목표 {TARGET_MDD_PCT}% 이상) → "
        f"{'✅ PASS' if mdd_ok else '❌ FAIL'}",
        f"- PF: **{pf:.2f}** (목표 {TARGET_PF} 이상) → "
        f"{'✅ PASS' if pf_ok else '❌ FAIL'}",
        f"- 진입(trade) 건수: **{tu['entries']}건** "
        f"(과매매·유령봇 밸런스 수동 검토)",
        "",
    ]

    if not detail.empty and "exit_type" in detail.columns:
        sells = detail[detail["side"] == "SELL"]
        swing_sells = sells[sells["exit_type"].astype(str).str.startswith("SWING")].shape[0]
        mom_sells = sells[sells["exit_type"].astype(str).str.startswith("MOMENTUM")].shape[0]
        lines.extend([
            "### 엔진별 청산 이벤트",
            "",
            f"- Swing: {swing_sells}건",
            f"- Momentum: {mom_sells}건",
            "",
        ])

    if not equity.empty and "regime" in equity.columns:
        rc = equity["regime"].value_counts()
        lines.append("### 일별 equity curve regime 샘플 (최근 5일)")
        lines.append("")
        for _, row in equity.tail(5).iterrows():
            lines.append(
                f"- {row['date']}: {row['regime']} · 자산 {float(row['total_equity']):,.0f}원"
            )
        lines.append("")

    return "\n".join(lines)


def _run_smoke() -> int:
    import py_compile

    files = [
        "src/engine/capital_buffer_manager.py",
        "src/engine/market_classifier.py",
        "src/engine/portfolio_manager_v101.py",
        "run_v10_1_integrated_research.py",
        "tests/test_market_classifier.py",
    ]
    for f in files:
        py_compile.compile(os.path.join(project_root, f), doraise=True)
        print(f"  py_compile OK: {f}", flush=True)

    from tests.test_capital_manager import run_unit_tests as run_capital_tests
    from tests.test_market_classifier import run_unit_tests

    run_unit_tests()
    run_capital_tests()
    print("v10.1.0 integrated smoke PASS", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="v10.1.0 통합 백테스트")
    p.add_argument("--prewarm", type=int, default=DEFAULT_PREWARM)
    p.add_argument("--start", default=BT_START)
    p.add_argument("--end", default=BT_END)
    p.add_argument("--yes", action="store_true")
    p.add_argument("--smoke", action="store_true")
    args = p.parse_args(argv)

    if args.smoke:
        return _run_smoke()

    prewarm = max(int(args.prewarm), DEFAULT_PREWARM)
    v5 = _build_v101_config()
    start_s = str(args.start).strip()[:10]
    end_s = str(args.end).strip()[:10]

    print(
        f"v10.1.0 Integrated · {start_s}~{end_s} · "
        f"자금 {v5.environment.initial_cash:,.0f}원 · 슬롯 {v5.portfolio.max_slots}",
        flush=True,
    )
    if not args.yes and not _prompt_yes_no("통합 백테스트를 실행할까요?"):
        print("취소됨.", flush=True)
        return 0

    os.makedirs(OUT_DIR, exist_ok=True)

    print("① 지수 동기화 (KOSPI/KOSDAQ FDR) ...", flush=True)
    warm_start = (pd.Timestamp(start_s) - pd.Timedelta(days=120)).strftime("%Y-%m-%d")
    kospi = load_index_close_series(fdr_ticker=KOSPI_INDEX_FDR, start=warm_start, end=end_s)
    kosdaq = load_index_close_series(fdr_ticker=KOSDAQ_INDEX_FDR, start=warm_start, end=end_s)
    print(f"  KOSPI {len(kospi)}일 · KOSDAQ {len(kosdaq)}일", flush=True)

    print(f"벌크 OHLCV 로드 {start_s}~{end_s} ...", flush=True)
    day_frames, bdays = load_merged_market_day_frames(
        start_s,
        end_s,
        warm_bdays=prewarm + 30,
    )
    sim_bdays = bdays[(bdays >= pd.Timestamp(start_s)) & (bdays <= pd.Timestamp(end_s))]
    print(f"  {len(sim_bdays)} 영업일 · {len(day_frames[0])} 종목(첫일)", flush=True)

    print("② MarketClassifier 타임머신 스케줄 ...", flush=True)
    regime_schedule = build_regime_schedule(sim_bdays, kospi, kosdaq)
    regime_counts = summarize_regime_schedule(regime_schedule)
    print(
        f"  momentum {regime_counts.get('momentum', 0)}일 · "
        f"swing {regime_counts.get('swing', 0)}일 · "
        f"cash {regime_counts.get('cash', 0)}일",
        flush=True,
    )

    pd.DataFrame(
        [{"date": d, "regime": r} for d, r in sorted(regime_schedule.items())]
    ).to_csv(REGIME_CSV, index=False, encoding="utf-8-sig")

    print("시총 캐시 (KOSPI+KOSDAQ) ...", flush=True)
    marcap_cache = _build_marcap_cache_dual(bdays)
    shares = _load_shares_fallback(end_s)
    target_universe = _universe_at_start(day_frames, bdays, start_s)
    index_members = load_index_members(start_s)
    print(f"  universe {len(target_universe):,} · index {len(index_members):,}", flush=True)

    manager = PortfolioManagerV101(
        day_frames,
        bdays,
        start_date=start_s,
        end_date=end_s,
        v5_config=v5,
        target_universe=target_universe,
        regime_by_date=regime_schedule,
        index_members=index_members,
        alpha=Alpha101Config(prewarm_bars=prewarm),
        marcap_by_date_code=marcap_cache,
        shares_by_code=shares,
        prewarm_bars=prewarm,
    )

    print("③ 통합 시뮬레이션 가동 ...", flush=True)
    result = manager.run()
    metrics = result.metrics
    tu = _trade_unit_winrate(result.trades_detail)

    if not result.trades.empty:
        result.trades.to_csv(TRADES_CSV, index=False, encoding="utf-8-sig")
    if not result.equity_curve.empty:
        result.equity_curve.to_csv(EQUITY_CSV, index=False, encoding="utf-8-sig")

    report = _fmt_v101_report(metrics, result.trades_detail, regime_counts, result.equity_curve)
    buf = manager.capital_buffer.summary()
    print(
        f"\n[v10.2 Safe Vault] 금고 잔액 {buf['safe_vault']:,.0f}원 · "
        f"수확 {buf['harvest_count']}회 · "
        f"전량수혈 {buf['refill_full_count']}회 · "
        f"부분수혈 {buf['refill_partial_count']}회",
        flush=True,
    )
    with open(REPORT_MD, "w", encoding="utf-8") as fh:
        fh.write("# v10.1.0 Integrated Research Report\n\n")
        fh.write(report)
        fh.write("\n## v10.2 Capital Buffer\n\n")
        fh.write(f"- Safe Vault 최종: **{buf['safe_vault']:,.0f}원**\n")
        fh.write(f"- 수확 {buf['harvest_count']}회 (누적 {buf['total_harvested']:,.0f}원)\n")
        fh.write(
            f"- 수혈 전량 {buf['refill_full_count']}회 / 부분 {buf['refill_partial_count']}회 "
            f"(누적 {buf['total_refilled']:,.0f}원)\n"
        )
        fh.write("\n## Spec\n\n")
        fh.write("- 일별 KOSPI/KOSDAQ MA5·MA20 Fact → momentum/swing/cash\n")
        fh.write("- 스윙 보유 → 스윙 청산·추격매수 유지 / 빈 슬롯만 당일 regime 진입\n")
        fh.write("- cash → 신규 매수 Blackout (기존 포지션 청산 룰 유지)\n")
        fh.write("- 200만 원 · 4슬롯 · 매수 0.015% · 매도 0.20%\n")
        fh.write("- v10.2: 15:30 Safe Vault 수확·수혈\n")

    print(
        f"\n완료 · PF {metrics['profit_factor']:.2f} · "
        f"MDD {metrics['mdd_pct']:.2f}% · "
        f"누적 {metrics['cumulative_return_pct']:+.2f}% · "
        f"진입 {tu['entries']}건",
        flush=True,
    )
    print(f"  trades  -> {TRADES_CSV}", flush=True)
    print(f"  equity  -> {EQUITY_CSV}", flush=True)
    print(f"  regime  -> {REGIME_CSV}", flush=True)
    print(f"  report  -> {REPORT_MD}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
