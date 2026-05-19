"""
누적·CAGR·MDD(소수 둘째 자리)·정적 보고서 PNG·전체 백테스트 파이프라인.
(GUI/Tkinter 비의존. v3.5: 타점 인덱스 불일치 시 CRITICAL 로그·GUI 경고;
 v3.4 날짜 엄격 매칭·축 바인딩·v3.3 타점 스타일.)
"""
from __future__ import annotations

import datetime
import os
import sys
import warnings
from dataclasses import dataclass

import matplotlib

# PNG/워커스레드: mplfinance가 plt.figure()를 쓰므로 GUI 백엔드보다 먼저 비대화형으로 고정
matplotlib.use("Agg")

import numpy as np
import pandas as pd
from matplotlib import ticker as mticker
from matplotlib.figure import Figure
from matplotlib.lines import Line2D

from .data_loader import (
    ensure_datetime_index,
    fetch_filtered_universe,
    load_ohlcv,
    resample_weekly_ohlcv,
)
from .simulator import simulate_single
from .strategy import add_signals

WARMUP_DAYS_DAILY = 120
WARMUP_DAYS_FOR_WEEKLY = 800

# 차트 표시용 추세 이평 기간 → 선색 (매매 기준 이평과 겹치면 해당 추세선 스킵)
TREND_MA_PERIODS = (5, 10, 20, 60, 120, 200)
TREND_MA_COLORS: dict[int, str] = {
    5: "#5d4037",
    10: "#00695c",
    20: "#558b2f",
    60: "#f9a825",
    120: "#ff6f00",
    200: "#6a1b9a",
}

# 타점 마커 — 데이터 앵커(저가/고가) + offset points 고정 간격(v3.3·v3.4 매칭)
TRADE_MARKER_OFFSET_PT = 15.0
MARKER_BUY_COLOR = "#2e7d32"
MARKER_BUY_OUTLINE = "#1b5e20"
MARKER_SELL_COLOR = "#fdd835"
MARKER_SELL_OUTLINE = "#b45309"
MARKER_ANNOT_SIZE = 11

# make_backtest_figure 가 생성한 Figure 에 부착: 차트에서 스킵된 매매 타점 건수(v3.5)
FIG_ATTR_TRADE_MARKERS_SKIPPED = "_trade_markers_skipped"


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


def _korean_font_rc() -> dict:
    """mplfinance·matplotlib 공통: 설치된 한글 고딕 우선(rc에 넣어 make_mpf_style과 동기화)."""
    from matplotlib import font_manager

    installed = {f.name for f in font_manager.fontManager.ttflist}
    for name in ("Malgun Gothic", "AppleGothic", "NanumGothic", "Nanum Gothic"):
        if name in installed:
            return {
                "font.sans-serif": [name],
                "font.family": "sans-serif",
                "axes.unicode_minus": False,
            }
    return {"axes.unicode_minus": False}


def _chart_rc_params() -> dict:
    """mplfinance·matplotlib 공통: 한글 + 축·세로 여유(지표 간 답답함 완화)."""
    return {
        **_korean_font_rc(),
        "axes.xmargin": 0.02,
        "axes.ymargin": 0.15,
    }


def _save_report_png(fig: Figure, out_path: str, dpi: int = 300) -> None:
    """보고서 PNG: 저장 직전 tight_layout + bbox/pad로 상단·사방 흰 여백 최소화."""
    dn = os.path.dirname(out_path)
    if dn:
        os.makedirs(dn, exist_ok=True)
    # mplfinance 축은 수동 배치라 tight_layout 호환 경고가 날 수 있음 → 무시하고 시도
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=".*not compatible with tight_layout.*",
            category=UserWarning,
        )
        try:
            fig.tight_layout()
        except Exception:
            pass
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight", pad_inches=0.05)


def _trade_resolve_bar_index(t: dict, idx: pd.DatetimeIndex) -> int | None:
    """거래일 → 시뮬 인덱스 내 봉 번호. 자정 기준 일자 normalize 후 정확히 일치할 때만(nearest 금지)."""
    trade_ts = pd.Timestamp(t["date"])
    idx_tz = getattr(idx, "tz", None)
    if idx_tz is not None:
        trade_ts = (
            trade_ts.tz_localize(idx_tz, ambiguous="infer", nonexistent="shift_forward")
            if trade_ts.tzinfo is None
            else trade_ts.tz_convert(idx_tz)
        )
    elif trade_ts.tzinfo is not None:
        trade_ts = trade_ts.tz_convert(None)

    target = trade_ts.normalize()
    idx_norm = idx.normalize()
    pos = idx_norm.get_indexer([target], method=None)
    if pos.size == 0 or int(pos[0]) < 0:
        return None
    return int(pos[0])


