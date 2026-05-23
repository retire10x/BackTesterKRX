"""
정적 백테스트 리포트 차트 (matplotlib Agg · mplfinance 멀티패널).
GUI 비의존. metrics.run_backtest_detailed 가 조립한 인자로 Figure·PNG 를 만든다.
v4.6: 패널 세로 레이블(Price·Volume 등) 숨김 후 거래량·누적수익률 패널은 좌상단 뱃지.ax.text 로 표시.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import os
import sys
import warnings

import numpy as np
import pandas as pd
from matplotlib import ticker as mticker
from matplotlib.figure import Figure

from .backtest_constants import (
    FIG_ATTR_TRADE_MARKERS_SKIPPED,
    MARKER_ANNOT_SIZE,
    MARKER_BUY_COLOR,
    MARKER_BUY_OUTLINE,
    MARKER_SELL_COLOR,
    MARKER_SELL_OUTLINE,
    MARKER_TRAIL_STOP_COLOR,
    MARKER_TRAIL_STOP_OUTLINE,
    TREND_MA_COLORS,
    TREND_MA_LINEWIDTH,
    TRADE_MARKER_OFFSET_PT,
)


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
    """mplfinance·matplotlib 공통: 한글 + 가격 패널 y 여백은 추후 set_ylim 로 보정."""
    return {
        **_korean_font_rc(),
        "axes.xmargin": 0.02,
        "axes.ymargin": 0.06,
    }


def save_figure_as_png(fig: Figure, out_path: str, dpi: int = 300) -> None:
    """보고서 PNG: tight_layout → subplots_adjust → 무인자 autofmt_xdate 로 라벨·가장자리 clipping 완화."""
    dn = os.path.dirname(out_path)
    if dn:
        os.makedirs(dn, exist_ok=True)
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
        try:
            fig.subplots_adjust(
                left=0.05, right=0.92, top=0.93, bottom=0.12, hspace=0.34
            )
        except Exception:
            pass
        try:
            fig.autofmt_xdate()
        except Exception:
            pass
    # bbox_inches='tight' 는 본 레이아웃을 덮어 잘림을 유발할 수 있어 figure bbox 기준 저장
    fig.savefig(out_path, dpi=dpi)


def _mplfinance_primary_axes(axlist: list) -> list:
    """mplfinance 플래튼 뒤 axlist 에서 패널별 보이는 primary 축만(짝수 인덱스) 순서대로 반환."""
    out: list = []
    for i in range(0, len(axlist), 2):
        ax = axlist[i]
        if getattr(ax, "get_visible", lambda: True)():
            out.append(ax)
    return out


def _assign_price_volume_return_axes(
    primary_axes: list,
    *,
    show_volume: bool,
    show_return: bool,
) -> tuple:
    ax_price = primary_axes[0] if primary_axes else None
    ax_vol = None
    ax_ret = None
    if show_volume and show_return and len(primary_axes) >= 3:
        ax_vol, ax_ret = primary_axes[1], primary_axes[2]
    elif show_volume and len(primary_axes) >= 2:
        ax_vol = primary_axes[1]
    elif show_return and len(primary_axes) >= 2:
        ax_ret = primary_axes[1]
    return ax_price, ax_vol, ax_ret


def _strip_vertical_ylabel(ax) -> None:
    """패널 축 우측·좌측 세로 레이블 제거( mplfinance 기본 문자열 숨김 )."""
    if ax is None:
        return
    ax.set_ylabel("")
    try:
        ax.yaxis.offsetText.set_visible(False)
    except Exception:
        pass


def _panel_badge_font_families() -> tuple[str, ...]:
    """패널 뱃지(이모지+한글) glyph 폴백 — 없는 패밀리는 matplotlib 가 조용히 스킵."""
    return ("Segoe UI Emoji", "Malgun Gothic", "DejaVu Sans", "sans-serif")


def _panel_upper_left_badge(ax, text: str, *, fontsize: float = 9.0) -> None:
    """패널 내부 좌상단: 흰 반투명 박스 + 연한 회색 테두리."""
    if ax is None or not text:
        return
    ax.text(
        0.012,
        0.96,
        text,
        transform=ax.transAxes,
        fontsize=fontsize,
        ha="left",
        va="top",
        fontfamily=_panel_badge_font_families(),
        bbox={
            "facecolor": "white",
            "alpha": 0.8,
            "edgecolor": "#dddddd",
            "linewidth": 0.8,
            "boxstyle": "round,pad=0.32",
        },
        zorder=25,
        clip_on=False,
    )


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
    sell_fx_ma = [
        pe.withStroke(linewidth=1.35, foreground=MARKER_SELL_OUTLINE),
    ]
    sell_fx_trail = [
        pe.withStroke(linewidth=1.35, foreground=MARKER_TRAIL_STOP_OUTLINE),
    ]

    for t in buys:
        bi_exec = _trade_resolve_bar_index(t, idx)
        if bi_exec is None:
            skipped_count += 1
            continue
        bi = bi_exec - 1
        if bi < 0:
            skipped_count += 1
            continue
        t["marked_date"] = idx[bi].strftime("%Y-%m-%d")
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
        bi_exec = _trade_resolve_bar_index(t, idx)
        if bi_exec is None:
            skipped_count += 1
            continue
        bi = bi_exec - 1
        if bi < 0:
            skipped_count += 1
            continue
        t["marked_date"] = idx[bi].strftime("%Y-%m-%d")
        if str((t.get("reason") or "")).strip().lower() == "trail_stop":
            scolor = MARKER_TRAIL_STOP_COLOR
            sfx = sell_fx_trail
        else:
            scolor = MARKER_SELL_COLOR
            sfx = sell_fx_ma
        ann = ax.annotate(
            "▼",
            xy=(xnums[bi], float(high.iloc[bi])),
            xytext=(0.0, TRADE_MARKER_OFFSET_PT),
            textcoords="offset points",
            fontsize=MARKER_ANNOT_SIZE,
            color=scolor,
            ha="center",
            va="center",
            zorder=6,
        )
        ann.set_path_effects(sfx)

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
    """mplfinance 다패널 Figure에서 주 패널_axes 쌍 사이 세로 숨통 확보.
    
    하단 마진(0.15)과 상단 마진(0.95)을 강제 지정하고,
    각 패널이 차지하는 세로 비율을 원래 비율(6:2:2 등)로 완벽히 등분하여 재배치합니다.
    """
    axes_all = fig.axes
    n_pairs = len(axes_all) // 2
    if n_pairs < 2:
        # 단일 패널인 경우에도 최소한 하단 마진(0.15)을 확보해 줍니다.
        if n_pairs == 1:
            pri = axes_all[0]
            twin = axes_all[1] if len(axes_all) > 1 else None
            pos = pri.get_position()
            if pos.y0 < 0.15:
                diff = 0.15 - pos.y0
                new_h = pos.height - diff
                if new_h > 0.1:
                    pri.set_position([pos.x0, 0.15, pos.width, new_h])
                    if twin is not None:
                        twin.set_position([pos.x0, 0.15, pos.width, new_h])
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
        
    # 하단에서 상단 순으로 정렬
    meta.sort(key=lambda d: d["pos"].y0)
    
    # 마진 확보 범위 (하단 0.15 ~ 상단 0.95)
    y_min = 0.15
    y_max = 0.95
    N = len(meta)
    
    total_orig_h = sum(m["pos"].height for m in meta)
    H_total = y_max - y_min
    H_panels = H_total - (N - 1) * gap_each
    
    if H_panels <= 0.1:
        return
        
    y_current = y_min
    for m in meta:
        orig_h = m["pos"].height
        ratio = orig_h / total_orig_h
        h_new = H_panels * ratio
        
        lp = m["pos"]
        m["pri"].set_position([lp.x0, y_current, lp.width, h_new])
        if m["twin"] is not None:
            m["twin"].set_position([lp.x0, y_current, lp.width, h_new])
            
        y_current += h_new + gap_each


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


def _share_x_axes(fig: Figure, ax_primary) -> None:
    """멀티패널이 동일한 X 눈금·날짜를 쓰도록 축 공유."""
    for ax in fig.axes:
        if ax is ax_primary:
            continue
        try:
            ax.sharex(ax_primary)
        except Exception:
            continue


def _apply_hts_style_xaxis(fig: Figure, idx: pd.DatetimeIndex) -> None:
    """세로 격자 + 날짜 눈금·포매터. 라벨 회전은 적용하지 않음(저장 시 `save_figure_as_png` 의 `autofmt_xdate` 에 맡김)."""
    n = len(idx)
    if n == 0:
        return
    maj_step = max(1, min(45, n // 14))
    min_step = max(1, maj_step // 2)
    maj_loc = mticker.MultipleLocator(base=maj_step)
    min_loc = mticker.MultipleLocator(base=min_step)
    formatter = _hts_major_tick_formatter(idx, maj_step)

    for ax in fig.axes:
        ax.xaxis.set_major_locator(maj_loc)
        ax.xaxis.set_minor_locator(min_loc)
        ax.xaxis.set_major_formatter(formatter)
        ax.tick_params(
            axis="x",
            which="major",
            labelsize=8.5,
            bottom=True,
            labelbottom=True,
        )
        ax.tick_params(axis="x", which="minor", bottom=True)
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

    가격 패널 비중을 다소 키워 캔들·추세선이 세로 중앙에 잘 보이게 함.
    """
    if show_volume and show_return:
        return (6, 2, 2), 2
    if show_volume and not show_return:
        return (6, 2), None
    if not show_volume and show_return:
        return (6, 2), 1
    return (1,), None


