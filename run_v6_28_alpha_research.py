"""
v6.28 소생 및 파라미터 재조정 검증 러너.

진입: v6.27 완화 — 거래대금≥50억 + 종가/MA20≤1.10
청산: v5.5.2 순정 (+8%/-3%/4일)
유니버스: 코스닥 전종목 (--universe all, top-n 없음)

실행:
  python run_v6_28_alpha_research.py --prewarm 120 --mode dynamic_warmup --universe all --yes
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
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
        key, val = k.strip(), v.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        if key and key not in os.environ:
            os.environ[key] = val


_load_env_file(os.path.join(project_root, ".env"))

from src.data_loader import fetch_filtered_universe, fetch_pykrx_marcap_trade_krw_by_code  # noqa: E402
from src.engine.portfolio_manager import (  # noqa: E402
    PortfolioManager as _PortfolioManagerV4,
    load_merged_market_day_frames,
)
from src.engine.portfolio_manager_v626 import DEFAULT_PREWARM_BARS  # noqa: E402
from src.engine.portfolio_manager_v627 import PortfolioManagerV627  # noqa: E402
from src.engine.portfolio_manager_v628 import PortfolioManagerV628  # noqa: E402
from src.v5_config import load_v5_relay_config  # noqa: E402
from src.v5_relay_screener import (  # noqa: E402
    RELAY_BACKTEST_END,
    RELAY_BACKTEST_START,
    RELAY_PHASES,
)
from src.v5_universe import _fetch_pykrx_listed_shares_by_code  # noqa: E402

OUT_DIR = os.path.join(project_root, "outputs")
V627_TRADES_CSV = os.path.join(OUT_DIR, "v6_27_baseline_trades.csv")
V628_TRADES_CSV = os.path.join(OUT_DIR, "v6_28_alpha_trades.csv")
REPORT_MD = os.path.join(OUT_DIR, "v6_28_alpha_research_report.md")


def _prompt_yes_no(question: str, *, default: str = "n") -> bool:
    suffix = " [y/N]" if default == "n" else " [Y/n]"
    try:
        raw = input(f"{question}{suffix}: ").strip().lower()
    except EOFError:
        return False
    if not raw:
        raw = default
    return raw in ("y", "yes", "예", "ㅇ")


def _load_kosdaq_mask() -> frozenset[str]:
    uni = fetch_filtered_universe("KOSDAQ", "")
    codes = frozenset(str(c).zfill(6) for c in uni.keys())
    if not codes:
        raise RuntimeError("코스닥 종목 리스트를 불러오지 못했습니다 (FDR).")
    return codes


def _phase_universe_all(day_frames, bdays, segment_start, mask) -> frozenset[str]:
    """구간 시작 직전 영업일 — 코스닥 전종목(데이터 존재 종목)."""
    sd = pd.Timestamp(segment_start).normalize()
    pos = int(bdays.get_indexer([sd], method="bfill")[0])
    ref = max(0, pos - 1)
    frame = day_frames[ref]
    return frozenset(
        str(code).zfill(6)
        for code in frame.index
        if str(code).zfill(6) in mask
    )


def _build_marcap_cache(
    bdays: pd.DatetimeIndex,
    kosdaq_mask: frozenset[str],
    *,
    verbose: bool = True,
) -> dict[tuple[str, str], float]:
    cache: dict[tuple[str, str], float] = {}
    total = len(bdays)
    for i, dt in enumerate(bdays):
        date_s = pd.Timestamp(dt).normalize().strftime("%Y-%m-%d")
        snap = fetch_pykrx_marcap_trade_krw_by_code(date_s, market="KOSDAQ")
        for code, (mc, _ta) in snap.items():
            c6 = str(code).zfill(6)
            if c6 not in kosdaq_mask:
                continue
            if mc is not None and np.isfinite(mc) and float(mc) > 0:
                cache[(date_s, c6)] = float(mc)
        if verbose and (i == 0 or (i + 1) % 60 == 0 or i + 1 == total):
            print(f"   시총 캐시 {i + 1}/{total}일 · {len(cache):,}건")
    return cache


def _load_shares_fallback(as_of_date: str) -> dict[str, float]:
    shares = _fetch_pykrx_listed_shares_by_code(as_of_date, market="KOSDAQ")
    return {str(k).zfill(6): float(v) for k, v in shares.items() if np.isfinite(v) and v > 0}


def _run_relay(
    *,
    label: str,
    manager_cls,
    day_frames,
    bdays,
    v5,
    phase_universes: dict[int, frozenset[str]],
    manager_kwargs: dict | None = None,
):
    cash = float(v5.environment.initial_cash)
    base_initial = cash
    trade_id_offset = 0
    all_equity: list[pd.DataFrame] = []
    all_trades: list[pd.DataFrame] = []
    all_detail: list[pd.DataFrame] = []
    extra = manager_kwargs or {}

    print(f"\n=== [{label}] 7구간 릴레이 시작 (초기 {base_initial:,.0f}원) ===")
    for phase in RELAY_PHASES:
        phase_uni = phase_universes[phase.phase_id]
        manager = manager_cls(
            day_frames,
            bdays,
            start_date=phase.segment_start,
            end_date=phase.segment_end,
            v5_config=v5,
            target_universe=phase_uni,
            starting_cash=cash,
            period_end_date=phase.segment_end,
            trade_id_offset=trade_id_offset,
            **extra,
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
            f"  [{phase.phase_id}] {phase.segment_start}~{phase.segment_end} → "
            f"{cash:,.0f}원 · SELL {m['total_trades']}건 · 승률 {m['win_rate_pct']:.1f}% · PF {m['profit_factor']:.2f}"
        )

    equity = pd.concat(all_equity, ignore_index=True) if all_equity else pd.DataFrame()
    trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    detail = pd.concat(all_detail, ignore_index=True) if all_detail else pd.DataFrame()
    metrics = _PortfolioManagerV4._compute_metrics(equity, trades, base_initial)
    return equity, trades, detail, metrics


def _trade_unit_winrate(detail: pd.DataFrame) -> dict:
    if detail.empty:
        return {"entries": 0, "wins": 0, "win_rate_pct": 0.0}
    sells = detail[detail["side"] == "SELL"].copy()
    if sells.empty:
        return {"entries": 0, "wins": 0, "win_rate_pct": 0.0}
    sells["pnl_amount"] = pd.to_numeric(sells["pnl_amount"], errors="coerce").fillna(0.0)
    grp = sells.groupby("trade_id")["pnl_amount"].sum()
    entries = int(len(grp))
    wins = int((grp > 0).sum())
    return {
        "entries": entries,
        "wins": wins,
        "win_rate_pct": (wins / entries * 100.0) if entries else 0.0,
    }


def _exit_type_breakdown(detail: pd.DataFrame) -> pd.Series:
    if detail.empty:
        return pd.Series(dtype=int)
    sells = detail[detail["side"] == "SELL"]
    return sells["exit_type"].value_counts()


def _fmt_metrics(name: str, metrics: dict, detail: pd.DataFrame) -> str:
    tu = _trade_unit_winrate(detail)
    bd = _exit_type_breakdown(detail)
    lines = [
        f"### {name}",
        "",
        f"- 최종 자산: **{metrics['final_equity']:,.0f}원** "
        f"(누적 {metrics['cumulative_return_pct']:+.2f}%)",
        f"- 청산 이벤트: {metrics['total_trades']}건 · 이벤트 승률 {metrics['win_rate_pct']:.2f}%",
        f"- 진입(trade) 단위: {tu['entries']}건 · 승률 **{tu['win_rate_pct']:.2f}%** ({tu['wins']}승)",
        f"- PF: {metrics['profit_factor']:.2f} · MDD: {metrics['mdd_pct']:.2f}%",
        "- 청산 사유 분포:",
    ]
    for k, v in bd.items():
        lines.append(f"  - {k}: {v}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="v6.28 소생 및 파라미터 재조정 검증")
    p.add_argument(
        "--prewarm", type=int, default=DEFAULT_PREWARM_BARS,
        help=f"히스토리 프리워밍 영업일 (기본 {DEFAULT_PREWARM_BARS}, dynamic_warmup 모드에서 120 강제)",
    )
    p.add_argument(
        "--mode", choices=("dynamic_warmup",), default="dynamic_warmup",
        help="dynamic_warmup: prewarm=120 강제 + 대규모 표본 검증",
    )
    p.add_argument(
        "--universe", choices=("all",), default="all",
        help="all=코스닥 전종목 스캔 (top-n 제한 없음)",
    )
    p.add_argument("--yes", action="store_true", help="확인 질문 생략")
    args = p.parse_args(argv)

    prewarm = int(args.prewarm)
    if args.mode == "dynamic_warmup" and prewarm != DEFAULT_PREWARM_BARS:
        print(
            f"⚠️  dynamic_warmup 모드: --prewarm={prewarm} → "
            f"{DEFAULT_PREWARM_BARS}으로 강제 적용 (워밍업 착시 봉쇄)"
        )
        prewarm = DEFAULT_PREWARM_BARS

    v5 = load_v5_relay_config(section="v5_5")

    print("--- v6.28 소생 및 파라미터 재조정 검증 ---")
    print(f"  기간     : {RELAY_BACKTEST_START} ~ {RELAY_BACKTEST_END} ({len(RELAY_PHASES)}구간)")
    print(f"  모드     : {args.mode} · prewarm={prewarm}영업일")
    print(f"  v6.28 진입: MA20 변곡 + 회전율≥8% + 거래대금≥50억 + 종가/MA20≤1.10 + 양봉")
    print(f"  청산     : v5.5.2 순정 (+8%/-3%/4일)")
    print(f"  유니버스 : 코스닥 전종목 (top-n 제한 없음)")
    print(f"  슬롯     : {v5.portfolio.max_slots} × {v5.portfolio.slot_invest_amount:,.0f}원")

    if not args.yes and not _prompt_yes_no("위 설정으로 v6.28 검증 백테스트를 실행할까요?", default="n"):
        print("취소했습니다.")
        return 0

    print(f"\n🚀 벌크 로딩 ({RELAY_BACKTEST_START} ~ {RELAY_BACKTEST_END})…")
    day_frames, bdays = load_merged_market_day_frames(
        RELAY_BACKTEST_START, RELAY_BACKTEST_END, force_bulk=True
    )
    print(f"📊 벌크 로드 완료: {len(day_frames)} 영업일")

    print("\n🔎 코스닥 종목 마스크 로딩…")
    kosdaq_mask = _load_kosdaq_mask()
    print(f"   코스닥 {len(kosdaq_mask):,}종")

    print("\n📦 일별 시가총액 캐시 구축 (회전율 필터용)…")
    marcap_cache = _build_marcap_cache(bdays, kosdaq_mask)
    shares_fb = _load_shares_fallback(RELAY_BACKTEST_START)
    print(f"   캐시 {len(marcap_cache):,}건 · 상장주식수 폴백 {len(shares_fb):,}종")

    phase_universes: dict[int, frozenset[str]] = {}
    for phase in RELAY_PHASES:
        uni = _phase_universe_all(day_frames, bdays, phase.segment_start, kosdaq_mask)
        phase_universes[phase.phase_id] = uni
        print(f"   구간 {phase.phase_id} 유니버스: {len(uni):,}종")

    manager_kwargs = {
        "prewarm_bars": prewarm,
        "enable_prewarm": True,
        "marcap_by_date_code": marcap_cache,
        "shares_by_code": shares_fb,
    }

    v627_eq, v627_tr, v627_td, v627_m = _run_relay(
        label="V6.27 BASELINE (100억+1.06, +12%/-4%)",
        manager_cls=PortfolioManagerV627,
        day_frames=day_frames,
        bdays=bdays,
        v5=v5,
        phase_universes=phase_universes,
        manager_kwargs=manager_kwargs,
    )
    v628_eq, v628_tr, v628_td, v628_m = _run_relay(
        label="V6.28 ALPHA (50억+1.10, +8%/-3%)",
        manager_cls=PortfolioManagerV628,
        day_frames=day_frames,
        bdays=bdays,
        v5=v5,
        phase_universes=phase_universes,
        manager_kwargs=manager_kwargs,
    )

    os.makedirs(OUT_DIR, exist_ok=True)
    v627_td.to_csv(V627_TRADES_CSV, index=False, encoding="utf-8-sig")
    v628_td.to_csv(V628_TRADES_CSV, index=False, encoding="utf-8-sig")

    v627_tu = _trade_unit_winrate(v627_td)
    v628_tu = _trade_unit_winrate(v628_td)
    delta_wr = v628_tu["win_rate_pct"] - v627_tu["win_rate_pct"]
    delta_ret = v628_m["cumulative_return_pct"] - v627_m["cumulative_return_pct"]
    delta_pf = v628_m["profit_factor"] - v627_m["profit_factor"]
    entry_delta = v628_tu["entries"] - v627_tu["entries"]

    v627_block = _fmt_metrics("V6.27 BASELINE — 100억+1.06 (+12%/-4%)", v627_m, v627_td)
    v628_block = _fmt_metrics("V6.28 ALPHA — 50억+1.10 (+8%/-3%)", v628_m, v628_td)

    report = "\n".join([
        "# v6.28 소생 및 파라미터 재조정 검증 리포트",
        "",
        f"- 기간: {RELAY_BACKTEST_START} ~ {RELAY_BACKTEST_END} ({len(RELAY_PHASES)}구간)",
        f"- 모드: `{args.mode}` · prewarm={prewarm}영업일",
        f"- 유니버스: 코스닥 전종목 (top-n 제한 없음)",
        f"- 슬롯: {v5.portfolio.max_slots} × {v5.portfolio.slot_invest_amount:,.0f}원",
        "",
        "## v6.28 변경 요약 (V6.27 → V6.28)",
        "",
        "| 항목 | V6.27 | V6.28 |",
        "|------|-------|-------|",
        "| 거래대금 절대값 | ≥ 100억 | ≥ 50억 |",
        "| 이격도 (종가/MA20) | ≤ 1.06 | ≤ 1.10 |",
        "| 청산 | +12% / -4% | +8% / -3% (v5.5.2 롤백) |",
        "| 유니버스 | Top-N | 전종목 |",
        "",
        "## 비교 요약",
        "",
        "| 지표 | V6.27 | V6.28 | Δ |",
        "|------|-------|-------|---|",
        f"| 진입 표본 | {v627_tu['entries']} | {v628_tu['entries']} | {entry_delta:+d} |",
        f"| 진입 승률 | {v627_tu['win_rate_pct']:.2f}% | {v628_tu['win_rate_pct']:.2f}% | {delta_wr:+.2f}%p |",
        f"| 누적 수익률 | {v627_m['cumulative_return_pct']:+.2f}% | {v628_m['cumulative_return_pct']:+.2f}% | {delta_ret:+.2f}%p |",
        f"| PF | {v627_m['profit_factor']:.2f} | {v628_m['profit_factor']:.2f} | {delta_pf:+.2f} |",
        f"| MDD | {v627_m['mdd_pct']:.2f}% | {v628_m['mdd_pct']:.2f}% | — |",
        "",
        v627_block,
        v628_block,
        f"- 거래 CSV: `{V628_TRADES_CSV}`",
        "",
    ])
    with open(REPORT_MD, "w", encoding="utf-8") as fh:
        fh.write(report + "\n")

    print("\n" + "=" * 60)
    print("📈 v6.28 검증 결과 요약")
    print("=" * 60)
    print(f"진입 표본   : V6.27 {v627_tu['entries']} → V6.28 {v628_tu['entries']} ({entry_delta:+d})")
    print(f"진입 승률   : {v627_tu['win_rate_pct']:.2f}% → {v628_tu['win_rate_pct']:.2f}% ({delta_wr:+.2f}%p)")
    print(f"누적 수익률 : {v627_m['cumulative_return_pct']:+.2f}% → {v628_m['cumulative_return_pct']:+.2f}% ({delta_ret:+.2f}%p)")
    print(f"PF          : {v627_m['profit_factor']:.2f} → {v628_m['profit_factor']:.2f} ({delta_pf:+.2f})")
    print(f"MDD         : {v627_m['mdd_pct']:.2f}% → {v628_m['mdd_pct']:.2f}%")
    print(f"\n리포트   : {REPORT_MD}")
    print(f"거래 CSV : {V628_TRADES_CSV}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