def _draw_trade_markers_matplotlib(
    ax,
    buys: list[dict],
    sells: list[dict],
    odata: pd.DataFrame,
) -> int:
    """매수▲·매도▼ — 저가/고가 앵커 + offset points. 매칭 실패 건수를 반환(v3.5)."""
    import matplotlib.patheffects as pe

    idx = odata.index
    low = odata["Low"].astype(float)
    high = odata["High"].astype(float)
    xnums = np.arange(len(idx), dtype=float)

    skipped_count = 0

    buy_fx = [
        pe.withStroke(linewidth=1.25, foreground=MARKER_BUY_OUTLINE),
    ]
    sell_fx = [
        pe.withStroke(linewidth=1.35, foreground=MARKER_SELL_OUTLINE),
    ]

    for t in buys:
        bi = _trade_resolve_bar_index(t, idx)
        if bi is None:
            skipped_count += 1
            continue
        ann = ax.annotate(
            "▲",
            xy=(xnums[bi], float(low.iloc[bi])),
            xytext=(0.0, -TRADE_MARKER_OFFSET_PT),
            textcoords="offset points",
            fontsize=MARKER_ANNOT_SIZE,
            color=MARKER_BUY_COLOR,
            ha="center",
            va="center",
            zorder=6,
        )
        ann.set_path_effects(buy_fx)

    for t in sells:
        bi = _trade_resolve_bar_index(t, idx)
        if bi is None:
            skipped_count += 1
            continue
        ann = ax.annotate(
            "▼",
            xy=(xnums[bi], float(high.iloc[bi])),
            xytext=(0.0, TRADE_MARKER_OFFSET_PT),
            textcoords="offset points",
            fontsize=MARKER_ANNOT_SIZE,
            color=MARKER_SELL_COLOR,
            ha="center",
            va="center",
            zorder=6,
        )
        ann.set_path_effects(sell_fx)

    if skipped_count > 0:
        error_msg = (
            f"[CRITICAL ERROR] 총 {skipped_count}건의 매매 타점이 차트 OHLCV 날짜 인덱스와 "
            "매칭되지 않아 렌더링에서 제외되었습니다. "
            "시뮬 체결일과 차트 데이터의 날짜·타임존·정규화(normalize) 상태를 즉시 재점검하세요."
        )
        print(error_msg, file=sys.stderr)
        warnings.warn(error_msg, RuntimeWarning, stacklevel=2)

    return skipped_count
def _expand_mpf_vertical_panel_gaps(fig: Figure, gap_each: float = 0.024) -> None:
    """mplfinance 다패널 Figure에서 주 패널_axes 쌍 사이 세로 숨통 확보."""
    axes_all = fig.axes
    n_pairs = len(axes_all) // 2
    if n_pairs < 2:
        return
    pairs: list[tuple] = []
    for i in range(n_pairs):
        pri = axes_all[2 * i]
        twin = axes_all[2 * i + 1] if 2 * i + 1 < len(axes_all) else None
        pairs.append((pri, twin))
    meta = []
    for pri, twin in pairs:
        pos = pri.get_position()
        meta.append({"pri": pri, "twin": twin, "pos": pos})
    meta.sort(key=lambda d: d["pos"].y0)
    for i in range(len(meta) - 1):
        lower = meta[i]
        upper = meta[i + 1]
        lp = lower["pos"]
        up = upper["pos"]
        new_lo_h = lp.height - gap_each
        new_up_y0 = up.y0 + gap_each
        new_up_h = up.height - gap_each
        if new_lo_h <= 0.04 or new_up_h <= 0.04:
            continue
        lower["pri"].set_position([lp.x0, lp.y0, lp.width, new_lo_h])
        if lower["twin"] is not None:
            lower["twin"].set_position([lp.x0, lp.y0, lp.width, new_lo_h])
        upper["pri"].set_position([up.x0, new_up_y0, up.width, new_up_h])
        if upper["twin"] is not None:
            upper["twin"].set_position([up.x0, new_up_y0, up.width, new_up_h])
        lower["pos"] = lower["pri"].get_position()
        upper["pos"] = upper["pri"].get_position()


