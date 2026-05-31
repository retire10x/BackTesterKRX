"""
정적 백테스트 리포트 차트 (matplotlib Agg · mplfinance 멀티패널).
GUI 비의존. metrics.run_backtest_detailed 가 조립한 인자로 Figure·PNG 를 만든다.
v4.9: `figsize`·저장 DPI·`layout_preset`(report/gui_target) 선택 — GUI 패널 픽셀에 맞춘 렌더.
v4.6: 패널 세로 레이블(Price·Volume 등) 숨김 후 거래량 패널은 좌상단 뱃지로 표시.
v4.7: 누적수익률 독립 하단 패널 제거. 옵션으로 가격 패널 twinx 배경 음영 오버레이만 지원.
"""
from __future__ import annotations

import math

import matplotlib

matplotlib.use("Agg")

import io
import os
import sys
import warnings

import numpy as np
import pandas as pd
from matplotlib import ticker as mticker
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

from .backtest_constants import (
    FIG_ATTR_NO_XDATE_LABELS,
    FIG_ATTR_PRICE_PANEL_XDATE,
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
    TREND_MA_LINEWIDTHS,
    TRADE_MARKER_OFFSET_PT,
)


def _korean_font_rc() -> dict:
    """mplfinance·matplotlib 공통: 설치된 한글 고딕 우선(rc에 넣어 make_mpf_style과 동기화)."""
    from matplotlib import font_manager

    installed = {f.name for f in font_manager.fontManager.ttflist}
    for name in ("Malgun Gothic", "Pretendard", "AppleGothic", "NanumGothic", "Nanum Gothic"):
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


# CLI/report 기준 캔버스(평균 디스플레이·고해상도 PNG 재샘플용)
DEFAULT_CLI_FIGSIZE_IN = (12.0, 7.0)
DEFAULT_CLI_SAVE_DPI = 300


def _scaled_rc_for_figure_inches(fig_w_in: float, fig_h_in: float) -> dict:
    """표준 12×7 대비 축 비율에 맞춰 폰트·틱 크기 소폭 스케일(저해상도 gui_target 과대/과소 글자 완충)."""
    ref_w, ref_h = DEFAULT_CLI_FIGSIZE_IN
    area_ratio = max(1e-6, (fig_w_in * fig_h_in) / (ref_w * ref_h))
    sf = math.sqrt(area_ratio)
    sf = max(0.70, min(1.07, sf))
    fsz = max(7.5, min(11.25, 9.25 * sf))
    return {
        **_chart_rc_params(),
        "font.size": fsz,
        "axes.titlesize": max(10.0, fsz + 2.25),
        "axes.labelsize": fsz,
        "xtick.labelsize": max(7.85, fsz - 1.0),
        "ytick.labelsize": max(7.85, fsz - 1.0),
        "legend.fontsize": max(7.5, fsz - 1.05),
    }


def _prepare_figure_for_png_export(fig: Figure, *, layout_preset: str) -> None:
    """tight_layout / subplots_adjust / autofmt — 디스크·메모리 PNG 공통."""
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
            if layout_preset == "gui_target":
                fig.subplots_adjust(
                    left=0.04,
                    right=0.96,
                    top=0.93,
                    bottom=0.08,
                    hspace=0.20,
                )
            else:
                fig.subplots_adjust(
                    left=0.05, right=0.92, top=0.93, bottom=0.12, hspace=0.34
                )
        except Exception:
            pass
        price_xdate = bool(getattr(fig, FIG_ATTR_PRICE_PANEL_XDATE, False))
        no_xdate = bool(getattr(fig, FIG_ATTR_NO_XDATE_LABELS, False))
        if not price_xdate and not no_xdate:
            try:
                fig.autofmt_xdate()
            except Exception:
                pass
        else:
            try:
                hspace = 0.44 if price_xdate else 0.38
                bottom = 0.08 if price_xdate else 0.06
                if layout_preset == "gui_target":
                    fig.subplots_adjust(
                        left=0.04,
                        right=0.96,
                        top=0.93,
                        bottom=bottom,
                        hspace=min(hspace, 0.22),
                    )
                else:
                    fig.subplots_adjust(
                        left=0.05,
                        right=0.92,
                        top=0.93,
                        bottom=bottom,
                        hspace=hspace,
                    )
            except Exception:
                pass