def _autoscale_price_panel_y_with_trends(
    ax_price,
    odata: pd.DataFrame,
    trend_ma: dict[int, pd.Series] | None,
    idx: pd.DatetimeIndex,
) -> None:
    """저가~고가 및 표시 중인 추세 이평을 포함해 가격 축 범위를 잡아 봉이 하단에 치우치지 않게 함."""
    low = odata["Low"].astype(float)
    high = odata["High"].astype(float)
    ymin = float(np.nanmin(low.to_numpy()))
    ymax = float(np.nanmax(high.to_numpy()))
    if trend_ma:
        for ser in trend_ma.values():
            v = ser.reindex(idx).astype(float).dropna()
            if v.empty:
                continue
            ymin = min(ymin, float(v.min()))
            ymax = max(ymax, float(v.max()))
    span = ymax - ymin
    if span <= 0 or not (np.isfinite(ymin) and np.isfinite(ymax)):
        return
    pad = max(span * 0.07, abs(ymax) * 0.004 if ymax else span * 0.02)
    ax_price.set_ylim(ymin - pad, ymax + pad)


def _draw_trend_ma_lines_and_legend(
    ax_price,
    idx: pd.DatetimeIndex,
    trend_ma: dict[int, pd.Series] | None,
    _bar_label: str,
) -> None:
    """추세 이평 오버레이(체크한 기간만) + 좌측 상단 순정 Legend(v4.5)."""
    mw = float(TREND_MA_LINEWIDTH)
    if not trend_ma:
        return
    x = np.arange(len(idx))
    n_visible = 0
    for period in sorted(trend_ma.keys()):
        ser = trend_ma[period].reindex(idx).astype(float)
        if not ser.notna().any():
            continue
        color = TREND_MA_COLORS.get(period, "#546e7a")
        ax_price.plot(
            x,
            ser.to_numpy(),
            color=color,
            linewidth=mw,
            solid_capstyle="round",
            zorder=4,
            label=f"{period}일선",
        )
        n_visible += 1
    if n_visible == 0:
        return
    ncol = 2 if n_visible > 3 else 1
    ax_price.legend(
        loc="upper left",
        fontsize=8.5,
        framealpha=0.55,
        fancybox=True,
        edgecolor="0.65",
        facecolor="white",
        ncol=ncol,
        handlelength=2.2,
        labelspacing=0.35,
        borderpad=0.45,
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

    # v4.5: 추세 이평은 가격 패널 ax.legend — 세로 라벨(Price 등) 대신 패널 뱃지로 패널 구분.

    title = f"{name} (매매기준 MA{ma_n})"

    plot_type = "candle" if show_candle else "line"

    fig, axlist = mpf.plot(
        odata,
        type=plot_type,
        style=style,
        addplot=addplots if addplots else [],
        volume=show_volume,
        panel_ratios=panel_ratios,
        returnfig=True,
        figsize=(12, 8.4),
        title=title,
        tight_layout=False,
        scale_padding=1.04,
        ylabel="",
        ylabel_lower="",
    )
    _expand_mpf_vertical_panel_gaps(fig, gap_each=0.028)

    primary_axes = _mplfinance_primary_axes(axlist)
    ax_price, ax_vol, ax_ret = _assign_price_volume_return_axes(
        primary_axes, show_volume=show_volume, show_return=show_return
    )
    if ax_price is None:
        raise RuntimeError("mplfinance 가격 패널 축을 찾지 못했습니다.")

    _strip_vertical_ylabel(ax_price)
    _strip_vertical_ylabel(ax_vol)
    _strip_vertical_ylabel(ax_ret)
    if show_volume:
        _panel_upper_left_badge(ax_vol, "📊 Volume")
    if show_return:
        _panel_upper_left_badge(ax_ret, "📈 누적 수익률 (%)")

    _share_x_axes(fig, ax_price)
    _apply_hts_style_xaxis(fig, idx)
    n_skip_tm = _draw_trade_markers_matplotlib(ax_price, buys, sells, odata)
    setattr(fig, FIG_ATTR_TRADE_MARKERS_SKIPPED, int(n_skip_tm))
    _draw_trend_ma_lines_and_legend(ax_price, idx, trend_ma, bar_label)
    _autoscale_price_panel_y_with_trends(ax_price, odata, trend_ma, idx)
    # sharex 시 상단 패널 라벨 겹침 완화 — 최종 회전·여백은 save_figure_as_png(autofmt_xdate·subplots_adjust)에서 처리.
    for ax in fig.axes:
        plt.setp(ax.get_xticklabels(), visible=True)
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
    save_figure_as_png(fig, out_path)
