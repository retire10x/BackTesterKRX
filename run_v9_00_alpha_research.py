"""
v9.0.0 대형주 피보나치 스윙 (Risk-Free Swing) 검증 러너.

진입: MA60×MA200 GC(3~6개월) + 피보나치 0.382/0.500/0.618 종가 분할매수
청산: 스윙고점 50% 익절 → 본전 손절 → 0라인/1:2 전량손절
유니버스: KOSPI200/KOSDAQ150 또는 시총 1조+, 5,000억 미만 제외
자금: 200만 원 · 4슬롯

실행:
  python run_v9_00_alpha_research.py --prewarm 220 --yes
  python run_v9_00_alpha_research.py --smoke
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
from src.engine.portfolio_manager import (  # noqa: E402
    PortfolioManager as _PortfolioManagerV4,
    load_merged_market_day_frames,
)
from src.engine.portfolio_manager_v900 import Alpha900Config, PortfolioManagerV900  # noqa: E402
from src.v5_config import V5MacroTrendFilterConfig, load_v5_relay_config  # noqa: E402
from src.v5_relay_screener import (  # noqa: E402
    RELAY_BACKTEST_END,
    RELAY_BACKTEST_START,
    RELAY_PHASES,
)
from src.v5_universe import _fetch_pykrx_listed_shares_by_code  # noqa: E402

OUT_DIR = os.path.join(project_root, "outputs")
TRADES_CSV = os.path.join(OUT_DIR, "v9_00_fib_swing_trades.csv")
REPORT_MD = os.path.join(OUT_DIR, "v9_00_fib_swing_research_report.md")
DEFAULT_PREWARM = 220


def _build_v9_config():
    base = load_v5_relay_config(section="v5_5")
    macro_off = V5MacroTrendFilterConfig(enabled=False)
    return replace(
        base,
        section="v9_0",
        environment=replace(base.environment, initial_cash=2_000_000),
        portfolio=replace(
            base.portfolio,
            max_slots=4,
            slot_invest_amount=500_000,
        ),
        strategy=replace(
            base.strategy,
            strategy_name="fib_swing_risk_free",
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


def _phase_universe_all_large(day_frames, bdays, segment_start) -> frozenset[str]:
    """구간 시작일 기준 벌크 프레임 전 종목(매니저 내부에서 시총/지수 필터)."""
    sd = pd.Timestamp(segment_start).normalize()
    pos = int(bdays.get_indexer([sd], method="bfill")[0])
    ref = max(0, pos - 1)
    frame = day_frames[ref]
    return frozenset(str(code).zfill(6) for code in frame.index)


def _run_relay_v9(
    *,
    label: str,
    day_frames,
    bdays,
    v5,
    phase_universes: dict[int, frozenset[str]],
    phase_index: dict[int, frozenset[str]],
    alpha_kwargs: dict,
):
    cash = float(v5.environment.initial_cash)
    base_initial = cash
    trade_id_offset = 0
    all_equity: list[pd.DataFrame] = []
    all_trades: list[pd.DataFrame] = []
    all_detail: list[pd.DataFrame] = []

    print(f"\n=== [{label}] 7구간 릴레이 시작 (초기 {base_initial:,.0f}원) ===", flush=True)
    for phase in RELAY_PHASES:
        phase_uni = phase_universes[phase.phase_id]
        manager = PortfolioManagerV900(
            day_frames,
            bdays,
            start_date=phase.segment_start,
            end_date=phase.segment_end,
            v5_config=v5,
            target_universe=phase_uni,
            starting_cash=cash,
            period_end_date=phase.segment_end,
            trade_id_offset=trade_id_offset,
            index_members=phase_index.get(phase.phase_id, frozenset()),
            **alpha_kwargs,
        )
        result = manager.run()

        if not result.equity_curve.empty:
            eq = result.equity_curve.copy()
            eq["relay_phase"] = phase.phase_id
            all_equity.append(eq)
            cash = float(result.equity_curve["total_equity"].iloc[-1])
        else:
            cash = float(manager.cash)
        if not result.trades.empty:
            tr = result.trades.copy()
            tr["relay_phase"] = phase.phase_id
            all_trades.append(tr)
        if not result.trades_detail.empty:
            td = result.trades_detail.copy()
            td["relay_phase"] = phase.phase_id
            all_detail.append(td)
        trade_id_offset = manager._trade_id_counter
        m = result.metrics
        print(
            f"  [{phase.phase_id}] {phase.segment_start}~{phase.segment_end} -> "
            f"{cash:,.0f}원 · SELL {m['total_trades']}건 · 승률 {m['win_rate_pct']:.1f}% · PF {m['profit_factor']:.2f}",
            flush=True,
        )

    equity = pd.concat(all_equity, ignore_index=True) if all_equity else pd.DataFrame()
    trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    detail = pd.concat(all_detail, ignore_index=True) if all_detail else pd.DataFrame()
    metrics = _PortfolioManagerV4._compute_metrics(equity, trades, base_initial)
    return equity, trades, detail, metrics


def _run_smoke() -> int:
    import py_compile

    files = [
        "src/engine/fib_swing_strategy.py",
        "src/engine/portfolio_manager_v900.py",
        "run_v9_00_alpha_research.py",
        "tests/test_fib_swing_strategy.py",
    ]
    for f in files:
        path = os.path.join(project_root, f)
        py_compile.compile(path, doraise=True)
        print(f"  py_compile OK: {f}", flush=True)

    from tests.test_fib_swing_strategy import run_unit_tests

    run_unit_tests()
    print("v9.0.0 smoke test PASS", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="v9.0.0 Fib Swing Risk-Free 검증")
    p.add_argument("--prewarm", type=int, default=DEFAULT_PREWARM)
    p.add_argument("--yes", action="store_true")
    p.add_argument("--smoke", action="store_true", help="컴파일+유닛 테스트만")
    args = p.parse_args(argv)

    if args.smoke:
        return _run_smoke()

    prewarm = max(int(args.prewarm), DEFAULT_PREWARM)
    v5 = _build_v9_config()

    print(
        f"v9.0.0 Fib Swing · 자금 {v5.environment.initial_cash:,.0f}원 · "
        f"슬롯 {v5.portfolio.max_slots} · prewarm {prewarm}",
        flush=True,
    )
    if not args.yes and not _prompt_yes_no("7구간 릴레이 백테스트를 실행할까요?"):
        print("취소됨.", flush=True)
        return 0

    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"벌크 OHLCV 로드 {RELAY_BACKTEST_START}~{RELAY_BACKTEST_END} ...", flush=True)
    day_frames, bdays = load_merged_market_day_frames(
        RELAY_BACKTEST_START,
        RELAY_BACKTEST_END,
        warm_bdays=prewarm + 30,
    )
    print(f"  {len(bdays)} 영업일 · {len(day_frames[0])} 종목(첫일)", flush=True)

    print("시총 캐시 구축 (KOSPI+KOSDAQ) ...", flush=True)
    marcap_cache = _build_marcap_cache_dual(bdays)
    shares = _load_shares_fallback(RELAY_BACKTEST_END)

    phase_universes: dict[int, frozenset[str]] = {}
    phase_index: dict[int, frozenset[str]] = {}
    for phase in RELAY_PHASES:
        phase_universes[phase.phase_id] = _phase_universe_all_large(
            day_frames, bdays, phase.segment_start
        )
        phase_index[phase.phase_id] = load_index_members(phase.segment_start)
        print(
            f"  Phase {phase.phase_id}: universe {len(phase_universes[phase.phase_id]):,} · "
            f"index {len(phase_index[phase.phase_id]):,}",
            flush=True,
        )

    alpha_kwargs = {
        "alpha": Alpha900Config(prewarm_bars=prewarm),
        "marcap_by_date_code": marcap_cache,
        "shares_by_code": shares,
        "prewarm_bars": prewarm,
    }

    eq, trades, detail, metrics = _run_relay_v9(
        label="V9.0.0 FIB SWING",
        day_frames=day_frames,
        bdays=bdays,
        v5=v5,
        phase_universes=phase_universes,
        phase_index=phase_index,
        alpha_kwargs=alpha_kwargs,
    )

    if not trades.empty:
        trades.to_csv(TRADES_CSV, index=False, encoding="utf-8-sig")
    report = _fmt_metrics("V9.0.0 Fib Swing Risk-Free", metrics, detail)
    with open(REPORT_MD, "w", encoding="utf-8") as fh:
        fh.write("# v9.0.0 Fib Swing Research Report\n\n")
        fh.write(report)
        fh.write("\n## Spec\n\n")
        fh.write("- 200만 원 · 4슬롯 · 1:1:2 분할(0.382/0.500/0.618)\n")
        fh.write("- KOSPI200/KOSDAQ150 또는 시총 1조+, 5천억 미만 제외\n")
        fh.write("- 15:20 일봉 종가 체결\n")

    tu = _trade_unit_winrate(detail)
    print(
        f"\n완료 · PF {metrics['profit_factor']:.2f} · "
        f"누적 {metrics['cumulative_return_pct']:+.2f}% · "
        f"진입 {tu['entries']}건",
        flush=True,
    )
    print(f"  trades -> {TRADES_CSV}", flush=True)
    print(f"  report -> {REPORT_MD}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