def _hts_major_tick_formatter(
    idx: pd.DatetimeIndex, maj_step: int
) -> mticker.FuncFormatter:
    """평소 MM.DD, 연도 전환 첫 봉 또는 그 직후 첫 메이저 틱만 YYYY-MM."""

    def fmt(x, pos=None):
        n = len(idx)
        if n == 0:
            return ""
        i = int(round(float(x)))
        i = max(0, min(n - 1, i))
        ts = idx[i]
        if i > 0 and idx[i - 1].year != ts.year:
            return f"{ts.year}-{ts.month:02d}"
        prev_tick = i - maj_step
        if prev_tick >= 0 and idx[prev_tick].year != ts.year:
            return f"{ts.year}-{ts.month:02d}"
        return f"{ts.month:02d}.{ts.day:02d}"

    return mticker.FuncFormatter(fmt)


def _apply_hts_style_xaxis(fig: Figure, idx: pd.DatetimeIndex) -> None:
    """세로 격자 밀도 확대 + 날짜 라벨 포맷 교체 (공유 X축 패널 일괄 적용)."""
    n = len(idx)
    if n == 0:
        return
    # 기존 mplfinance ~ n/10 간격 대비 약 2배 촘촘한 메이저 틱; 라벨은 메이저에만.
    maj_step = max(1, min(45, n // 14))
    min_step = max(1, maj_step // 2)
    maj_loc = mticker.MultipleLocator(base=maj_step)
    min_loc = mticker.MultipleLocator(base=min_step)
    formatter = _hts_major_tick_formatter(idx, maj_step)

    for ax in fig.axes:
        ax.xaxis.set_major_locator(maj_loc)
        ax.xaxis.set_minor_locator(min_loc)
        ax.xaxis.set_major_formatter(formatter)
        ax.tick_params(axis="x", which="major", labelsize=8.5)
        ax.grid(True, which="major", axis="x", linestyle="--", linewidth=0.55, color="#cfcfcf")
        ax.grid(
            True,
            which="minor",
            axis="x",
            linestyle="--",
            linewidth=0.35,
            color="#e8e8e8",
            alpha=0.95,
        )


def _chart_panel_ratios_and_return_panel(
    show_volume: bool, show_return: bool
) -> tuple[tuple[int, ...], int | None]:
    """거래량·수익률 표시 여부에 따른 mplfinance panel_ratios 및 누적수익률 패널 인덱스.

    거래량·수익률 패널 세로 비중은 기본 대비 약 30% 축소(×0.7)한 정수 비율.
    """
    if show_volume and show_return:
        # was (5, 2, 3) → volume/return ×0.7 → (5, 1.4, 2.1) ≡ (50, 14, 21)
        return (50, 14, 21), 2
    if show_volume and not show_return:
        # was (7, 3) → 3×0.7=2.1 ≡ (10, 3)
        return (10, 3), None
    if not show_volume and show_return:
        # was (6, 4) → 4×0.7=2.8 ≡ (15, 7)
        return (15, 7), 1
    return (1,), None


def _draw_static_trend_ma_legend(ax, bar_label: str) -> None:
    """추세 이평 6종 — 범례 색상은 항상 TREND_MA_COLORS 와 1:1."""
    unit = "봉" if "주" in bar_label else "일"
    handles = [
        Line2D(
            [0],
            [0],
            color=TREND_MA_COLORS[p],
            linewidth=2.4,
            solid_capstyle="round",
            label=f"{p}{unit}선",
        )
        for p in TREND_MA_PERIODS
    ]
    ax.legend(
        handles=handles,
        loc="upper left",
        fontsize=8,
        framealpha=0.92,
        fancybox=False,
        edgecolor="#bdbdbd",
        ncol=2,
        columnspacing=0.9,
        handlelength=2.2,
    )


def _draw_trend_ma_lines_on_price_panel(
    ax_price,
    idx: pd.DatetimeIndex,
    trend_ma: dict[int, pd.Series] | None,
    bar_label: str,
) -> None:
    """추세 이평 오버레이(체크한 기간만). 매매 기준 N과 무관하게 모두 그린다."""
    if not trend_ma:
        return
    x = np.arange(len(idx))
    for period in sorted(trend_ma.keys()):
        ser = trend_ma[period].reindex(idx).astype(float)
        if not ser.notna().any():
            continue
        color = TREND_MA_COLORS.get(period, "#546e7a")
        ax_price.plot(
            x,
            ser.to_numpy(),
            color=color,
            linewidth=0.95,
            solid_capstyle="round",
            zorder=4,
        )


def make_backtest_figure(
    sim: pd.DataFrame,
    trades: list[dict],
    name: str,
    bar_label: str,
    ma_n: int,
    ret_series: pd.Series,
    trend_ma: dict[int, pd.Series] | None = None,
    *,
    show_candle: bool = True,
    show_volume: bool = True,
    show_return: bool = True,
) -> Figure:
    """가격(OHLC)·거래량·누적 수익률을 지표 토글에 맞춰 mplfinance 멀티패널로 렌더."""
    import matplotlib.pyplot as plt
    import mplfinance as mpf

    chart_rc = _chart_rc_params()
    plt.rcParams.update(chart_rc)
    buys = [t for t in trades if t["side"] == "BUY"]
    sells = [t for t in trades if t["side"] == "SELL"]

    odata = sim[["Open", "High", "Low", "Close"]].copy().astype(float)
    if "Volume" in sim.columns:
        odata["Volume"] = pd.to_numeric(sim["Volume"], errors="coerce").fillna(0.0)
    else:
        odata["Volume"] = 0.0

    idx = odata.index

    panel_ratios, ret_panel = _chart_panel_ratios_and_return_panel(
        show_volume, show_return
    )

    addplots: list = []
    # mplfinance 0.12.10b: 가격 패널에 line/scatter addplot 을 여러 개 두면 하위 패널 처리 시 빈 y 배열 오류가 남.
    # 수익률만 make_addplot 으로 두고, 타점·추세선은 matplotlib 로 상단 패널에 직접 그린다.
    if ret_panel is not None:
        ret_aligned = ret_series.reindex(idx).astype(float)
        if not ret_aligned.notna().any():
            ret_aligned = pd.Series(0.0, index=idx)
        addplots.append(
            mpf.make_addplot(
                ret_aligned,
                panel=ret_panel,
                color="royalblue",
                width=1.6,
                ylabel="누적 수익률 (%)",
            )
        )

    mc = mpf.make_marketcolors(
        up="#e53935",
        down="#1e88e5",
        edge={"up": "#e53935", "down": "#1e88e5"},
        wick={"up": "#e53935", "down": "#1e88e5"},
        volume={"up": "#e53935", "down": "#1e88e5"},
    )
    style = mpf.make_mpf_style(
        base_mpf_style="charles",
        marketcolors=mc,
        gridstyle="--",
        gridcolor="#cfcfcf",
        rc=chart_rc,
    )

    trend_note = ""
    if trend_ma:
        unit = "봉" if "주" in bar_label else "일"
        trend_note = " · " + "·".join(f"{p}{unit}" for p in sorted(trend_ma.keys()))
    unit_ma = "봉" if "주" in bar_label else "일"

    price_label = "캔들" if show_candle else "종가"
    shown: list[str] = [price_label]
    if show_volume:
        shown.append("거래량")
    if show_return:
        shown.append("수익률")
    chart_bits = "+".join(shown)
    title = (
        f"{name} · {bar_label} · {chart_bits} · "
        f"매매기준 {ma_n}{unit_ma}{trend_note}"
    )

    plot_type = "candle" if show_candle else "line"

    fig, axlist = mpf.plot(
        odata,
        type=plot_type,
        style=style,
        addplot=addplots if addplots else [],
        volume=show_volume,
        panel_ratios=panel_ratios,
        returnfig=True,
        figsize=(12, 10),
        title=title,
        tight_layout=True,
        scale_padding=0.88,
    )
    _expand_mpf_vertical_panel_gaps(fig, gap_each=0.028)
    _apply_hts_style_xaxis(fig, idx)
    ax_price = axlist[0]
    n_skip_tm = _draw_trade_markers_matplotlib(ax_price, buys, sells, odata)
    setattr(fig, FIG_ATTR_TRADE_MARKERS_SKIPPED, int(n_skip_tm))
    _draw_trend_ma_lines_on_price_panel(ax_price, idx, trend_ma, bar_label)
    _draw_static_trend_ma_legend(ax_price, bar_label)
    return fig


def save_backtest_report_png(
    sim: pd.DataFrame,
    trades: list[dict],
    name: str,
    bar_label: str,
    ma_n: int,
    ret_series: pd.Series,
    out_path: str,
    trend_ma: dict[int, pd.Series] | None = None,
    *,
    show_candle: bool = True,
    show_volume: bool = True,
    show_return: bool = True,
) -> None:
    fig = make_backtest_figure(
        sim,
        trades,
        name,
        bar_label,
        ma_n,
        ret_series,
        trend_ma=trend_ma,
        show_candle=show_candle,
        show_volume=show_volume,
        show_return=show_return,
    )
    _save_report_png(fig, out_path)


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
) -> BacktestResult:
    """설정 dict 기준 전체 백테스트. GUI·CLI 공용."""
    lines: list[str] = []
    period = cfg.get("period", {})
    start = period.get("start_date")
    end = period.get("end_date")
    uni = cfg.get("universe", {})
    market = uni.get("market", "KOSPI")
    keyword = uni.get("search_keyword", "") or ""
    selected = (override_code or uni.get("selected_code") or "").strip().zfill(6)

    st = cfg.get("strategy", {})
    ma_n = int(st.get("ma_period", 20))
    interval = normalize_interval(str(st.get("interval", "daily")))
    trend_flags = trend_overlay_flags_from_strategy(st)
    show_chart_candle = bool(st.get("show_chart_candle", True))
    show_chart_volume = bool(st.get("show_chart_volume", True))
    show_chart_return = bool(st.get("show_chart_return", True))

    costs = cfg.get("trading_costs", {})
    buy_c = float(costs.get("buy_cost", 0.00015))
    sell_c = float(costs.get("sell_cost", 0.0020))

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

    candidates = fetch_filtered_universe(market, keyword)
    if selected not in candidates:
        return BacktestResult(
            False,
            f"코드 {selected} 가 현재 검색 결과에 없습니다. 키워드·시장을 확인하세요.",
            [],
            None,
            lines,
        )

    name = candidates[selected]
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

    start_dt = datetime.datetime.strptime(str(start), "%Y-%m-%d")
    warm_days = WARMUP_DAYS_FOR_WEEKLY if interval == "weekly" else WARMUP_DAYS_DAILY
    warm = (start_dt - datetime.timedelta(days=warm_days)).strftime("%Y-%m-%d")

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

    sig_df = add_signals(bars, ma_n)
    res = simulate_single(sig_df, str(start), initial, buy_c, sell_c)
    if res is None:
        return BacktestResult(False, "시뮬 구간이 너무 짧습니다.", [], None, lines)
    sim, trades = res

    full_close = sig_df["Close"].astype(float)
    trend_ma: dict[int, pd.Series] = {}
    for p in TREND_MA_PERIODS:
        if not trend_flags.get(p):
            continue
        trend_ma[p] = rolling_trend_ma_series(full_close, p)
    trend_plot = (
        {p: s.reindex(sim.index) for p, s in trend_ma.items()} if trend_ma else None
    )

    eq = sim["Equity"]
    total_r, cagr_r, mdd_r, ret_series = metrics_total_cagr_mdd_equity(
        eq, initial, bars_per_year
    )
    final_eq = float(eq.iloc[-1])

    summary = [
        ["종목", f"{name} ({selected})"],
        ["봉 주기", bar_label],
        ["초기 자산", f"{initial:,.2f} 원"],
        ["최종 평가액", f"{final_eq:,.2f} 원"],
        ["누적 수익률", f"{total_r:.2f} %"],
        ["연평균 수익률", f"{cagr_r:.2f} %"],
        ["최대 손실 낙폭", f"{mdd_r:.2f} %"],
    ]

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
        show_return=show_chart_return,
    )
    trade_markers_skipped = int(getattr(fig, FIG_ATTR_TRADE_MARKERS_SKIPPED, 0))
    if trade_markers_skipped > 0:
        lines.append(
            f"[CRITICAL] 차트 타점 {trade_markers_skipped}건이 OHLCV 인덱스와 날짜 매칭 실패로 생략되었습니다. "
            "체결일·차트 구간·타임존·normalize 를 재점검하세요."
        )

    _save_report_png(fig, out_png)

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
            "show_chart_return": show_chart_return,
        }

    n_buy = sum(1 for t in trades if t["side"] == "BUY")
    n_sell = sum(1 for t in trades if t["side"] == "SELL")
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
