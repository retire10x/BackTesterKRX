"""
누적·CAGR·MDD(소수 둘째 자리)·정적 보고서 PNG·전체 백테스트 파이프라인.
(GUI/Tkinter 비의존. matplotlib Figure 는 GUI 임베드·PNG 공용.)
"""
from __future__ import annotations

import datetime
import os
from dataclasses import dataclass

import matplotlib
import numpy as np
import pandas as pd
from matplotlib.figure import Figure

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

# 차트 v2.1: 작은 화살표 + 종가선과 수직 간격(매수 아래·매도 위)
MARKER_SIZE = 18
MARKER_LINEWIDTH = 0.28
MARKER_OFFSET_FRAC = 0.028


@dataclass
class BacktestResult:
    ok: bool
    error: str | None
    summary_rows: list[list[str]]
    report_path: str | None
    log_lines: list[str]
    replay_chart: dict | None = None


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


def _setup_korean_font():
    for font in ("Malgun Gothic", "AppleGothic", "NanumGothic", "DejaVu Sans"):
        try:
            matplotlib.rcParams["font.family"] = font
            matplotlib.rcParams["axes.unicode_minus"] = False
            return
        except Exception:
            continue
    matplotlib.rcParams["axes.unicode_minus"] = False


def _price_axis_offset(close_series: pd.Series) -> float:
    lo = float(close_series.min())
    hi = float(close_series.max())
    span = max(hi - lo, 1.0)
    med = float(close_series.median()) if len(close_series) else span
    return max(
        span * MARKER_OFFSET_FRAC,
        med * 0.0048,
        span * 0.0065,
    )


def _trend_line_label(bar_label: str, period: int) -> str:
    if "주" in bar_label:
        return f"{period}봉 장기 이평"
    return f"{period}일 장기 이평"


def make_backtest_figure(
    sim: pd.DataFrame,
    trades: list[dict],
    name: str,
    bar_label: str,
    ma_n: int,
    ret_series: pd.Series,
    trend_ma: dict[int, pd.Series] | None = None,
) -> Figure:
    """백테스트 2패널 Figure (PNG 저장·Tk 임베드 공용)."""
    _setup_korean_font()
    buys = [t for t in trades if t["side"] == "BUY"]
    sells = [t for t in trades if t["side"] == "SELL"]
    close = sim["Close"].astype(float)
    off = _price_axis_offset(close)

    fig = Figure(figsize=(12, 8))
    ax_price, ax_ret = fig.subplots(
        2,
        1,
        sharex=True,
        gridspec_kw={"height_ratios": [2.0, 1.0]},
    )

    ax_price.plot(
        sim.index,
        close.values,
        color="#333333",
        linewidth=1.2,
        label="종가",
        zorder=3,
    )
    if trend_ma:
        long_styles = (
            (120, "#ff6f00", 0.95),
            (200, "#6a1b9a", 0.95),
        )
        for period, color, lw in long_styles:
            if period not in trend_ma:
                continue
            ser = trend_ma[period].astype(float)
            ax_price.plot(
                sim.index,
                ser.values,
                color=color,
                linewidth=lw,
                alpha=0.88,
                label=_trend_line_label(bar_label, period),
                zorder=2,
            )
    if buys:
        y_buy = [t["price"] - off for t in buys]
        ax_price.scatter(
            [t["date"] for t in buys],
            y_buy,
            marker="^",
            s=MARKER_SIZE,
            c="#d32f2f",
            edgecolors="#7f1010",
            linewidths=MARKER_LINEWIDTH,
            zorder=5,
            label="매수 체결 (익봉 시가)",
        )
    if sells:
        y_sell = [t["price"] + off for t in sells]
        ax_price.scatter(
            [t["date"] for t in sells],
            y_sell,
            marker="v",
            s=MARKER_SIZE,
            c="#1565c0",
            edgecolors="#0a2f5c",
            linewidths=MARKER_LINEWIDTH,
            zorder=5,
            label="매도 체결 (익봉 시가)",
        )
    ax_price.set_ylabel("가격 (원)")
    ax_price.grid(True, linestyle="--", alpha=0.45)
    ax_price.legend(loc="upper left", fontsize=8, framealpha=0.92)
    ax_price.set_title(
        f"{name} · {bar_label} · {ma_n}봉 이평 | 주가·매매 타점",
        fontsize=13,
        pad=10,
    )

    ax_ret.plot(
        sim.index,
        ret_series.values,
        color="royalblue",
        linewidth=2,
        label="누적 수익률 (%)",
    )
    ax_ret.set_xlabel("날짜 (봉 기준)")
    ax_ret.set_ylabel("수익률 (%)")
    ax_ret.grid(True, linestyle="--", alpha=0.45)
    ax_ret.legend(loc="upper left", fontsize=9)

    fig.tight_layout()
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
) -> None:
    fig = make_backtest_figure(
        sim, trades, name, bar_label, ma_n, ret_series, trend_ma=trend_ma
    )
    dn = os.path.dirname(out_path)
    if dn:
        os.makedirs(dn, exist_ok=True)
    fig.savefig(out_path, dpi=300)


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
    show_ma120 = bool(st.get("show_ma120", False))
    show_ma200 = bool(st.get("show_ma200", False))

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
    if show_ma120 or show_ma200:
        parts: list[str] = []
        if show_ma120:
            parts.append("120")
        if show_ma200:
            parts.append("200")
        unit = "봉(주봉)" if bar_label == "주봉" else "일"
        lines.append(f"[차트] 장기 추세선 표시: {', '.join(parts)}{unit} 이평")

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
    if show_ma120:
        trend_ma[120] = full_close.rolling(120, min_periods=20).mean()
    if show_ma200:
        trend_ma[200] = full_close.rolling(200, min_periods=20).mean()
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
        sim, trades, name, bar_label, ma_n, ret_series, trend_ma=trend_plot
    )
    dn = os.path.dirname(out_png)
    if dn:
        os.makedirs(dn, exist_ok=True)
    fig.savefig(out_png, dpi=300)

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
        }

    n_buy = sum(1 for t in trades if t["side"] == "BUY")
    n_sell = sum(1 for t in trades if t["side"] == "SELL")
    lines.append(f"[그래프] {out_png} (매수 {n_buy}회 / 매도 {n_sell}회)")

    return BacktestResult(
        True, None, summary, out_png, lines, replay_chart=replay_chart
    )
