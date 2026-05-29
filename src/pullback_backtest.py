"""
v3.40: 단일 종목 주도주 눌림목 타임라인 백테스트.
종가 매수(신호일 t) → 익일 시가(0분) 매도, 복리 누적.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.data_loader import ensure_datetime_index, kim_straight_trend_pass

# GUI 스캔·백테스트 공통 고정 비용 (Harness)
PULLBACK_BUY_COST = 0.00015
PULLBACK_SELL_COST = 0.0020


@dataclass
class PullbackTimelineResult:
    ok: bool
    error: str | None
    report_text: str
    n_entries: int
    final_equity: float
    initial_cash: float


def pullback_signal_at_index(
    vol: pd.Series,
    low: pd.Series,
    close: pd.Series,
    i: int,
    *,
    volume_burst_multiple: float,
    vol_shrink_limit: float,
) -> bool:
    """봉 인덱스 i 를 신호일(t)로 v3.30 3중 조건 판정."""
    if i < 21:
        return False
    vol_ma20_prior = float(vol.iloc[i - 21 : i - 1].mean())
    prev_vol = float(vol.iloc[i - 1])
    today_vol = float(vol.iloc[i])
    ma20 = float(close.iloc[i - 19 : i + 1].mean())
    low_t = float(low.iloc[i])
    close_t = float(close.iloc[i])

    if not (
        np.isfinite(vol_ma20_prior)
        and vol_ma20_prior > 0
        and np.isfinite(prev_vol)
        and prev_vol > 0
        and np.isfinite(ma20)
        and np.isfinite(low_t)
        and np.isfinite(close_t)
        and np.isfinite(today_vol)
    ):
        return False

    burst = float(volume_burst_multiple)
    shrink = float(vol_shrink_limit)
    if prev_vol <= vol_ma20_prior * burst:
        return False
    if not (low_t < ma20 and close_t >= ma20):
        return False
    if today_vol > prev_vol * shrink:
        return False
    kim_ok, _, _ = kim_straight_trend_pass(close, at_index=i)
    if not kim_ok:
        return False
    return True


def _leg_return(buy_px: float, sell_px: float) -> float:
    if buy_px <= 0 or sell_px <= 0:
        return 0.0
    gross = sell_px / buy_px
    net = gross * (1.0 - PULLBACK_SELL_COST) / (1.0 + PULLBACK_BUY_COST) - 1.0
    return float(net)


def run_pullback_timeline_backtest(
    df: pd.DataFrame,
    *,
    initial_cash: float,
    volume_burst_multiple: float,
    vol_shrink_limit: float,
    sell_timing_minutes: int = 0,
    code: str = "",
    name: str = "",
) -> PullbackTimelineResult:
    """
    기간 내 매 봉별 눌림목 신호를 전수 탐색해 복리 시뮬레이션.
    sell_timing_minutes: 현재 0(익일 시가)만 지원.
    """
    if sell_timing_minutes != 0:
        return PullbackTimelineResult(
            False,
            "현재 매도 시점은 0분(익일 시가)만 지원합니다.",
            "",
            0,
            float(initial_cash),
            float(initial_cash),
        )

    if df is None or df.empty:
        return PullbackTimelineResult(False, "차트 데이터가 없습니다.", "", 0, 0.0, initial_cash)

    work = ensure_datetime_index(df.copy()).sort_index()
    if len(work) < 120:
        return PullbackTimelineResult(
            False,
            "봉 수가 부족합니다(김직선 MA120 검증에 최소 120봉 필요).",
            "",
            0,
            float(initial_cash),
            float(initial_cash),
        )

    vol = pd.to_numeric(work["Volume"], errors="coerce")
    low = pd.to_numeric(work["Low"], errors="coerce")
    close = pd.to_numeric(work["Close"], errors="coerce")
    opn = pd.to_numeric(work["Open"], errors="coerce")

    equity = float(initial_cash)
    entries: list[tuple[str, float, float, float]] = []

    for i in range(119, len(work) - 1):
        if not pullback_signal_at_index(
            vol,
            low,
            close,
            i,
            volume_burst_multiple=volume_burst_multiple,
            vol_shrink_limit=vol_shrink_limit,
        ):
            continue
        buy_px = float(close.iloc[i])
        sell_px = float(opn.iloc[i + 1])
        if buy_px <= 0 or sell_px <= 0:
            continue
        leg_r = _leg_return(buy_px, sell_px)
        equity *= 1.0 + leg_r
        dt = work.index[i].strftime("%Y-%m-%d")
        entries.append((dt, buy_px, sell_px, leg_r * 100.0))

    n = len(entries)
    total_ret = (equity / float(initial_cash) - 1.0) * 100.0 if initial_cash > 0 else 0.0
    hdr = f"{name} ({code})".strip() if code or name else "선택 종목"
    lines = [
        f"■ {hdr}",
        f"■ 기간: {work.index[0].strftime('%Y-%m-%d')} ~ {work.index[-1].strftime('%Y-%m-%d')}",
        f"■ 세력 개입 배수: {volume_burst_multiple:g} | 눌림 거래량 비율: {vol_shrink_limit:g}",
        "■ v3.50 김직선 추세: 종가>MA120 · MA5≥MA10",
        f"■ 매수 수수료: {PULLBACK_BUY_COST * 100:.3f}% | 매도(세금 포함): {PULLBACK_SELL_COST * 100:.2f}%",
        f"■ 매도 시점: 0분(익일 시가)",
        "",
        f"• 총 누적 진입 횟수: {n}회",
        f"• 초기 자산: {initial_cash:,.0f} 원",
        f"• 기간 내 최종 복리 자산 평가액: {equity:,.0f} 원",
        f"• 기간 누적 수익률: {total_ret:+.2f} %",
        "",
    ]
    if entries:
        lines.append("— 진입 내역 (신호일 종가 매수 → 익일 시가 매도) —")
        if n > 40:
            lines.append(f"  (최근 40건만 표시 / 전체 {n}건)")
        for dt, bp, sp, lr in entries[-40:]:
            lines.append(
                f"  {dt}  매수 {bp:,.0f} → 매도 {sp:,.0f}  ({lr:+.2f}%)"
            )
    else:
        lines.append("• 해당 기간에 눌림목 3중 조건을 만족한 진입일이 없습니다.")

    return PullbackTimelineResult(
        True,
        None,
        "\n".join(lines),
        n,
        float(equity),
        float(initial_cash),
    )
