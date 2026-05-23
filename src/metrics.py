"""
누적·CAGR·MDD(소수 둘째 자리)·`run_backtest_detailed` 파이프라인.
차트 Figure·PNG 는 `backtest_chart` 모듈 (GUI/Tkinter 비의존).

v4.0: 매수 진입 필터(120선 회귀 기울기·돌파 강도·시간 버퍼);
v4.1: 사용자 시작일 이전 거래일 130봉분 일봉 OHLCV 선행 로드(주봉은 캘린더 버퍼)·YAML 빈 기간 시 실행 시점 6개월~오늘;
v4.4: 수익률 구간별 가변 고점 대비 낙폭 매도(`simulator.simulate_single` trailing_stop)·차트 타점 색 구분;
v4.6: 매매 규칙 분리(`golden_buy_enabled`·`dead_cross_sell_enabled`) — 매수 후보 필터 AND·매도 트레일/데크 OR(strategy·simulator);
v4.5: 차트 내 `ax.legend` 범례 매립·GUI 외부 범례 제거;
v3.5 타점 미매칭 알림·v3.4 날짜 엄격 매칭·v3.3 타점 스타일.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .backtest_chart import (
    make_backtest_figure,
    save_backtest_report_png,
    save_figure_as_png,
)
from .backtest_constants import FIG_ATTR_TRADE_MARKERS_SKIPPED, TREND_MA_PERIODS
from .data_loader import (
    default_backtest_period_range,
    ensure_datetime_index,
    fetch_filtered_universe,
    load_ohlcv,
    normalize_krx_listing_market,
    ohlcv_warm_start_date,
    resample_weekly_ohlcv,
)
from .simulator import simulate_single
from .strategy import add_entry_filter_columns, add_signals

# v4.0 매수 필터 사용 시 MA120·회귀 창 등 최소 봉 수 가드
MIN_BARS_FOR_ENTRY_FILTERS = 130


def strategy_entry_filters_from_cfg(st: dict) -> dict[str, bool | float]:
    """GUI/YAML strategy 블록 → simulate_single entry_filters."""
    return {
        "filter_trend_slope": bool(st.get("filter_trend_slope", False)),
        "slope_threshold": float(st.get("slope_threshold", 0.01)),
        "filter_breakout_strength": bool(st.get("filter_breakout_strength", False)),
        "filter_time_buffer": bool(st.get("filter_time_buffer", False)),
        "harness_buy_all_three_and": bool(st.get("harness_buy_all_three_and", False)),
        "use_slope_acceleration": bool(st.get("use_slope_acceleration", False)),
    }


def strategy_trailing_stop_from_cfg(st: dict) -> dict[str, bool | float]:
    """strategy 블록 → simulate_single trailing_stop kwargs."""
    return {
        "enabled": bool(st.get("trailing_stop_enabled", False)),
        "trailing_reference_pct": float(st.get("trailing_reference_pct", 10.0)),
        "trailing_drop_below_pct": float(st.get("trailing_drop_below_pct", 3.0)),
        "trailing_drop_above_pct": float(st.get("trailing_drop_above_pct", 5.0)),
    }


def strategy_cross_flags_from_cfg(st: dict) -> dict[str, bool]:
    """기본 크로스 스위치 — `strategy.add_signals`·`simulate_single` 데크 매도 실행과 동기화."""
    return {
        "golden_buy_enabled": bool(st.get("golden_buy_enabled", True)),
        "dead_cross_sell_enabled": bool(st.get("dead_cross_sell_enabled", True)),
    }


@dataclass
class BacktestResult:
    ok: bool
    error: str | None
    summary_rows: list[list[str]]
    report_path: str | None
    log_lines: list[str]
    replay_chart: dict | None = None
    n_buy: int = 0
    n_sell: int = 0
    trade_markers_skipped: int = 0


def _listing_display_name_resolve(
    code: str, preferred_market: object
) -> str | None:
    """v4.8: 우선 상장 시장에서 코드 조회 후, 교차 시장 순회(FDR 목록 한계 완화)."""
    cdf = str(code or "").strip().zfill(6)
    pref = normalize_krx_listing_market(preferred_market) or "KOSPI"
    for m in (pref,) + tuple(x for x in ("KOSPI", "KOSDAQ", "ETF") if x != pref):
        u = fetch_filtered_universe(m, "")
        if cdf in u:
            return str(u[cdf])
    return None


def normalize_interval(s: str) -> str:
    x = (s or "daily").strip().lower()
    if x in ("d", "day", "daily", "일", "일봉"):
        return "daily"
    if x in ("w", "week", "weekly", "주", "주봉"):
        return "weekly"
    raise ValueError(f"지원하지 않는 interval: {s} (daily 또는 weekly)")


def metrics_total_cagr_mdd_equity(
    equity: pd.Series, initial: float, bars_per_year: float
):
    """누적수익률(%), CAGR(%), MDD(%), 수익률 시리즈(%)."""
    ret_pct = (equity / float(initial) - 1.0) * 100.0
    n = len(equity)
    if n < 2:
        return 0.0, 0.0, 0.0, ret_pct

    total_ret = float(ret_pct.iloc[-1])
    years = n / float(bars_per_year)
    if years <= 0:
        cagr_pct = 0.0
    else:
        ratio = float(equity.iloc[-1]) / float(initial)
        cagr_pct = (ratio ** (1.0 / years) - 1.0) * 100.0

    peak_eq = equity.cummax()
    dd = np.where(peak_eq > 1e-12, (peak_eq - equity) / peak_eq, 0.0)
    mdd_pct = float(np.nanmax(dd)) * 100.0 if len(dd) else 0.0

    return total_ret, cagr_pct, mdd_pct, ret_pct


def trend_overlay_flags_from_strategy(st: dict) -> dict[int, bool]:
    """차트 추세선 6종 표시 여부. 신규 키 show_trend_ma{기간} 우선, 없으면 구 show_ma120/200."""
    flags: dict[int, bool] = {}
    for p in TREND_MA_PERIODS:
        k = f"show_trend_ma{p}"
        if k in st:
            flags[p] = bool(st[k])
        elif p == 120:
            flags[p] = bool(st.get("show_ma120", False))
        elif p == 200:
            flags[p] = bool(st.get("show_ma200", False))
        else:
            flags[p] = False
    return flags


def rolling_trend_ma_series(close: pd.Series, period: int) -> pd.Series:
    """추세 오버레이용 이평 (짧은 기간은 min_periods 완화)."""
    min_periods = 2 if period <= 10 else min(20, period)
    return close.rolling(period, min_periods=min_periods).mean()


def run_backtest_detailed(
    cfg: dict,
    override_code: str | None = None,
    embed_figure: bool = False,
    *,
    ohlcv_preloaded_daily: pd.DataFrame | None = None,
    omit_report_artifacts: bool = False,
) -> BacktestResult:
    """설정 dict 기준 전체 백테스트. GUI·CLI 공용."""
    lines: list[str] = []
    period = cfg.get("period", {})
    start = period.get("start_date")
    end = period.get("end_date")

    if not str(start or "").strip() or not str(end or "").strip():
        s_d, e_d = default_backtest_period_range()
        if not str(start or "").strip():
            start = s_d.strftime("%Y-%m-%d")
        if not str(end or "").strip():
            end = e_d.strftime("%Y-%m-%d")
    uni = cfg.get("universe", {})
    keyword = uni.get("search_keyword", "") or ""
    market_key = normalize_krx_listing_market(uni.get("market", "KOSPI")) or "KOSPI"
    selected = (override_code or uni.get("selected_code") or "").strip().zfill(6)

    st = cfg.get("strategy", {})
    ma_n = int(st.get("ma_period", 20))
    interval = normalize_interval(str(st.get("interval", "daily")))
    trend_flags = trend_overlay_flags_from_strategy(st)
    show_chart_candle = bool(st.get("show_chart_candle", True))
    show_chart_volume = bool(st.get("show_chart_volume", True))
    # v4.7: 하단 누적수익률 패널 제거 — 레거시 show_chart_return 은 무시
    show_return_overlay = bool(st.get("show_return_overlay", False))

    costs = cfg.get("trading_costs", {})
    buy_c = float(costs.get("buy_cost", 0.00015))
    sell_c = float(costs.get("sell_cost", 0.0018))

    port = cfg.get("portfolio", {})
    initial = float(port.get("initial_cash", 5_000_000))

    if not selected or selected == "000000":
        return BacktestResult(
            False,
            "종목을 선택하세요 (리스트에서 1개).",
            [],
            None,
            lines,
        )

    candidates_kw = fetch_filtered_universe(market_key, keyword)
    if selected in candidates_kw:
        name = candidates_kw[selected]
    else:
        universe_all = fetch_filtered_universe(market_key, "")
        if selected not in universe_all:
            name = _listing_display_name_resolve(selected, market_key)
            if name is None:
                warm_probe = ohlcv_warm_start_date(str(start), interval=interval)
                probe = load_ohlcv(selected, warm_probe, str(end))
                if probe is None or probe.empty:
                    return BacktestResult(
                        False,
                        (
                            f"코드 {selected} 은(는) 시장 '{market_key}' 상장 목록에서 찾지 못했으며, "
                            "가격 데이터도 로드하지 못했습니다. 종목 코드·거래 가능 여부를 확인하세요."
                        ),
                        [],
                        None,
                        lines,
                    )
                name = str(selected).zfill(6)
        else:
            name = universe_all[selected]
    bar_label = "주봉" if interval == "weekly" else "일봉"
    bars_per_year = 52.0 if interval == "weekly" else 252.0

    lines.append(
        f"[시작] {start} ~ {end} | {name} ({selected}) | {bar_label} | 이평 {ma_n} | 초기 {initial:,.0f}원 전액"
    )
    overlay_on = [p for p in TREND_MA_PERIODS if trend_flags[p]]
    if overlay_on:
        unit = "봉(주봉)" if bar_label == "주봉" else "일"
        lines.append(
            f"[차트] 추세선 오버레이: {', '.join(str(p) for p in overlay_on)}{unit} 이평"
        )

    warm = ohlcv_warm_start_date(str(start), interval=interval)

    raw: pd.DataFrame | None
    if ohlcv_preloaded_daily is not None:
        raw_full = ensure_datetime_index(ohlcv_preloaded_daily.copy())
        warm_ts = pd.Timestamp(str(warm))
        end_ts = pd.Timestamp(str(end))
        raw = raw_full.loc[
            (raw_full.index >= warm_ts) & (raw_full.index <= end_ts)
        ]
        if raw.empty:
            raw = load_ohlcv(selected, warm, str(end))
    else:
        raw = load_ohlcv(selected, warm, str(end))

    if raw is None:
        return BacktestResult(
            False, "데이터 로드 실패 또는 가격 데이터가 없습니다.", [], None, lines
        )

    raw = ensure_datetime_index(raw)
    if interval == "weekly":
        bars = resample_weekly_ohlcv(raw)
    else:
        bars = raw

    if len(bars) < ma_n + 5:
        return BacktestResult(
            False,
            "봉 데이터가 너무 적습니다. 기간이나 이평 N을 확인하세요.",
            [],
            None,
            lines,
        )

    entry_ef = strategy_entry_filters_from_cfg(st)
    harness_buy = bool(entry_ef.get("harness_buy_all_three_and", False))
    any_entry_filter = harness_buy or any(
        (
            entry_ef["filter_trend_slope"],
            entry_ef["filter_breakout_strength"],
            entry_ef["filter_time_buffer"],
            entry_ef["use_slope_acceleration"],
        )
    )
    if any_entry_filter and len(bars) < MIN_BARS_FOR_ENTRY_FILTERS:
        return BacktestResult(
            False,
            f"매수 진입 필터 사용 시 데이터가 부족합니다(권장 최소 {MIN_BARS_FOR_ENTRY_FILTERS}봉 이상). 기간을 넓히세요.",
            [],
            None,
            lines,
        )

    cross_flags = strategy_cross_flags_from_cfg(st)

    if any_entry_filter and not harness_buy:
        parts: list[str] = []
        if entry_ef["filter_trend_slope"]:
            parts.append(f"대세기울기≥{entry_ef['slope_threshold']}")
        if entry_ef["filter_breakout_strength"]:
            parts.append("돌파강도")
        if entry_ef["filter_time_buffer"]:
            parts.append("시간버퍼")
        if entry_ef["use_slope_acceleration"]:
            parts.append("곡선가속도(MA20 OLS 기울기>0)")
        lines.append("[전략 v4.0] 매수 진입 필터(골던 후보 AND): " + ", ".join(parts))
    elif harness_buy:
        hb = (
            "[Harness 매수] 골든 후 대세·돌파·시간버퍼 세 조건 **동시 만족(AND)** "
            "(개별 필터 스위치와 무관)."
        )
        if entry_ef["use_slope_acceleration"]:
            hb += " + 곡선가속도(MA20 OLS 기울기>0)."
        lines.append(hb)

    lines.append(
        "[매도] 트레일(활성 시) 종가 신호 또는 데드(옵션) 중 **어느 한쪽이라도 충족 시** 다음 봉 시가 청산 시뮬(OR)."
    )
    if not cross_flags["golden_buy_enabled"]:
        lines.append(
            "[전략 v4.6] 골든 매수 OFF — 매매 기준 이평 골든크로스 매수 신호 없음"
        )
    if not cross_flags["dead_cross_sell_enabled"]:
        lines.append(
            "[전략 v4.6] 데드 매도 OFF — 데드크로스 신호 기반 매도 체결 없음(트레일만 가능)"
        )

    trail_cfg = strategy_trailing_stop_from_cfg(st)
    if trail_cfg["enabled"]:
        lines.append(
            "[전략 v4.4] 가변 낙폭 매도: "
            f"기준 {trail_cfg['trailing_reference_pct']}% 미만 피크 → "
            f"고점 대비 {trail_cfg['trailing_drop_below_pct']}%, "
            f"이상 도달 시 고점 대비 {trail_cfg['trailing_drop_above_pct']}%"
        )

    sig_df = add_entry_filter_columns(
        add_signals(
            bars,
            ma_n,
            golden_buy_enabled=cross_flags["golden_buy_enabled"],
            dead_cross_sell_enabled=cross_flags["dead_cross_sell_enabled"],
        )
    )
    res = simulate_single(
        sig_df,
        str(start),
        initial,
        buy_c,
        sell_c,
        entry_filters=entry_ef,
        trailing_stop=trail_cfg,
        dead_cross_sell_enabled=cross_flags["dead_cross_sell_enabled"],
    )
    if res is None:
        return BacktestResult(False, "시뮬 구간이 너무 짧습니다.", [], None, lines)
    sim, trades = res

    eq = sim["Equity"]
    total_r, cagr_r, mdd_r, ret_series = metrics_total_cagr_mdd_equity(
        eq, initial, bars_per_year
    )
    final_eq = float(eq.iloc[-1])
    ret_nums = pd.to_numeric(ret_series, errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    ).dropna()
    if ret_nums.empty:
        hi_pct, lo_pct = 0.0, 0.0
    else:
        hi_pct = float(ret_nums.max())
        lo_pct = float(ret_nums.min())
    hl_line = f"{hi_pct:+.2f} % / {lo_pct:+.2f} %"

    summary = [
        ["종목", f"{name} ({selected})"],
        ["봉 주기", bar_label],
        ["초기 자산", f"{initial:,.2f} 원"],
        ["최종 평가액", f"{final_eq:,.2f} 원"],
        ["누적 수익률", f"{total_r:.2f} %"],
        ["최고/최저 수익률", hl_line],
        ["연평균 수익률", f"{cagr_r:.2f} %"],
        ["최대 손실 낙폭", f"{mdd_r:.2f} %"],
    ]

    n_buy = sum(1 for t in trades if t["side"] == "BUY")
    n_sell = sum(1 for t in trades if t["side"] == "SELL")

    if omit_report_artifacts:
        trend_plot_rb: dict[int, pd.Series] | None = None
        if embed_figure:
            full_close_rb = sig_df["Close"].astype(float)
            trend_ma_rb: dict[int, pd.Series] = {}
            for p in TREND_MA_PERIODS:
                if not trend_flags.get(p):
                    continue
                trend_ma_rb[p] = rolling_trend_ma_series(full_close_rb, p)
            trend_plot_rb = (
                {p: s.reindex(sim.index) for p, s in trend_ma_rb.items()}
                if trend_ma_rb
                else None
            )

        replay_chart_rb: dict | None = None
        if embed_figure:
            replay_chart_rb = {
                "sim": sim,
                "trades": trades,
                "name": name,
                "bar_label": bar_label,
                "ma_n": ma_n,
                "ret_series": ret_series,
                "trend_ma": trend_plot_rb,
                "show_chart_candle": show_chart_candle,
                "show_chart_volume": show_chart_volume,
                "show_return_overlay": show_return_overlay,
            }

        lines.append(
            f"[그래프] 생략(omit_report_artifacts 배치 모드) — 매수 {n_buy}회 / 매도 {n_sell}회"
        )

        return BacktestResult(
            True,
            None,
            summary,
            None,
            lines,
            replay_chart=replay_chart_rb,
            n_buy=n_buy,
            n_sell=n_sell,
            trade_markers_skipped=0,
        )

    full_close = sig_df["Close"].astype(float)
    trend_ma: dict[int, pd.Series] = {}
    for p in TREND_MA_PERIODS:
        if not trend_flags.get(p):
            continue
        trend_ma[p] = rolling_trend_ma_series(full_close, p)
    trend_plot = (
        {p: s.reindex(sim.index) for p, s in trend_ma.items()} if trend_ma else None
    )

    out_png = os.path.join("output", "backtest_report.png")
    fig = make_backtest_figure(
        sim,
        trades,
        name,
        bar_label,
        ma_n,
        ret_series,
        trend_ma=trend_plot,
        show_candle=show_chart_candle,
        show_volume=show_chart_volume,
        show_return_overlay=show_return_overlay,
    )
    trade_markers_skipped = int(getattr(fig, FIG_ATTR_TRADE_MARKERS_SKIPPED, 0))
    if trade_markers_skipped > 0:
        lines.append(
            f"[CRITICAL] 차트 타점 {trade_markers_skipped}건이 OHLCV 인덱스와 날짜 매칭 실패로 생략되었습니다. "
            "체결일·차트 구간·타임존·normalize 를 재점검하세요."
        )

    save_figure_as_png(fig, out_png)
    plt.close(fig)

    # 디버그 검증 로그 생성 (backtest_signal_debug.txt)
    debug_log_path = "backtest_signal_debug.txt"
    try:
        with open(debug_log_path, "w", encoding="utf-8") as f:
            f.write("=====================================================\n")
            f.write("[시뮬레이터 매매 신호 및 차트 마킹 동기화 검증 로그]\n")
            f.write(f"종목: {name} ({selected})\n\n")

            idx = sim.index
            buy_idx = 0
            sell_idx = 0

            for t in trades:
                side = t["side"]
                if side == "BUY":
                    buy_idx += 1
                    label = f"매수 {buy_idx}번"
                else:
                    sell_idx += 1
                    label = f"매도 {sell_idx}번"

                # 체결일 인덱스
                trade_ts = pd.Timestamp(t["date"]).normalize()
                idx_norm = idx.normalize()
                pos = idx_norm.get_indexer([trade_ts], method=None)
                if pos.size == 0 or int(pos[0]) < 0:
                    continue
                bi_exec = int(pos[0])
                bi_signal = bi_exec - 1
                if bi_signal < 0:
                    continue

                t_date_str = idx[bi_signal].strftime("%Y-%m-%d")
                exec_date_str = idx[bi_exec].strftime("%Y-%m-%d")

                # 캔들 형태 판단 (T일)
                op = float(sim["Open"].iloc[bi_signal])
                cl = float(sim["Close"].iloc[bi_signal])
                pct = (cl - op) / op if op > 0 else 0
                if pct < -0.03:
                    candle_desc = "장대음봉"
                elif pct > 0.03:
                    candle_desc = "장대양봉"
                elif pct < 0:
                    candle_desc = "음봉"
                elif pct > 0:
                    candle_desc = "양봉"
                else:
                    candle_desc = "도지"

                marked_date = t.get("marked_date", exec_date_str)

                error_suffix = ""
                if marked_date != t_date_str:
                    error_suffix = "   [오류: 인덱스 1칸 밀림 발생]"

                f.write(f" [{label}]\n\n")
                f.write(
                    f"전략 판단 신호 발생일 (T일 종가): {t_date_str} ({candle_desc})\n\n"
                )
                f.write(f"실제 차트 마킹 적용일 (정상 위치): {t_date_str}\n\n")
                f.write(
                    f"현재 차트 플로팅 인덱스 날짜   : {marked_date}{error_suffix}\n\n"
                )
                f.write(f"실제 체결 집행일 (T+1일 시가)  : {exec_date_str}\n")
                f.write("=====================================================\n\n")
    except Exception as e:
        import sys

        print(f"[ERROR] 검증 로그 작성 실패: {e}", file=sys.stderr)

    replay_chart: dict | None = None
    if embed_figure:
        replay_chart = {
            "sim": sim,
            "trades": trades,
            "name": name,
            "bar_label": bar_label,
            "ma_n": ma_n,
            "ret_series": ret_series,
            "trend_ma": trend_plot,
            "show_chart_candle": show_chart_candle,
            "show_chart_volume": show_chart_volume,
            "show_return_overlay": show_return_overlay,
        }

    lines.append(f"[그래프] {out_png} (매수 {n_buy}회 / 매도 {n_sell}회)")

    return BacktestResult(
        True,
        None,
        summary,
        out_png,
        lines,
        replay_chart=replay_chart,
        n_buy=n_buy,
        n_sell=n_sell,
        trade_markers_skipped=trade_markers_skipped,
    )


__all__ = [
    "BacktestResult",
    "FIG_ATTR_TRADE_MARKERS_SKIPPED",
    "MIN_BARS_FOR_ENTRY_FILTERS",
    "TREND_MA_PERIODS",
    "make_backtest_figure",
    "metrics_total_cagr_mdd_equity",
    "normalize_interval",
    "rolling_trend_ma_series",
    "run_backtest_detailed",
    "save_backtest_report_png",
    "save_figure_as_png",
    "strategy_cross_flags_from_cfg",
    "strategy_entry_filters_from_cfg",
    "strategy_trailing_stop_from_cfg",
    "trend_overlay_flags_from_strategy",
]