def _savefig_kwargs_for_layout(layout_preset: str) -> dict[str, object]:
    """GUI용은 tight crop으로 figure 외곽 흰 여백 제거, CLI report 는 전체 bbox 유지."""
    if layout_preset == "gui_target":
        return {"bbox_inches": "tight", "pad_inches": 0.05}
    return {}


def save_figure_as_png(
    fig: Figure,
    out_path: str,
    dpi: int = DEFAULT_CLI_SAVE_DPI,
    *,
    layout_preset: str = "report",
) -> None:
    """
    layout_preset:
      - ``report``: 기존 고해상도 보고서용 여백(기본 DPI 300 등).
      - ``gui_target``: 패널 맞춤 축 비율 + savefig ``bbox_inches='tight'``(pad 0.05).
    """
    dn = os.path.dirname(out_path)
    if dn:
        os.makedirs(dn, exist_ok=True)
    _prepare_figure_for_png_export(fig, layout_preset=layout_preset)
    fig.savefig(out_path, dpi=dpi, **_savefig_kwargs_for_layout(layout_preset))


def figure_to_png_bytes(
    fig: Figure,
    *,
    dpi: int = DEFAULT_CLI_SAVE_DPI,
    layout_preset: str = "report",
) -> bytes:
    """Figure → PNG 바이너리. v3.1 GUI: output/ 디스크 쓰기 없이 표시용."""
    _prepare_figure_for_png_export(fig, layout_preset=layout_preset)
    buf = io.BytesIO()
    fig.savefig(
        buf,
        format="png",
        dpi=dpi,
        **_savefig_kwargs_for_layout(layout_preset),
    )
    buf.seek(0)
    return buf.getvalue()


def _mplfinance_primary_axes(axlist: list) -> list:
    """mplfinance 플래튼 뒤 axlist 에서 패널별 보이는 primary 축만(짝수 인덱스) 순서대로 반환."""
    out: list = []
    for i in range(0, len(axlist), 2):
        ax = axlist[i]
        if getattr(ax, "get_visible", lambda: True)():
            out.append(ax)
    return out


def _assign_price_volume_axes(
    primary_axes: list,
    *,
    show_volume: bool,
) -> tuple:
    """가격 패널 + 선택적 거래량 패널(누적수익률 단독 패널 없음)."""
    ax_price = primary_axes[0] if primary_axes else None
    ax_vol = primary_axes[1] if show_volume and len(primary_axes) >= 2 else None
    return ax_price, ax_vol


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
    각 패널이 차지하는 세로 비율을 원래 비율(6:2·단일 패널 등)에 따라 등분 재배치합니다.
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


