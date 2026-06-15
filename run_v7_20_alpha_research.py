"""
v7.2.0 Final Master 검증 러너.

진입: v7.1.0 Pivot 조건 + KOSDAQ 지수 3일선 인터록
청산: +8%/-3%/4일
유니버스: 코스닥 전종목

실행:
  python run_v7_20_alpha_research.py --prewarm 120 --mode dynamic_warmup --universe all --yes
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

from run_v7_10_alpha_research import (  # noqa: E402
    _build_marcap_cache,
    _fmt_metrics,
    _load_kosdaq_mask,
    _load_shares_fallback,
    _phase_universe_all,
    _prompt_yes_no,
    _run_relay,
    _trade_unit_winrate,
)
from src.engine.portfolio_manager import load_merged_market_day_frames  # noqa: E402
from src.engine.portfolio_manager_v626 import DEFAULT_PREWARM_BARS  # noqa: E402
from src.engine.portfolio_manager_v720 import PortfolioManagerV720  # noqa: E402
from src.v5_config import load_v5_relay_config  # noqa: E402
from src.v5_relay_screener import (  # noqa: E402
    RELAY_BACKTEST_END,
    RELAY_BACKTEST_START,
    RELAY_PHASES,
)

OUT_DIR = os.path.join(project_root, "outputs")
ALPHA_TRADES_CSV = os.path.join(OUT_DIR, "v7_20_final_trades.csv")
REPORT_MD = os.path.join(OUT_DIR, "v7_20_final_research_report.md")
KOSDAQ_INDEX_TICKER = "2001"


def _load_kosdaq_index_df(start_date: str, end_date: str, bdays: pd.DatetimeIndex) -> pd.DataFrame:
    """pykrx KOSDAQ 지수 일봉을 로드해 매니저 주입용 close 컬럼으로 정규화."""
    try:
        from pykrx import stock as pykrx_stock  # type: ignore
    except Exception as exc:
        raise RuntimeError("pykrx를 import하지 못해 KOSDAQ 지수를 로드할 수 없습니다.") from exc

    s_ymd = pd.Timestamp(str(start_date).strip()[:10]).strftime("%Y%m%d")
    e_ymd = pd.Timestamp(str(end_date).strip()[:10]).strftime("%Y%m%d")
    raw = pykrx_stock.get_index_ohlcv_by_date(s_ymd, e_ymd, KOSDAQ_INDEX_TICKER)
    if raw is None or getattr(raw, "empty", True):
        raise RuntimeError("KOSDAQ 지수 OHLCV가 비어 있습니다.")

    work = raw.copy()
    if not isinstance(work.index, pd.DatetimeIndex):
        work.index = pd.to_datetime(work.index)
    work.index = work.index.normalize()
    work = work.sort_index()
    rename_map = {
        "시가": "open",
        "고가": "high",
        "저가": "low",
        "종가": "close",
        "거래량": "volume",
        "거래대금": "trading_value",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    }
    work = work.rename(columns={k: v for k, v in rename_map.items() if k in work.columns})
    if "close" not in work.columns:
        raise RuntimeError(f"KOSDAQ 지수 데이터에 close/종가 컬럼이 없습니다: {list(work.columns)}")

    aligned = work.reindex(pd.DatetimeIndex(bdays).normalize()).ffill()
    aligned["close"] = pd.to_numeric(aligned["close"], errors="coerce")
    if aligned["close"].dropna().empty:
        raise RuntimeError("KOSDAQ 지수 close 값이 모두 비어 있습니다.")
    return aligned


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="v7.2.0 Final Master 검증")
    p.add_argument(
        "--prewarm", type=int, default=DEFAULT_PREWARM_BARS,
        help=f"히스토리 프리워밍 영업일 (기본 {DEFAULT_PREWARM_BARS})",
    )
    p.add_argument(
        "--mode", choices=("dynamic_warmup",), default="dynamic_warmup",
        help="dynamic_warmup: prewarm=120 강제",
    )
    p.add_argument(
        "--universe", choices=("all",), default="all",
        help="all=코스닥 전종목 스캔",
    )
    p.add_argument("--yes", action="store_true", help="확인 질문 생략")
    args = p.parse_args(argv)

    prewarm = int(args.prewarm)
    if args.mode == "dynamic_warmup" and prewarm != DEFAULT_PREWARM_BARS:
        print(
            f"dynamic_warmup 모드: --prewarm={prewarm} -> "
            f"{DEFAULT_PREWARM_BARS}으로 강제 적용",
            flush=True,
        )
        prewarm = DEFAULT_PREWARM_BARS

    v5 = load_v5_relay_config(section="v5_5")

    print("--- v7.2.0 Final Master 검증 ---", flush=True)
    print(f"  기간     : {RELAY_BACKTEST_START} ~ {RELAY_BACKTEST_END} ({len(RELAY_PHASES)}구간)", flush=True)
    print(f"  모드     : {args.mode} · prewarm={prewarm}영업일", flush=True)
    print("  진입     : KOSDAQ 3일선 인터록 + 20일 200억 수급 + 낙폭과대 + 브레이크 캔들 + 거래량<30%", flush=True)
    print("  청산     : +8%/-3%/4일", flush=True)
    print("  유니버스 : 코스닥 전종목", flush=True)
    print(f"  슬롯     : {v5.portfolio.max_slots} × {v5.portfolio.slot_invest_amount:,.0f}원", flush=True)

    if not args.yes and not _prompt_yes_no("위 설정으로 v7.2.0 검증 백테스트를 실행할까요?", default="n"):
        print("취소했습니다.")
        return 0

    print(f"\n벌크 로딩 ({RELAY_BACKTEST_START} ~ {RELAY_BACKTEST_END})...", flush=True)
    day_frames, bdays = load_merged_market_day_frames(
        RELAY_BACKTEST_START, RELAY_BACKTEST_END, force_bulk=True
    )
    print(f"벌크 로드 완료: {len(day_frames)} 영업일", flush=True)

    print("\nKOSDAQ 지수 3일선 인터록 데이터 로딩...", flush=True)
    kosdaq_index_df = _load_kosdaq_index_df(RELAY_BACKTEST_START, RELAY_BACKTEST_END, bdays)
    print(f"   지수 일봉 {kosdaq_index_df['close'].notna().sum():,}개", flush=True)

    print("\n코스닥 종목 마스크 로딩...", flush=True)
    kosdaq_mask = _load_kosdaq_mask()
    print(f"   코스닥 {len(kosdaq_mask):,}종", flush=True)

    print("\n일별 시가총액 캐시 구축...", flush=True)
    marcap_cache = _build_marcap_cache(bdays, kosdaq_mask)
    shares_fb = _load_shares_fallback(RELAY_BACKTEST_START)
    print(f"   캐시 {len(marcap_cache):,}건 · 상장주식수 폴백 {len(shares_fb):,}종", flush=True)

    phase_universes: dict[int, frozenset[str]] = {}
    for phase in RELAY_PHASES:
        uni = _phase_universe_all(day_frames, bdays, phase.segment_start, kosdaq_mask)
        phase_universes[phase.phase_id] = uni
        print(f"   구간 {phase.phase_id} 유니버스: {len(uni):,}종", flush=True)

    alpha_kwargs = {
        "prewarm_bars": prewarm,
        "enable_prewarm": True,
        "marcap_by_date_code": marcap_cache,
        "shares_by_code": shares_fb,
        "kosdaq_index_df": kosdaq_index_df,
    }

    alpha_eq, alpha_tr, alpha_td, alpha_m = _run_relay(
        label="V7.2.0 FINAL MASTER (KOSDAQ 3일선 인터록 + Pivot)",
        manager_cls=PortfolioManagerV720,
        day_frames=day_frames,
        bdays=bdays,
        v5=v5,
        phase_universes=phase_universes,
        manager_kwargs=alpha_kwargs,
    )

    os.makedirs(OUT_DIR, exist_ok=True)
    alpha_td.to_csv(ALPHA_TRADES_CSV, index=False, encoding="utf-8-sig")

    alpha_tu = _trade_unit_winrate(alpha_td)
    pf_target_ok = alpha_m["profit_factor"] >= 1.5
    alpha_block = _fmt_metrics(
        "V7.2.0 FINAL MASTER — KOSDAQ 3일선 인터록 + Pivot",
        alpha_m,
        alpha_td,
    )

    report = "\n".join([
        "# v7.2.0 Final Master 검증 리포트",
        "",
        f"- 기간: {RELAY_BACKTEST_START} ~ {RELAY_BACKTEST_END} ({len(RELAY_PHASES)}구간)",
        f"- 모드: `{args.mode}` · prewarm={prewarm}영업일",
        "- 유니버스: 코스닥 전종목",
        f"- 슬롯: {v5.portfolio.max_slots} × {v5.portfolio.slot_invest_amount:,.0f}원",
        "",
        "## v7.2.0 파이널 마스터 명세",
        "",
        "| 조건 | 내용 |",
        "|------|------|",
        "| 시장 인터록 | KOSDAQ 종가 ≥ KOSDAQ 3일 이동평균선일 때만 신규 매수 허용 |",
        "| 수급 메모리 | 최근 20영업일 최고 거래대금 ≥ 200억 |",
        "| 낙폭과대 | RSI(14)≤30 OR 종가≤MA20×0.90 |",
        "| 브레이크 확인 | 양봉(종가>시가) OR 아랫꼬리>몸통 |",
        "| 거래량 급감 | 당일 거래량 < 최근 20일 최대×30% |",
        "| 청산 | +8% / -3% / 4일 |",
        "",
        "## 결과 요약",
        "",
        f"- 진입 표본: {alpha_tu['entries']}건",
        f"- 진입 승률: {alpha_tu['win_rate_pct']:.2f}% ({alpha_tu['wins']}승)",
        f"- 누적 수익률: {alpha_m['cumulative_return_pct']:+.2f}%",
        f"- PF: {alpha_m['profit_factor']:.2f}",
        f"- MDD: {alpha_m['mdd_pct']:.2f}%",
        "",
        alpha_block,
        "## Sign-off",
        "",
        f"- PF 목표 (≥1.5): V7.2.0 PF **{alpha_m['profit_factor']:.2f}** "
        f"({'달성' if pf_target_ok else '미달'})",
        "- 지수 차단 로그: 실행 로그에서 `[MARKET INTERCEPT]` 라인 확인",
        "",
        f"- 거래 CSV: `{ALPHA_TRADES_CSV}`",
        "",
    ])
    with open(REPORT_MD, "w", encoding="utf-8") as fh:
        fh.write(report + "\n")

    print("\n" + "=" * 60, flush=True)
    print("v7.2.0 Final Master 검증 결과 요약", flush=True)
    print("=" * 60, flush=True)
    print(f"진입 표본   : {alpha_tu['entries']}", flush=True)
    print(f"진입 승률   : {alpha_tu['win_rate_pct']:.2f}% ({alpha_tu['wins']}승)", flush=True)
    print(f"누적 수익률 : {alpha_m['cumulative_return_pct']:+.2f}%", flush=True)
    print(f"PF          : {alpha_m['profit_factor']:.2f}", flush=True)
    print(f"MDD         : {alpha_m['mdd_pct']:.2f}%", flush=True)
    print(f"PF 목표     : {'달성' if pf_target_ok else '미달'} (>=1.5)", flush=True)
    print(f"\n리포트   : {REPORT_MD}", flush=True)
    print(f"거래 CSV : {ALPHA_TRADES_CSV}", flush=True)
    print("=" * 60, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
