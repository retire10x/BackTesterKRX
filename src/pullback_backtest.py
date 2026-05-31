"""
v3.40: 단일 종목 주도주 눌림목 타임라인 백테스트.
종가 매수(신호일 t) → 익일 시가(0분) 매도, 복리 누적.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from src.data_loader import (
    ensure_datetime_index,
    leader_pullback_prev_day_yang,
    preprocess_clean_timeline,
)
from src.filters import (
    PULLBACK_MIN_OHLCV_BARS,
    kim_straight_trend_pass,
    leader_pullback_pass2_ma20_or_center,
    pass_disparity_lock,
)

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
    ticker_code: str = ""
    ticker_name: str = ""
    period_start: str = ""
    period_end: str = ""
    trade_history: list[dict[str, Any]] = field(default_factory=list)
    metrics_dict: dict[str, float] = field(default_factory=dict)


def pullback_signal_at_index(
    vol: pd.Series,
    low: pd.Series,
    high: pd.Series,
    close: pd.Series,
    opn: pd.Series,
    i: int,
    *,
    volume_burst_multiple: float,
    vol_shrink_limit: float,
    use_momentum_filter: bool,
) -> bool:
    """봉 인덱스 i 를 신호일(t)로 v3.30·v3.80 눌림목 조건 판정."""
    if i < 21:
        return False
    vol_ma20_prior = float(vol.iloc[i - 21 : i - 1].mean())
    prev_vol = float(vol.iloc[i - 1])
    today_vol = float(vol.iloc[i])
    ma20 = float(close.iloc[i - 19 : i + 1].mean())
    ma5 = float(close.iloc[i - 4 : i + 1].mean())
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
    prev_open = float(opn.iloc[i - 1])
    prev_close = float(close.iloc[i - 1])
    prev_high = float(high.iloc[i - 1])
    prev_low = float(low.iloc[i - 1])

    if prev_vol <= vol_ma20_prior * burst:
        return False
    if not leader_pullback_prev_day_yang(prev_open, prev_close):
        return False
    if not leader_pullback_pass2_ma20_or_center(
        low_t=low_t,
        close_t=close_t,
        ma20=ma20,
        prev_high=prev_high,
        prev_low=prev_low,
    ):
        return False
    if not pass_disparity_lock(close_t, ma5, ma20):
        return False
    if today_vol > prev_vol * shrink:
        return False
    kim_ok, long_ok, short_ok = kim_straight_trend_pass(close, at_index=i)
    if not long_ok:
        return False
    if use_momentum_filter and not short_ok:
        return False
    return True


def _leg_return(buy_px: float, sell_px: float) -> float:
    if buy_px <= 0 or sell_px <= 0:
        return 0.0
    gross = sell_px / buy_px
    net = gross * (1.0 - PULLBACK_SELL_COST) / (1.0 + PULLBACK_BUY_COST) - 1.0
    return float(net)


def _compute_backtest_metrics(
    trade_history: list[dict[str, Any]],
) -> dict[str, float]:
    """v4.60: 승률·손익비(PF) — 하단 매매 로그와 상단 요약 대칭."""
    n = len(trade_history)
    if n == 0:
        return {"total_trades": 0.0, "win_rate": 0.0, "profit_factor": 0.0}
    pnls = [float(t.get("pnl_ratio", 0.0) or 0.0) for t in trade_history]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate = len(wins) / n * 100.0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        pf = gross_win / gross_loss
    elif gross_win > 0:
        pf = float("inf")
    else:
        pf = 0.0
    return {
        "total_trades": float(n),
        "win_rate": float(win_rate),
        "profit_factor": float(pf),
    }


def run_pullback_timeline_backtest(
    df: pd.DataFrame,
    *,
    initial_cash: float,
    volume_burst_multiple: float,
    vol_shrink_limit: float,
    use_momentum_filter: bool,
    sell_timing_minutes: int = 0,
    code: str = "",
    name: str = "",
    period_start: str | None = None,
    period_end: str | None = None,
) -> PullbackTimelineResult:
    """
    기간 내 매 봉별 눌림목 신호를 전수 탐색해 복리 시뮬레이션.
    v4.50: period_start/end — 버퍼 풀(df) 위 MA120 워밍업 후 사용자 구간만 진입 집계.
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
            ticker_code=code,
            ticker_name=name,
            period_start=str(period_start or "")[:10],
            period_end=str(period_end or "")[:10],
        )

    if df is None or df.empty:
        return PullbackTimelineResult(False, "차트 데이터가 없습니다.", "", 0, 0.0, initial_cash)

    work = preprocess_clean_timeline(df.copy())
    if len(work) < PULLBACK_MIN_OHLCV_BARS:
        return PullbackTimelineResult(
            False,
            f"봉 수가 부족합니다(장기 대세 MA60·MA120 검증에 최소 {PULLBACK_MIN_OHLCV_BARS}봉 필요).",
            "",
            0,
            float(initial_cash),
            float(initial_cash),
        )

    vol = pd.to_numeric(work["Volume"], errors="coerce")
    low = pd.to_numeric(work["Low"], errors="coerce")
    high = pd.to_numeric(work["High"], errors="coerce")
    close = pd.to_numeric(work["Close"], errors="coerce")
    opn = pd.to_numeric(work["Open"], errors="coerce")

    ts0 = (
        pd.Timestamp(period_start).normalize()
        if period_start
        else work.index[0].normalize()
    )
    ts1 = (
        pd.Timestamp(period_end).normalize()
        if period_end
        else work.index[-1].normalize()
    )

    equity = float(initial_cash)
    trade_history: list[dict[str, Any]] = []

    for i in range(119, len(work) - 1):
        bar_ts = work.index[i].normalize()
        if bar_ts < ts0 or bar_ts > ts1:
            continue
        if not pullback_signal_at_index(
            vol,
            low,
            high,
            close,
            opn,
            i,
            volume_burst_multiple=volume_burst_multiple,
            vol_shrink_limit=vol_shrink_limit,
            use_momentum_filter=use_momentum_filter,
        ):
            continue
        buy_px = float(close.iloc[i])
        sell_px = float(opn.iloc[i + 1])
        if buy_px <= 0 or sell_px <= 0:
            continue
        leg_r = _leg_return(buy_px, sell_px)
        equity *= 1.0 + leg_r
        entry_dt = work.index[i].strftime("%Y-%m-%d")
        exit_dt = work.index[i + 1].strftime("%Y-%m-%d")
        pnl_pct = leg_r * 100.0
        trade_history.append(
            {
                "entry_date": entry_dt,
                "entry_price": buy_px,
                "exit_date": exit_dt,
                "exit_price": sell_px,
                "pnl_ratio": pnl_pct,
            }
        )

    n = len(trade_history)
    metrics_dict = _compute_backtest_metrics(trade_history)
    total_ret = (equity / float(initial_cash) - 1.0) * 100.0 if initial_cash > 0 else 0.0
    hdr = f"{name} ({code})".strip() if code or name else "선택 종목"
    lines = [
        f"■ {hdr}",
        f"■ 기간: {ts0.strftime('%Y-%m-%d')} ~ {ts1.strftime('%Y-%m-%d')}",
        f"■ 세력 개입 배수: {volume_burst_multiple:g} | 눌림 거래량 비율: {vol_shrink_limit:g}",
        "■ v4.15 Pass2: MA20 터치 회복 OR (MA20 위 + t-1 중심선 수호)",
        (
            "■ v3.95 추세: 종가>MA60·MA120·MA60>MA120 · MA5≥MA10"
            if use_momentum_filter
            else "■ v3.95 추세: Perfect Trend (MA5≥MA10 스킵)"
        ),
        f"■ 매수 수수료: {PULLBACK_BUY_COST * 100:.3f}% | 매도(세금 포함): {PULLBACK_SELL_COST * 100:.2f}%",
        f"■ 매도 시점: 0분(익일 시가)",
        "",
        f"• 총 누적 진입 횟수: {n}회",
        f"• 초기 자산: {initial_cash:,.0f} 원",
        f"• 기간 내 최종 복리 자산 평가액: {equity:,.0f} 원",
        f"• 기간 누적 수익률: {total_ret:+.2f} %",
        "",
    ]
    if trade_history:
        lines.append("— 진입 내역 (신호일 종가 매수 → 익일 시가 매도) —")
        if n > 40:
            lines.append(f"  (최근 40건만 표시 / 전체 {n}건)")
        for tr in trade_history[-40:]:
            lines.append(
                f"  {tr['entry_date']}  매수 {tr['entry_price']:,.0f} → "
                f"매도 {tr['exit_price']:,.0f}  ({tr['pnl_ratio']:+.2f}%)"
            )
    else:
        lines.append("• 해당 기간에 눌림목 조건을 만족한 진입일이 없습니다.")

    return PullbackTimelineResult(
        True,
        None,
        "\n".join(lines),
        n,
        float(equity),
        float(initial_cash),
        ticker_code=str(code or "").zfill(6),
        ticker_name=str(name or "").strip(),
        period_start=ts0.strftime("%Y-%m-%d"),
        period_end=ts1.strftime("%Y-%m-%d"),
        trade_history=trade_history,
        metrics_dict=metrics_dict,
    )
