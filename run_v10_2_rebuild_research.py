"""
v10.2.1-Rebuild — 피보나치 순수 스윙 + Safe Vault (3.5년 통합 리서치).

MarketClassifier·Momentum 완전 제거. v9.0 Fib 3단 그리드 + v10.2 자본 수확·수혈.

실행:
  python run_v10_2_rebuild_research.py --prewarm 260 --yes
  python run_v10_2_rebuild_research.py --smoke
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
from src.engine.portfolio_manager import load_merged_market_day_frames  # noqa: E402
from src.engine.portfolio_manager_v1021 import Alpha1021Config, PortfolioManagerV1021  # noqa: E402
from src.v5_config import V5MacroTrendFilterConfig, load_v5_relay_config  # noqa: E402
from src.v5_universe import _fetch_pykrx_listed_shares_by_code  # noqa: E402

BT_START = "2023-01-01"
BT_END = "2026-05-31"
OUT_DIR = os.path.join(project_root, "outputs")
TRADES_CSV = os.path.join(OUT_DIR, "v10_2_rebuild_trades.csv")
EQUITY_CSV = os.path.join(OUT_DIR, "v10_2_rebuild_equity.csv")
REPORT_MD = os.path.join(OUT_DIR, "v10_2_rebuild_research_report.md")
DEFAULT_PREWARM = 260

V9_BENCHMARK_PF = 3.17
TARGET_MDD_PCT = -7.0
TARGET_PF = 2.0


def _build_v1021_config():
    base = load_v5_relay_config(section="v5_5")
    macro_off = V5MacroTrendFilterConfig(enabled=False)
    return replace(
        base,
        section="v10_2_1",
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
            strategy_name="v10_2_1_fib_swing_vault",
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


def _fmt_rebuild_report(
    metrics: dict,
    detail: pd.DataFrame,
    equity: pd.DataFrame,
    buf: dict,
) -> str:
    tu = _trade_unit_winrate(detail)
    mdd = float(metrics["mdd_pct"])
    pf = float(metrics["profit_factor"])
    lines = [
        _fmt_metrics("V10.2.1-Rebuild (Fib Swing + Safe Vault)", metrics, detail),
        "### v10.2.1 Safe Vault",
        "",
        f"- 금고 최종 잔액: **{buf['safe_vault']:,.0f}원**",
        f"- 수확 {buf['harvest_count']}회 (누적 {buf['total_harvested']:,.0f}원)",
        f"- 전량 수혈 {buf['refill_full_count']}회 / 부분 {buf['refill_partial_count']}회 "
        f"(누적 {buf['total_refilled']:,.0f}원)",
        "",
        "### Cut-off vs v9.0 벤치마크",
        "",
        f"- PF: **{pf:.2f}** (목표 ≥{TARGET_PF}, v9.0 벤치마크 {V9_BENCHMARK_PF}) → "
        f"{'✅ PASS' if pf >= TARGET_PF else '❌ FAIL'}",
        f"- MDD: **{mdd:.2f}%** (목표 ≥{TARGET_MDD_PCT}%) → "
        f"{'✅ PASS' if mdd >= TARGET_MDD_PCT else '❌ FAIL'}",
        f"- 진입(trade): **{tu['entries']}건**",
        "",
    ]
    if not equity.empty and "rebalance_event" in equity.columns:
        events = equity["rebalance_event"].value_counts()
        lines.append("### 일별 리밸런싱 이벤트")
        lines.append("")
        for k, v in events.items():
            if k and k != "none":
                lines.append(f"- {k}: {v}일")
        lines.append("")
    return "\n".join(lines)


def _run_smoke() -> int:
    import py_compile

    files = [
        "src/engine/portfolio_manager_v1021.py",
        "src/engine/capital_buffer_manager.py",
        "src/engine/portfolio_manager_v900.py",
        "run_v10_2_rebuild_research.py",
        "tests/test_fib_swing_strategy.py",
        "tests/test_capital_manager.py",
    ]
    for f in files:
        py_compile.compile(os.path.join(project_root, f), doraise=True)
        print(f"  py_compile OK: {f}", flush=True)

    from tests.test_capital_manager import run_unit_tests as run_capital
    from tests.test_fib_swing_strategy import run_unit_tests as run_fib

    run_fib()
    run_capital()
    print("v10.2.1-Rebuild smoke PASS", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="v10.2.1-Rebuild Fib Swing + Safe Vault")
    p.add_argument("--prewarm", type=int, default=DEFAULT_PREWARM)
    p.add_argument("--start", default=BT_START)
    p.add_argument("--end", default=BT_END)
    p.add_argument("--yes", action="store_true")
    p.add_argument("--smoke", action="store_true")
    args = p.parse_args(argv)

    if args.smoke:
        return _run_smoke()

    prewarm = max(int(args.prewarm), DEFAULT_PREWARM)
    v5 = _build_v1021_config()
    start_s = str(args.start).strip()[:10]
    end_s = str(args.end).strip()[:10]

    print(
        f"v10.2.1-Rebuild · Fib Swing + Safe Vault · {start_s}~{end_s} · "
        f"자금 {v5.environment.initial_cash:,.0f}원 · 슬롯 {v5.portfolio.max_slots}",
        flush=True,
    )
    if not args.yes and not _prompt_yes_no("3.5년 통합 리서치를 실행할까요?"):
        print("취소됨.", flush=True)
        return 0

    os.makedirs(OUT_DIR, exist_ok=True)

    print(f"벌크 OHLCV 로드 {start_s}~{end_s} ...", flush=True)
    day_frames, bdays = load_merged_market_day_frames(
        start_s,
        end_s,
        warm_bdays=prewarm + 30,
    )
    print(f"  {len(bdays)} 영업일 · {len(day_frames[0])} 종목(첫일)", flush=True)

    print("시총 캐시 (KOSPI+KOSDAQ) ...", flush=True)
    marcap_cache = _build_marcap_cache_dual(bdays)
    shares = _load_shares_fallback(end_s)
    target_universe = _universe_at_start(day_frames, bdays, start_s)
    index_members = load_index_members(start_s)
    print(f"  universe {len(target_universe):,} · index {len(index_members):,}", flush=True)

    manager = PortfolioManagerV1021(
        day_frames,
        bdays,
        start_date=start_s,
        end_date=end_s,
        v5_config=v5,
        target_universe=target_universe,
        index_members=index_members,
        alpha=Alpha1021Config(prewarm_bars=prewarm),
        marcap_by_date_code=marcap_cache,
        shares_by_code=shares,
        prewarm_bars=prewarm,
    )

    print("피보나치 3단 그리드 + 15:30 Safe Vault 시뮬레이션 ...", flush=True)
    result = manager.run()
    metrics = result.metrics
    tu = _trade_unit_winrate(result.trades_detail)
    buf = manager.capital_buffer.summary()

    if not result.trades.empty:
        result.trades.to_csv(TRADES_CSV, index=False, encoding="utf-8-sig")
    if not result.equity_curve.empty:
        result.equity_curve.to_csv(EQUITY_CSV, index=False, encoding="utf-8-sig")

    report = _fmt_rebuild_report(metrics, result.trades_detail, result.equity_curve, buf)
    with open(REPORT_MD, "w", encoding="utf-8") as fh:
        fh.write("# v10.2.1-Rebuild Research Report\n\n")
        fh.write(report)
        fh.write("\n## Architecture\n\n")
        fh.write("- v9.0.0 Fib 3단 그리드 (0.382/0.500/0.618 · 1:1:2)\n")
        fh.write("- KOSPI200/KOSDAQ150 · MA60×MA200 GC · Risk-Free 본전 스탑\n")
        fh.write("- v10.2 Safe Vault: 수익 수확 / 손실 수혈 (200만 원 고정 화력)\n")
        fh.write("- **제거:** MarketClassifier · Momentum · Cash Blackout\n")
        fh.write("- 200만 원 · 4슬롯 · 매수 0.015% · 매도 0.20%\n")

    print(
        f"\n완료 · PF {metrics['profit_factor']:.2f} · "
        f"MDD {metrics['mdd_pct']:.2f}% · "
        f"누적 {metrics['cumulative_return_pct']:+.2f}% · "
        f"진입 {tu['entries']}건",
        flush=True,
    )
    print(
        f"  Safe Vault {buf['safe_vault']:,.0f}원 · "
        f"수확 {buf['harvest_count']}회 · "
        f"수혈 {buf['refill_full_count']}+{buf['refill_partial_count']}회",
        flush=True,
    )
    print(f"  trades -> {TRADES_CSV}", flush=True)
    print(f"  equity -> {EQUITY_CSV}", flush=True)
    print(f"  report -> {REPORT_MD}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
