"""
일봉 기준 종목 스크리너: 설정 종료일 이전 영업일 구간만 사용(미래 참조 금지).
최근 N거래일 변동성(ATR 또는 수익률 표준편차)·거래대금(Σ 거래량×종가)으로 상위 종목 후보 산출.
FinanceDataReader 기반으로 GUI 비의존.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from .data_loader import ensure_datetime_index, fetch_filtered_universe, load_ohlcv

# 일봉 ATR 안정 추정 및 20영업일 윈도우를 위한 캘린더 상 여유(fetch 상한 스크린 대비)
_SCR_FETCH_CALENDAR_DAYS = 200
MAX_SCREEN_WORKERS = 6


@dataclass(frozen=True)
class ScreenerEntry:
    code: str
    name: str
    volatility_raw: float
    turnover_krw_sum: float
    combined_score: float


def default_screener_config() -> dict:
    """settings.yaml 우선 병합용 기본 블록."""
    return {
        "enabled": False,
        "lookback_trading_days": 20,
        "top_n": 30,
        "volatility_metric": "atr14",  # atr14 | std_return
        "combine": "sum_rank_pct",   # 각 지표 순위분위합(둘 다 클수록 상위)
    }


def atr_ratio_series(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """종가 대비 Wilder TR 기반 ATR 비율(대략 % 스케일)."""
    h, l, c = high.astype(float), low.astype(float), close.astype(float)
    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)

    atr = pd.Series(np.nan, index=tr.index, dtype=float)
    if len(tr.dropna()) < period:
        return atr
    # Wilder 평균: 첫 period 구간 단순 평균 후 이평식
    first = float(tr.iloc[:period].mean())
    atr.iloc[period - 1] = first
    for i in range(period, len(tr)):
        atr.iloc[i] = (atr.iloc[i - 1] * (period - 1) + tr.iloc[i]) / period
    out = (atr / c.replace(0.0, np.nan)) * 100.0
    return out


def _daily_metrics_slice(
    df: pd.DataFrame,
    *,
    end_ts: pd.Timestamp,
    lookback: int,
    volatility_metric: str,
) -> tuple[float | None, float | None]:
    """
    end_ts까지의 일봉만 사용하여 [마지막 lookback거래일] 구간 변동성·거래대금 합 계산.
    ATR은 전체 로드 구간으로 워밍업한 뒤, 마지막 lookback구간의 ATR/종가 비율 평균만 사용.
    """
    z = df.copy()
    z = ensure_datetime_index(z)
    cols = {"Open", "High", "Low", "Close", "Volume"}
    if not cols.issubset(set(z.columns)):
        return None, None

    mask = z.index.normalize() <= end_ts.normalize()
    z = z.loc[mask]
    req = {"Open", "High", "Low", "Close", "Volume"}
    z = z.dropna(subset=list(req)).copy()
    metric = volatility_metric.strip().lower()
    atr_period = 14
    # ATR은 lookback 이전 봉까지 포함해 추정해야 안정적이다.
    min_len = lookback + (atr_period if metric in ("atr", "atr14") else 0)
    if len(z) < min_len:
        return None, None

    tail = z.iloc[-lookback:]
    turnover = (
        pd.to_numeric(tail["Volume"], errors="coerce").fillna(0)
        * pd.to_numeric(tail["Close"], errors="coerce").fillna(0)
    ).sum()

    if turnover <= 0 or not np.isfinite(turnover):
        return None, None

    if metric == "std_return":
        cl = pd.to_numeric(tail["Close"], errors="coerce")
        lr = np.log(cl / cl.shift(1)).dropna()
        if len(lr) < max(lookback // 2, 5):
            return None, None
        vol = float(lr.std(ddof=0))
    elif metric in ("atr", "atr14"):
        atrp = atr_ratio_series(
            pd.to_numeric(z["High"], errors="coerce"),
            pd.to_numeric(z["Low"], errors="coerce"),
            pd.to_numeric(z["Close"], errors="coerce"),
            period=atr_period,
        )
        atr_tail = atrp.dropna().iloc[-lookback:]
        if atr_tail.empty:
            return None, None
        vol = float(atr_tail.mean())
    else:
        raise ValueError(f"지원하지 않는 volatility_metric: {volatility_metric}")

    if not np.isfinite(vol):
        return None, None

    return float(vol), float(turnover)


def _screen_fetch_start(end_date: str) -> str:
    t = pd.Timestamp(str(end_date).strip()[:10])
    return (t - pd.Timedelta(days=_SCR_FETCH_CALENDAR_DAYS)).strftime("%Y-%m-%d")


def rank_entries_by_dual_high(
    raw_rows: list[tuple[str, str, float, float]],
) -> list[ScreenerEntry]:
    """
    두 지표 모두 '클수록 좋은' 순으로 순위합(백분위)으로 정렬합니다.
    """
    if not raw_rows:
        return []
    vols = np.array([x[2] for x in raw_rows], dtype=float)
    turnovers = np.array([x[3] for x in raw_rows], dtype=float)
    # pandas rank pct: 높은 값이 큰 순위값
    v_rank = pd.Series(vols).rank(pct=True, method="average", ascending=True)
    t_rank = pd.Series(turnovers).rank(pct=True, method="average", ascending=True)
    scores = ((v_rank + t_rank) / 2.0).to_numpy(dtype=float)
    zipped = sorted(
        (
            (
                raw_rows[i][0],
                raw_rows[i][1],
                float(vols[i]),
                float(turnovers[i]),
                float(scores[i]),
            )
            for i in range(len(raw_rows))
        ),
        key=lambda r: (-r[4], r[0]),
    )
    return [
        ScreenerEntry(code=a, name=b, volatility_raw=c, turnover_krw_sum=d, combined_score=e)
        for a, b, c, d, e in zipped
    ]


def _load_one_candidate(
    code: str,
    name: str,
    fetch_start: str,
    end_date: str,
    lookback: int,
    volatility_metric: str,
) -> tuple[str, str, float, float] | None:
    df = load_ohlcv(code, fetch_start, end_date)
    if df is None or df.empty:
        return None
    end_ts = pd.Timestamp(str(end_date).strip()[:10])
    v, tv = _daily_metrics_slice(
        df, end_ts=end_ts, lookback=lookback, volatility_metric=volatility_metric
    )
    if v is None or tv is None:
        return None
    return (code, name, v, tv)


def screen_universe(
    *,
    market: str,
    keyword: str,
    end_date: str,
    lookback_trading_days: int,
    top_n: int,
    volatility_metric: str,
    progress_cb: Callable[[int, int, str], None] | None = None,
    max_workers: int = MAX_SCREEN_WORKERS,
) -> list[ScreenerEntry]:
    """
    키워드로 좁힌 시장 유니버스에 대해 스크리닝 후 상위 top_n 반환.
    progress_cb(done_count, total, last_code) — 스레드에서 호출 시 GUI는 after로 래핑 권장.
    """
    cand = fetch_filtered_universe(market, keyword)
    if not cand:
        return []
    fetch_start = _screen_fetch_start(end_date)
    items = sorted(cand.items(), key=lambda x: x[0])
    total = len(items)
    raw: list[tuple[str, str, float, float]] = []
    done = 0

    def _one(pair: tuple[str, str]) -> tuple[str, str, float, float] | None:
        code, name = pair
        return _load_one_candidate(
            code, name, fetch_start, end_date, lookback_trading_days, volatility_metric
        )

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 12))) as ex:
        futures = {ex.submit(_one, p): p[0] for p in items}
        for fut in as_completed(futures):
            done += 1
            code = futures[fut]
            if progress_cb is not None:
                progress_cb(done, total, code)
            try:
                row = fut.result()
            except Exception:
                row = None
            if row is not None:
                raw.append(row)

    ranked = rank_entries_by_dual_high(raw)
    return ranked[: max(1, int(top_n))]


def summary_line_for_entry(e: ScreenerEntry) -> str:
    return (
        f"{e.code} {e.name} | "
        f"vol={e.volatility_raw:.6g} | "
        f"거래대금합(원)={e.turnover_krw_sum:,.0f} | "
        f"score={e.combined_score:.4f}"
    )
