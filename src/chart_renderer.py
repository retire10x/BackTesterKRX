"""
v4.30 차트 우측 상단 미니멀 OHLC 가격 박스 — 이격도·부가 수식 없음.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

import pandas as pd


def ohlc_row_at_anchor(
    sim: pd.DataFrame,
    t0_date: str,
) -> dict[str, float] | None:
    """앵커 일자(t0) 봉 OHLC — pykrx sim 인덱스 일자 매칭."""
    if sim is None or sim.empty:
        return None
    target = str(t0_date or "").strip()[:10]
    if not target:
        return ohlc_row_from_sim(sim)
    want = pd.Timestamp(target).normalize()
    for i, ix in enumerate(sim.index):
        try:
            if pd.Timestamp(ix).normalize() == want:
                return ohlc_row_from_sim(sim, at_index=i)
        except (TypeError, ValueError):
            continue
    return ohlc_row_from_sim(sim)


def resolve_ohlc_overlay_row(
    overlay: Mapping[str, Any],
    sim: pd.DataFrame | None = None,
) -> dict[str, float] | None:
    """오버레이에 고정된 4가격이 있으면 우선, 없으면 sim·t0_date로 조회."""
    keys = ("open", "high", "low", "close")
    if all(k in overlay for k in keys):
        row: dict[str, float] = {}
        for k in keys:
            try:
                v = float(overlay[k])
            except (TypeError, ValueError):
                return None
            if not math.isfinite(v):
                return None
            row[k] = v
        return row
    if sim is not None:
        return ohlc_row_at_anchor(sim, str(overlay.get("t0_date") or ""))
    return None


def ohlc_row_from_sim(
    sim: pd.DataFrame,
    *,
    at_index: int | None = None,
) -> dict[str, float] | None:
    """pykrx 로드 OHLCV 마지막(또는 지정) 봉 — Open/High/Low/Close 원본."""
    if sim is None or sim.empty:
        return None
    i = -1 if at_index is None else int(at_index)
    if i < 0:
        i = len(sim) + i
    if i < 0 or i >= len(sim):
        return None
    row = sim.iloc[i]
    out: dict[str, float] = {}
    for key, col in (
        ("open", "Open"),
        ("high", "High"),
        ("low", "Low"),
        ("close", "Close"),
    ):
        try:
            v = float(row[col])
        except (TypeError, ValueError, KeyError):
            return None
        if not math.isfinite(v):
            return None
        out[key] = v
    return out


def format_t0_date_label(sim: pd.DataFrame, *, fallback: str = "") -> str:
    """앵커 일자 YYYY-MM-DD — sim 마지막 봉 인덱스."""
    if sim is not None and not sim.empty:
        try:
            return pd.Timestamp(sim.index[-1]).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            pass
    return str(fallback or "").strip()[:10]


def ohlc_overlay_for_chart(
    sim: pd.DataFrame,
    ticker_code: str,
    ticker_name: str,
    t0_date: str,
    *,
    trade_amount_krw: float | None = None,
) -> dict[str, object] | None:
    """앵커일 OHLC 오버레이 — 거래정지(Volume/거래대금 0) 시 None (차트 UI 안전)."""
    from src.filters import pass_active_trading_gate

    row = ohlc_row_at_anchor(sim, t0_date)
    if not row:
        return None
    target = str(t0_date or "").strip()[:10]
    want = pd.Timestamp(target).normalize()
    vol_s = pd.to_numeric(sim.get("Volume"), errors="coerce")
    close_s = pd.to_numeric(sim.get("Close"), errors="coerce")
    vol_t0 = None
    close_t0 = row.get("close")
    for i, ix in enumerate(sim.index):
        try:
            if pd.Timestamp(ix).normalize() == want:
                vol_t0 = float(vol_s.iloc[i]) if vol_s is not None else None
                if close_t0 is None and close_s is not None:
                    close_t0 = float(close_s.iloc[i])
                break
        except (TypeError, ValueError):
            continue
    trd = trade_amount_krw
    if trd is None and close_t0 is not None and vol_t0 is not None:
        trd = float(close_t0) * float(vol_t0)
    if not pass_active_trading_gate(vol_t0, trd):
        return None
    return {
        "code": str(ticker_code or "").strip().zfill(6),
        "name": str(ticker_name or "").strip(),
        "t0_date": target,
        **row,
    }


def draw_ohlc_minimal_panel(
    ax,
    ticker_code: str,
    ticker_name: str,
    t0_date: str,
    ohlc_row: Mapping[str, Any],
    *,
    fontsize: float = 8.5,
    alpha: float = 0.6,
) -> None:
    """
    차트 우측 상단: O/H/L/C 4가격만 초소형 투명 박스 (v4.30).
    이격도·수식 텍스트 없음.
    """
    if ax is None or not ohlc_row:
        return

    def _px(key: str) -> int | None:
        try:
            v = float(ohlc_row[key])
        except (TypeError, ValueError, KeyError):
            return None
        if not math.isfinite(v):
            return None
        return int(round(v))

    o = _px("open")
    h = _px("high")
    low = _px("low")
    c = _px("close")
    if o is None or h is None or low is None or c is None:
        return

    code6 = str(ticker_code or "").strip().zfill(6)
    nm = str(ticker_name or "").strip() or code6
    t0 = str(t0_date or "").strip()[:10]

    minimal_text = (
        f"[{code6}] {nm} ({t0} t0)\n"
        f"O: {o:,} | H: {h:,} | L: {low:,} | C: {c:,}"
    )

    bbox_props = {
        "boxstyle": "square,pad=0.2",
        "facecolor": "#F8F9F9",
        "edgecolor": "none",
        "alpha": float(alpha),
    }

    ax.text(
        0.99,
        0.98,
        minimal_text,
        transform=ax.transAxes,
        fontsize=float(fontsize),
        fontweight="bold",
        color="#34495E",
        horizontalalignment="right",
        verticalalignment="top",
        bbox=bbox_props,
        zorder=30,
        clip_on=False,
    )
