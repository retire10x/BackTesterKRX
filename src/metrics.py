"""
누적·CAGR·MDD(소수 둘째 자리)·정적 보고서 PNG·전체 백테스트 파이프라인.
(GUI/Tkinter 비의존. v2.7: 캔들·거래량·수익률 토글 및 동적 panel_ratios; v2.6: PNG 저장 여백 압축.)
"""
from __future__ import annotations

import datetime
import os
import warnings
from dataclasses import dataclass

import matplotlib

# PNG/워커스레드: mplfinance가 plt.figure()를 쓰므로 GUI 백엔드보다 먼저 비대화형으로 고정
matplotlib.use("Agg")

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

# 차트 v2.5: 캔들 타점 마커 크기
MARKER_SIZE = 60
MARKER_LINEWIDTH = 0.35


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
    """mplfinance·matplotlib 공통: 한글 + 축 데이터 여백 최소화(캔들이 좌우로 붙도록)."""
    return {
        **_korean_font_rc(),
        "axes.xmargin": 0.02,
        "axes.ymargin": 0.02,
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


def _trade_price_series_at_open(
    trades: list[dict], index: pd.DatetimeIndex
) -> pd.Series:
    """체결 봉 인덱스에 익봉 시가(y) — 캔들 시가 위치와 일치."""
    s = pd.Series(np.nan, index=index, dtype=float)
    for t in trades:
        ts = pd.Timestamp(t["date"])
        if ts not in s.index:
            pos = index.get_indexer([ts], method="nearest")
            if pos.size and pos[0] >= 0:
                ts = index[int(pos[0])]
            else:
                continue
        s.loc[ts] = float(t["price"])
    return s


def _chart_panel_ratios_and_return_panel(
    show_volume: bool, show_return: bool
) -> tuple[tuple[int, ...], int | None]:
    """거래량·수익률 표시 여부에 따른 mplfinance panel_ratios 및 누적수익률 패널 인덱스."""
    if show_volume and show_return:
        return (5, 2, 3), 2
    if show_volume and not show_return:
        return (7, 3), None
    if not show_volume and show_return:
        return (6, 4), 1
    return (1,), None


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
    ma_col = f"MA{ma_n}"
    if ma_col in sim.columns:
        ma_primary = sim[ma_col].reindex(idx).astype(float)
    else:
        ma_primary = odata["Close"].rolling(ma_n, min_periods=1).mean()

    addplots: list = [
        mpf.make_addplot(ma_primary, panel=0, color="#263238", width=1.05),
    ]
    if trend_ma:
        for period, color in ((120, "#ff6f00"), (200, "#6a1b9a")):
            if period not in trend_ma:
                continue
            ser = trend_ma[period].reindex(idx).astype(float)
            if not ser.notna().any():
                continue
            addplots.append(
                mpf.make_addplot(ser, panel=0, color=color, width=0.95),
            )

    buy_y = _trade_price_series_at_open(buys, idx)
    sell_y = _trade_price_series_at_open(sells, idx)
    ms = max(8.0, min(22.0, MARKER_SIZE / 2.5))
    if buys and buy_y.notna().any():
        addplots.append(
            mpf.make_addplot(
                buy_y,
                type="scatter",
                marker="^",
                markersize=ms,
                color="#c62828",
                edgecolors="#3e2723",
                linewidths=MARKER_LINEWIDTH,
                panel=0,
            )
        )
    if sells and sell_y.notna().any():
        addplots.append(
            mpf.make_addplot(
                sell_y,
                type="scatter",
                marker="v",
                markersize=ms,
                color="#0d47a1",
                edgecolors="#01579b",
                linewidths=MARKER_LINEWIDTH,
                panel=0,
            )
        )

    panel_ratios, ret_panel = _chart_panel_ratios_and_return_panel(
        show_volume, show_return
    )
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
        trend_note = " · " + "·".join(f"{p}{unit}" for p in sorted(trend_ma))
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
        f"{ma_n}{unit_ma} 이평{trend_note}"
    )

    plot_type = "candle" if show_candle else "line"

    fig, _axlist = mpf.plot(
        odata,
        type=plot_type,
        style=style,
        addplot=addplots,
        volume=show_volume,
        panel_ratios=panel_ratios,
        returnfig=True,
        figsize=(12, 10),
        title=title,
        tight_layout=True,
        scale_padding=0.88,
    )
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
    )