def _apply_chart_xaxis_grid_and_locators(
    fig: Figure, idx: pd.DatetimeIndex
) -> tuple[mticker.MultipleLocator, mticker.MultipleLocator, mticker.Formatter]:
    """모든 패널 공통 X 격자·로케이터."""
    n = len(idx)
    maj_step = max(1, min(45, n // 14)) if n else 1
    min_step = max(1, maj_step // 2)
    maj_loc = mticker.MultipleLocator(base=maj_step)
    min_loc = mticker.MultipleLocator(base=min_step)
    formatter = _hts_major_tick_formatter(idx, maj_step)

    for ax in fig.axes:
        ax.xaxis.set_major_locator(maj_loc)
        ax.xaxis.set_minor_locator(min_loc)
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
    return maj_loc, min_loc, formatter


def _apply_chart_xaxis_price_panel_dates(
    fig: Figure,
    idx: pd.DatetimeIndex,
    ax_price,
    ax_vol=None,
) -> None:
    """가격 패널 하단에만 날짜 라벨 — 거래량 패널은 라벨·하단 눈금 없음(v3.16)."""
    if ax_price is None or len(idx) == 0:
        return
    _maj_loc, _min_loc, formatter = _apply_chart_xaxis_grid_and_locators(fig, idx)

    for ax in fig.axes:
        if ax is ax_price:
            continue
        ax.xaxis.set_major_formatter(mticker.NullFormatter())
        ax.tick_params(
            axis="x",
            which="both",
            bottom=False,
            top=False,
            labelbottom=False,
            labeltop=False,
        )

    ax_price.xaxis.set_major_formatter(formatter)
    ax_price.tick_params(
        axis="x",
        which="major",
        labelbottom=True,
        labeltop=False,
        bottom=True,
        top=False,
        labelsize=8.0,
        pad=3,
    )
    ax_price.tick_params(
        axis="x",
        which="minor",
        bottom=True,
        top=False,
        labelbottom=False,
        labeltop=False,
    )
    if ax_vol is not None:
        ax_vol.tick_params(
            axis="x",
            which="both",
            labelbottom=False,
            labeltop=False,
            bottom=False,
            top=False,
        )


def _hide_price_volume_panel_border(ax_price, ax_vol) -> None:
    """가격·거래량 패널 사이 물리 테두리(spine) 제거."""
    if ax_price is None or ax_vol is None:
        return
    try:
        ax_price.spines["bottom"].set_visible(False)
        ax_vol.spines["top"].set_visible(False)
    except (KeyError, AttributeError):
        pass


def _draw_price_volume_panel_divider(fig: Figure, ax_price, ax_vol) -> None:
    """가격·거래량 패널 경계에 figure 좌표 수평 구분선."""
    if ax_price is None or ax_vol is None:
        return
    import matplotlib.lines as mlines

    pos_p = ax_price.get_position()
    pos_v = ax_vol.get_position()
    y_line = (pos_p.y0 + pos_v.y1) * 0.5
    x0 = min(pos_p.x0, pos_v.x0)
    x1 = max(pos_p.x0 + pos_p.width, pos_v.x0 + pos_v.width)
    fig.add_artist(
        mlines.Line2D(
            [x0, x1],
            [y_line, y_line],
            transform=fig.transFigure,
            color="#757575",
            linewidth=1.35,
            solid_capstyle="round",
            zorder=50,
            clip_on=False,
        )
    )


def slice_chart_viewport(
    sim: pd.DataFrame,
    trades: list[dict],
    ret_series: pd.Series,
    trend_ma: dict[int, pd.Series] | None,
    i0: int,
    i1: int,
) -> tuple[pd.DataFrame, list[dict], pd.Series, dict[int, pd.Series] | None]:
    """휠 줌용 봉 구간 [i0, i1] 슬라이스(가격·거래량·이평·타점 동기)."""
    n = len(sim)
    if n == 0:
        return sim, trades, ret_series, trend_ma
    i0 = max(0, min(int(i0), n - 1))
    i1 = max(i0, min(int(i1), n - 1))
    sim_s = sim.iloc[i0 : i1 + 1]
    idx_s = sim_s.index
    ret_s = ret_series.reindex(idx_s)
    trend_s = None
    if trend_ma:
        trend_s = {
            p: ser.reindex(idx_s) for p, ser in trend_ma.items()
        }
    idx_norm = idx_s.normalize()
    trades_s: list[dict] = []
    for t in trades:
        try:
            ts = pd.Timestamp(t["date"]).normalize()
        except Exception:
            continue
        pos = idx_norm.get_indexer([ts], method=None)
        if pos.size and int(pos[0]) >= 0:
            trades_s.append(t)
    return sim_s, trades_s, ret_s, trend_s


def _chart_panel_ratios(show_volume: bool) -> tuple[int, ...]:
    """가격+거래량 2패널만 지원(v4.7: 독립 누적수익률 서브플롯 제거)."""
    return (6, 2) if show_volume else (1,)


def _autoscale_price_panel_y_with_trends(
    ax_price,
    odata: pd.DataFrame,
    trend_ma: dict[int, pd.Series] | None,
    idx: pd.DatetimeIndex,
    trend_ma_visible: dict[int, bool] | None = None,
) -> None:
    """저가~고가 및 표시 중인 추세 이평을 포함해 가격 축 범위를 잡아 봉이 하단에 치우치지 않게 함."""
    low = odata["Low"].astype(float)
    high = odata["High"].astype(float)
    ymin = float(np.nanmin(low.to_numpy()))
    ymax = float(np.nanmax(high.to_numpy()))
    if trend_ma:
        for period, ser in trend_ma.items():
            if trend_ma_visible is not None and not bool(trend_ma_visible.get(period, True)):
                continue
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
    trend_ma_visible: dict[int, bool] | None = None,
) -> None:
    """추세 이평 오버레이(v3.15: 기간별 두께·색·가시성) + 좌측 상단 Legend."""
    if not trend_ma:
        return
    x = np.arange(len(idx))
    legend_handles = []
    for period in sorted(trend_ma.keys()):
        ser = trend_ma[period].reindex(idx).astype(float)
        if not ser.notna().any():
            continue
        is_visible = True if trend_ma_visible is None else bool(
            trend_ma_visible.get(period, True)
        )
        color = TREND_MA_COLORS.get(period, "#546e7a")
        lw = float(TREND_MA_LINEWIDTHS.get(period, TREND_MA_LINEWIDTH))
        (line,) = ax_price.plot(
            x,
            ser.to_numpy(),
            color=color,
            linewidth=lw,
            solid_capstyle="round",
            zorder=4,
            label=f"{period}일선",
        )
        line.set_visible(is_visible)
        if is_visible:
            legend_handles.append(line)
    if not legend_handles:
        return
    ncol = 2 if len(legend_handles) > 3 else 1
    ax_price.legend(
        handles=legend_handles,
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
    trend_ma_visible: dict[int, bool] | None = None,
    show_candle: bool = True,
    show_volume: bool = True,
    figsize: tuple[float, float] | None = None,
    ohlc_overlay: dict[str, str] | None = None,
) -> Figure:
    """가격(OHLC)·선택 거래량 2패널 mplfinance 렌더."""
    import matplotlib.pyplot as plt
    import mplfinance as mpf

    inch_w, inch_h = figsize if figsize is not None else DEFAULT_CLI_FIGSIZE_IN
    if figsize is None:
        chart_rc = _chart_rc_params()
    else:
        chart_rc = _scaled_rc_for_figure_inches(inch_w, inch_h)
    plt.rcParams.update(chart_rc)
    buys = [t for t in trades if t["side"] == "BUY"]
    sells = [t for t in trades if t["side"] == "SELL"]

    odata = sim[["Open", "High", "Low", "Close"]].copy().astype(float)
    if "Volume" in sim.columns:
        odata["Volume"] = pd.to_numeric(sim["Volume"], errors="coerce").fillna(0.0)
    else:
        odata["Volume"] = 0.0

    idx = odata.index

    panel_ratios = _chart_panel_ratios(show_volume)

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

    # v4.30: GUI 미니멀 OHLC 박스 사용 시 mplfinance 대제목 제거(차트 가림 방지).
    title = ""
    if ohlc_overlay is None:
        title = str(name or "").strip()

    plot_type = "candle" if show_candle else "line"

    fig, axlist = mpf.plot(
        odata,
        type=plot_type,
        style=style,
        addplot=[],
        volume=show_volume,
        panel_ratios=panel_ratios,
        returnfig=True,
        figsize=(inch_w, inch_h),
        title=title,
        tight_layout=False,
        scale_padding=1.04,
        ylabel="",
        ylabel_lower="",
    )
    panel_gap = 0.028 if show_volume else 0.028
    _expand_mpf_vertical_panel_gaps(fig, gap_each=panel_gap)

    primary_axes = _mplfinance_primary_axes(axlist)
    ax_price, ax_vol = _assign_price_volume_axes(primary_axes, show_volume=show_volume)
    if ax_price is None:
        raise RuntimeError("mplfinance 가격 패널 축을 찾지 못했습니다.")

    _strip_vertical_ylabel(ax_price)
    _strip_vertical_ylabel(ax_vol)
    if show_volume:
        _panel_upper_left_badge(ax_vol, "📊 Volume")

    _share_x_axes(fig, ax_price)
    _apply_chart_xaxis_price_panel_dates(fig, idx, ax_price, ax_vol if show_volume else None)
    if show_volume:
        _hide_price_volume_panel_border(ax_price, ax_vol)
    n_skip_tm = _draw_trade_markers_matplotlib(ax_price, buys, sells, odata)
    setattr(fig, FIG_ATTR_TRADE_MARKERS_SKIPPED, int(n_skip_tm))
    setattr(fig, FIG_ATTR_PRICE_PANEL_XDATE, True)
    setattr(fig, FIG_ATTR_NO_XDATE_LABELS, True)
    _draw_trend_ma_lines_and_legend(
        ax_price, idx, trend_ma, bar_label, trend_ma_visible=trend_ma_visible
    )
    _autoscale_price_panel_y_with_trends(
        ax_price, odata, trend_ma, idx, trend_ma_visible=trend_ma_visible
    )
    if ohlc_overlay is not None:
        from src.chart_renderer import (
            draw_ohlc_minimal_panel,
            format_t0_date_label,
            resolve_ohlc_overlay_row,
        )

        row = resolve_ohlc_overlay_row(ohlc_overlay, sim)
        if row is not None:
            draw_ohlc_minimal_panel(
                ax_price,
                str(ohlc_overlay.get("code", "")),
                str(ohlc_overlay.get("name", name or "")),
                str(
                    ohlc_overlay.get("t0_date")
                    or format_t0_date_label(sim)
                ),
                row,
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
    trend_ma_visible: dict[int, bool] | None = None,
    show_candle: bool = True,
    show_volume: bool = True,
    figsize: tuple[float, float] | None = None,
    save_dpi: int = DEFAULT_CLI_SAVE_DPI,
    layout_preset: str = "report",
) -> None:
    fig = make_backtest_figure(
        sim,
        trades,
        name,
        bar_label,
        ma_n,
        ret_series,
        trend_ma=trend_ma,
        trend_ma_visible=trend_ma_visible,
        show_candle=show_candle,
        show_volume=show_volume,
        figsize=figsize,
    )
    try:
        save_figure_as_png(fig, out_path, dpi=save_dpi, layout_preset=layout_preset)
    finally:
        plt.close(fig)


def render_backtest_chart_png_bytes(
    sim: pd.DataFrame,
    trades: list[dict],
    name: str,
    bar_label: str,
    ma_n: int,
    ret_series: pd.Series,
    trend_ma: dict[int, pd.Series] | None = None,
    *,
    trend_ma_visible: dict[int, bool] | None = None,
    show_candle: bool = True,
    show_volume: bool = True,
    figsize: tuple[float, float] | None = None,
    save_dpi: int = DEFAULT_CLI_SAVE_DPI,
    layout_preset: str = "report",
    ohlc_overlay: dict[str, str] | None = None,
) -> bytes:
    """
    v3.1 GUI 등: 동일 품질 차트를 PNG 바이트로 반환. 디스크 저장 없음(output/ I/O 절감).
    """
    fig = make_backtest_figure(
        sim,
        trades,
        name,
        bar_label,
        ma_n,
        ret_series,
        trend_ma=trend_ma,
        trend_ma_visible=trend_ma_visible,
        show_candle=show_candle,
        show_volume=show_volume,
        figsize=figsize,
        ohlc_overlay=ohlc_overlay,
    )
    try:
        return figure_to_png_bytes(fig, dpi=save_dpi, layout_preset=layout_preset)
    finally:
        plt.close(fig)
