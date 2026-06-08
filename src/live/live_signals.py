"""
v5.5.2 진입·청산 신호 — portfolio_manager_v5 와 동일 수학 (실시간 OHLCV DataFrame 입력).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.live.live_config import LiveStrategyConfig


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    col_map = {c.lower(): c for c in work.columns}
    rename = {}
    for std in ("open", "high", "low", "close", "volume"):
        for k, orig in col_map.items():
            if k == std or k.startswith(std):
                rename[orig] = std
                break
    if rename:
        work = work.rename(columns=rename)
    if "close" not in work.columns and "Close" in work.columns:
        work = work.rename(columns={"Close": "close", "Open": "open", "High": "high", "Low": "low", "Volume": "volume"})
    return work.sort_index()


def min_history_bars(strat: LiveStrategyConfig) -> int:
    """
    explain_entry_signal이 요구하는 ohlcv_df 최소 행 수.

    close_s_confirmed = close_s.iloc[:-1] (당일 미완성 캔들 제외)를 기준으로 산출하므로
    각 조건에서 confirmed 시리즈에 필요한 행 수 + 1(당일 캔들)이 실제 필요량이다.

    - 듀얼 MA 우상향: ma[-1]·ma[-2] 모두 필요 → confirmed에 w+1행 → ohlcv에 w+2행
    - price_above_ma: ma[-1]만 필요 → confirmed에 w행 → ohlcv에 w+1행
    - MA20 반전(yesterday_ma): confirmed에 window+1행 → ohlcv에 window+2행
    """
    mf = strat.macro_filter
    need = strat.lookback_window + 2  # MA20 반전 + past_close
    for w in mf.ma_lines:            # 듀얼 슬로프: ma[-2]까지 필요
        need = max(need, w + 2)
    if mf.price_above_ma > 0:        # price_above_ma: ma[-1]만 필요
        need = max(need, mf.price_above_ma + 1)
    return need


def passes_macro_filter(close_s: pd.Series, today_close: float, strat: LiveStrategyConfig) -> bool:
    mf = strat.macro_filter
    for w in mf.ma_lines:
        ma_s = close_s.rolling(window=w).mean()
        ma_today = float(ma_s.iloc[-1])
        ma_yesterday = float(ma_s.iloc[-2])
        if not np.isfinite(ma_today) or not np.isfinite(ma_yesterday):
            return False
        if ma_today <= ma_yesterday:
            return False
    if mf.price_above_ma > 0:
        ma_floor = float(close_s.rolling(window=mf.price_above_ma).mean().iloc[-1])
        if not np.isfinite(ma_floor) or today_close <= ma_floor:
            return False
    return True


def _macro_filter_failure_reason(
    close_s: pd.Series, today_close: float, strat: LiveStrategyConfig
) -> str | None:
    """통과 시 None, 탈락 시 사유 문자열."""
    mf = strat.macro_filter
    for w in mf.ma_lines:
        ma_s = close_s.rolling(window=w).mean()
        ma_today = float(ma_s.iloc[-1])
        ma_yesterday = float(ma_s.iloc[-2])
        if not np.isfinite(ma_today) or not np.isfinite(ma_yesterday):
            return f"MA{w} 산출 불가(데이터 부족)"
        if ma_today <= ma_yesterday:
            return f"MA{w} 우상향 미충족(전일 {ma_yesterday:,.0f} ≥ 당일 {ma_today:,.0f})"
    if mf.price_above_ma > 0:
        ma_floor = float(close_s.rolling(window=mf.price_above_ma).mean().iloc[-1])
        if not np.isfinite(ma_floor):
            return f"MA{mf.price_above_ma} 산출 불가"
        if today_close <= ma_floor:
            return (
                f"{mf.price_above_ma}일선 아래 위치 "
                f"(종가 {today_close:,.0f} ≤ MA{mf.price_above_ma} {ma_floor:,.0f})"
            )
    return None


def explain_entry_signal(ohlcv_df: pd.DataFrame, strat: LiveStrategyConfig) -> tuple[bool, str]:
    """
    진입 가능 여부 + 탈락/통과 사유 (SOP 수동 검증용).

    조건 (전부 AND):
      1. 당일 종가 ≤ 확정 MA20  — 20일선 아래 바닥권 진입 확인
      2. 확정 MA20 우상향        — MA20[-1] > MA20[-2] (단기 추세 인터록)
      3. 변곡 돌파               — 당일 종가 > 20영업일 전(확정) 종가
      4. 매크로 필터             — MA60/120 듀얼 우상향 · 종가 > price_above_ma 선

    [인터록] close_s_confirmed = close_s.iloc[:-1]
    당일 미완성 캔들을 제외한 확정 종가 기준으로 모든 MA를 산출한다.
    today_close는 별도 보관하여 조건 1·3에 사용한다.
    """
    window = strat.lookback_window
    need = min_history_bars(strat)
    if len(ohlcv_df) < need:
        return False, f"일봉 부족 ({len(ohlcv_df)}봉 < 필요 {need}봉)"

    work = _normalize_ohlcv(ohlcv_df)
    if "close" not in work.columns:
        return False, "OHLCV 종가 컬럼 없음"

    close_s = pd.to_numeric(work["close"], errors="coerce")
    today_close = float(close_s.iloc[-1])
    if not np.isfinite(today_close) or today_close <= 0:
        return False, "당일 종가 무효"

    # 당일 미완성 캔들 제외 확정 시리즈
    close_s_confirmed = close_s.iloc[:-1]
    if len(close_s_confirmed) < window + 1:
        return False, f"확정 일봉 부족 ({len(close_s_confirmed)}봉 < 필요 {window + 1}봉)"

    ma_s = close_s_confirmed.rolling(window=window).mean()
    ma_now = float(ma_s.iloc[-1])
    ma_prev = float(ma_s.iloc[-2])
    if not np.isfinite(ma_now) or not np.isfinite(ma_prev):
        return False, f"MA{window} 산출 불가"

    # 조건 1: 당일 종가 ≤ 확정 MA20 (20일선 아래 바닥권)
    if today_close > ma_now:
        return (
            False,
            f"MA{window} 반전 미충족 — 당일 종가({today_close:,.0f})가 "
            f"MA{window}({ma_now:,.0f}) 위에 있음(이미 이격)",
        )

    # 조건 2: 확정 MA20 우상향 (단기 추세 인터록)
    if ma_now <= ma_prev:
        return (
            False,
            f"MA{window} 하락 추세 — MA{window}({ma_now:,.0f}) ≤ 전일 MA{window}({ma_prev:,.0f})",
        )

    # 조건 3: 변곡 돌파 — 당일 종가 > 20영업일 전 확정 종가
    past_close = float(close_s_confirmed.iloc[-window])
    if not np.isfinite(past_close):
        return False, f"{window}영업일 전 종가 산출 불가"
    if today_close <= past_close:
        return (
            False,
            f"변곡 미충족 — 당일 종가({today_close:,.0f}) ≤ "
            f"{window}일 전 종가({past_close:,.0f})",
        )

    # 조건 4: 매크로 필터 (MA60/120 듀얼 우상향 · 종가 > price_above_ma선)
    macro_fail = _macro_filter_failure_reason(close_s_confirmed, today_close, strat)
    if macro_fail:
        return False, macro_fail

    return True, (
        f"통과 — MA{window} 반전·우상향·변곡·듀얼 MA·종가>MA{strat.macro_filter.price_above_ma}"
    )


def is_ma_inflection_entry(ohlcv_df: pd.DataFrame, strat: LiveStrategyConfig) -> bool:
    ok, _ = explain_entry_signal(ohlcv_df, strat)
    return ok


def evaluate_hit_and_run_exit(
    *,
    entry_price: float,
    high: float,
    low: float,
    close: float,
    hold_days: int,
    strat: LiveStrategyConfig,
) -> tuple[float, str] | None:
    """손절 → 익절 → 타임스탑 (장중 H/L 우선)."""
    target_px = entry_price * (1.0 + strat.target_profit_ratio)
    stop_px = entry_price * (1.0 - strat.stop_loss_ratio)

    if low <= stop_px:
        return stop_px, "STOP_LOSS"
    if high >= target_px:
        return target_px, "TAKE_PROFIT"
    if hold_days >= strat.max_hold_days:
        return close, "TIME_STOP"
    return None
