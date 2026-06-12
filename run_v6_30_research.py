"""
v6.30 연구 러너 (A+B+D) — 승률 개선 가설 검증.

레버 A: 코스닥 전종목 거래대금 상위 N 동적 후보 → 대규모 표본 확보(300건+).
레버 B: 익일 시가 갭다운 -2.5% 즉시 청산.
레버 D: 부분익절(+4% 50%) + 본전 이동 + 트레일링(+8% arm, -2% giveback).

⚠️ v5.5.2 SSOT 불가침. 본 러너는 연구 결과만 outputs/ 에 별도 저장한다.
비교군:
  · BASELINE_A : 레버 A(확대 표본)만, 청산은 v5.5.2 원본(+8%/-3%/4일)
  · V6_30_ABD  : 레버 A + B + D 통합

실행: python run_v6_30_research.py            (확인 후 실행)
      python run_v6_30_research.py --yes      (무질문)
      python run_v6_30_research.py --top-n 200
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

from src.data_loader import fetch_filtered_universe  # noqa: E402
from src.engine.portfolio_manager import (  # noqa: E402
    PortfolioManager as _PortfolioManagerV4,
    load_merged_market_day_frames,
)
from src.engine.portfolio_manager_v630 import (  # noqa: E402
    LeverConfig,
    PortfolioManagerV630,
)
from src.v5_config import load_v5_relay_config  # noqa: E402
from src.v5_relay_screener import (  # noqa: E402
    RELAY_BACKTEST_END,
    RELAY_BACKTEST_START,
    RELAY_PHASES,
    _resolve_universe_dir,
    load_relay_universe_codes,
)

OUT_DIR = os.path.join(project_root, "outputs")
ABD_TRADES_CSV = os.path.join(OUT_DIR, "v6_30_abd_trades.csv")
BASE_TRADES_CSV = os.path.join(OUT_DIR, "v6_30_baseline_trades.csv")
REPORT_MD = os.path.join(OUT_DIR, "v6_30_research_report.md")


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


def _phase_universe(day_frames, bdays, segment_start, mask, top_n) -> frozenset[str]:
    """레버 A: 구간 시작 직전 영업일의 코스닥 거래대금 상위 N종 = 구간 고정 유니버스.

    구간별 고정 유니버스라야 stock_history가 정상 누적되어 MA120 워밍업이 가능.
    """
    sd = pd.Timestamp(segment_start).normalize()
    pos = int(bdays.get_indexer([sd], method="bfill")[0])
    ref = max(0, pos - 1)
    frame = day_frames[ref]
    rows: list[tuple[str, float]] = []
    for code in frame.index:
        c6 = str(code).zfill(6)
        if c6 not in mask:
            continue
        try:
            close = float(frame.loc[code, "Close"])
            vol = float(frame.loc[code, "Volume"])
        except Exception:
            continue
        if not np.isfinite(close) or not np.isfinite(vol) or close <= 0:
            continue
        rows.append((c6, close * vol))
    rows.sort(key=lambda x: x[1], reverse=True)
    return frozenset(c for c, _ in rows[:top_n])


def _run_relay(
    *,
    label: str,
    day_frames,
    bdays,
    v5,
    lever: LeverConfig,
    phase_universes: dict[int, frozenset[str]],
    enable_prewarm: bool = True,
):
    """7구간 릴레이 실행 — 자산 이월 · 구간말 PERIOD_RESET. (merged_equity, merged_trades, merged_detail) 반환."""
    cash = float(v5.environment.initial_cash)
    base_initial = cash
    trade_id_offset = 0
    all_equity: list[pd.DataFrame] = []
    all_trades: list[pd.DataFrame] = []
    all_detail: list[pd.DataFrame] = []

    print(f"\n=== [{label}] 7구간 릴레이 시작 (초기 {base_initial:,.0f}원) ===")
    for phase in RELAY_PHASES:
        phase_uni = phase_universes[phase.phase_id]
        manager = PortfolioManagerV630(
            day_frames,
            bdays,
            start_date=phase.segment_start,
            end_date=phase.segment_end,
            v5_config=v5,
            target_universe=phase_uni,     # 레버 A: 구간별 거래대금 상위 N 고정 유니버스
            starting_cash=cash,
            period_end_date=phase.segment_end,
            trade_id_offset=trade_id_offset,
            lever=lever,
            enable_prewarm=enable_prewarm,
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
    """진입(trade_id) 단위 승률 — 부분청산을 한 거래로 합산."""
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
    p = argparse.ArgumentParser(description="v6.30 연구 백테스트 (A+B+D)")
    p.add_argument("--top-n", type=int, default=200, help="구간별 거래대금 상위 N (레버 A, wide 모드)")
    p.add_argument(
        "--universe", choices=("wide", "narrow"), default="wide",
        help="wide=코스닥 거래대금 상위 N(레버 A) · narrow=v5.5.2 릴레이 정예 유니버스",
    )
    p.add_argument("--yes", action="store_true", help="확인 질문 생략")
    p.add_argument(
        "--no-prewarm", action="store_true",
        help="히스토리 프리워밍 비활성(기존 v5 릴레이 워밍업 부족 재현 검증용)",
    )
    args = p.parse_args(argv)

    v5 = load_v5_relay_config(section="v5_5")  # 진입 조건·비용·슬롯은 v5.5.2 SSOT 그대로
    is_wide = args.universe == "wide"

    print("--- v6.30 연구 백테스트 (A+B+D) ---")
    print(f"  기간     : {RELAY_BACKTEST_START} ~ {RELAY_BACKTEST_END} ({len(RELAY_PHASES)}구간)")
    print(f"  진입     : v5.5.2 (MA20 변곡 + 듀얼 MA60/120↑ + 종가>MA120)")
    if is_wide:
        print(f"  레버 A   : 코스닥 전종목 거래대금 상위 {args.top_n}종 (구간별 고정)")
    else:
        print(f"  유니버스 : v5.5.2 릴레이 정예 (Top{v5.screener.top_n}) — 레버 A 미적용")
    print(f"  레버 B   : 익일 시가 -2.5% 갭다운 즉시 청산")
    print(f"  레버 D   : +4% 50% 부분익절 → 본전이동 → +8% arm/-2% 트레일링")
    print(f"  슬롯     : {v5.portfolio.max_slots} × {v5.portfolio.slot_invest_amount:,.0f}원")

    if not args.yes and not _prompt_yes_no("위 설정으로 연구 백테스트를 실행할까요?", default="n"):
        print("취소했습니다.")
        return 0

    print(f"\n🚀 벌크 로딩 ({RELAY_BACKTEST_START} ~ {RELAY_BACKTEST_END})…")
    day_frames, bdays = load_merged_market_day_frames(
        RELAY_BACKTEST_START, RELAY_BACKTEST_END, force_bulk=True
    )
    print(f"📊 벌크 로드 완료: {len(day_frames)} 영업일")

    # 구간별 유니버스 산출
    phase_universes: dict[int, frozenset[str]] = {}
    if is_wide:
        print("\n🔎 코스닥 종목 마스크 로딩…")
        mask = _load_kosdaq_mask()
        print(f"   코스닥 {len(mask):,}종")
        for phase in RELAY_PHASES:
            phase_universes[phase.phase_id] = _phase_universe(
                day_frames, bdays, phase.segment_start, mask, args.top_n
            )
    else:
        universe_dir = _resolve_universe_dir(v5, project_root)
        for phase in RELAY_PHASES:
            codes = load_relay_universe_codes(phase, universe_dir=universe_dir)
            phase_universes[phase.phase_id] = frozenset(str(c).zfill(6) for c in codes)

    lever_off = LeverConfig(gap_down_enabled=False, partial_tp_enabled=False)
    lever_on = LeverConfig()  # 작업지시서 v6.30 기본값

    prewarm = not args.no_prewarm
    base_eq, base_tr, base_td, base_m = _run_relay(
        label="BASELINE (청산 v5.5.2 원본)",
        day_frames=day_frames, bdays=bdays, v5=v5,
        lever=lever_off, phase_universes=phase_universes, enable_prewarm=prewarm,
    )
    abd_eq, abd_tr, abd_td, abd_m = _run_relay(
        label="V6_30 (B+D 적용)",
        day_frames=day_frames, bdays=bdays, v5=v5,
        lever=lever_on, phase_universes=phase_universes, enable_prewarm=prewarm,
    )

    os.makedirs(OUT_DIR, exist_ok=True)
    base_td.to_csv(BASE_TRADES_CSV, index=False, encoding="utf-8-sig")
    abd_td.to_csv(ABD_TRADES_CSV, index=False, encoding="utf-8-sig")

    base_block = _fmt_metrics("BASELINE_A — 레버 A만 (청산 v5.5.2 원본)", base_m, base_td)
    abd_block = _fmt_metrics("V6.30 — A+B+D 통합", abd_m, abd_td)

    base_tu = _trade_unit_winrate(base_td)
    abd_tu = _trade_unit_winrate(abd_td)
    delta_wr = abd_tu["win_rate_pct"] - base_tu["win_rate_pct"]
    delta_ret = abd_m["cumulative_return_pct"] - base_m["cumulative_return_pct"]

    report = "\n".join([
        "# v6.30 연구 리포트 (A+B+D)",
        "",
        f"- 기간: {RELAY_BACKTEST_START} ~ {RELAY_BACKTEST_END} ({len(RELAY_PHASES)}구간)",
        f"- 레버 A: 코스닥 전종목 거래대금 상위 {args.top_n}종/일",
        f"- 슬롯: {v5.portfolio.max_slots} × {v5.portfolio.slot_invest_amount:,.0f}원",
        "",
        "## 비교 요약",
        "",
        "| 지표 | BASELINE_A | V6.30 A+B+D | Δ |",
        "|------|-----------|-------------|---|",
        f"| 진입 표본 | {base_tu['entries']} | {abd_tu['entries']} | — |",
        f"| 진입 승률 | {base_tu['win_rate_pct']:.2f}% | {abd_tu['win_rate_pct']:.2f}% | {delta_wr:+.2f}%p |",
        f"| 누적 수익률 | {base_m['cumulative_return_pct']:+.2f}% | {abd_m['cumulative_return_pct']:+.2f}% | {delta_ret:+.2f}%p |",
        f"| PF | {base_m['profit_factor']:.2f} | {abd_m['profit_factor']:.2f} | — |",
        f"| MDD | {base_m['mdd_pct']:.2f}% | {abd_m['mdd_pct']:.2f}% | — |",
        "",
        base_block,
        abd_block,
    ])
    with open(REPORT_MD, "w", encoding="utf-8") as fh:
        fh.write(report + "\n")

    print("\n" + "=" * 60)
    print("📈 v6.30 연구 결과 요약")
    print("=" * 60)
    print(f"진입 표본   : BASELINE {base_tu['entries']} → A+B+D {abd_tu['entries']}")
    print(f"진입 승률   : {base_tu['win_rate_pct']:.2f}% → {abd_tu['win_rate_pct']:.2f}% ({delta_wr:+.2f}%p)")
    print(f"누적 수익률 : {base_m['cumulative_return_pct']:+.2f}% → {abd_m['cumulative_return_pct']:+.2f}% ({delta_ret:+.2f}%p)")
    print(f"PF          : {base_m['profit_factor']:.2f} → {abd_m['profit_factor']:.2f}")
    print(f"MDD         : {base_m['mdd_pct']:.2f}% → {abd_m['mdd_pct']:.2f}%")
    print(f"\n리포트   : {REPORT_MD}")
    print(f"거래 CSV : {ABD_TRADES_CSV}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
