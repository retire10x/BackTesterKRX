"""
v10.1.0 장세 자동 분류 — 15:15 확정 지수 Fact 기반 (인간 예측 배제).

KOSPI·KOSDAQ 지수가 각각 MA5·MA20 대비 어디에 있는지로 momentum / swing / cash 판정.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

import pandas as pd

from src.live.live_engine import _now_kst

logger = logging.getLogger("MarketClassifier")

Regime = Literal["momentum", "swing", "cash"]

KOSPI_INDEX_PYKRX = "1001"
KOSDAQ_INDEX_PYKRX = "2001"
KOSPI_INDEX_FDR = "KS11"
KOSDAQ_INDEX_FDR = "KQ11"

MA_FAST = 5
MA_SLOW = 20
REGIME_CHECK_TIME = "15:15"
HISTORY_CALENDAR_DAYS = 90

IndexRegime = Regime


@dataclass(frozen=True)
class IndexRegimeSnapshot:
    """단일 지수의 현재가·이동평균·판정."""

    label: str
    ticker: str
    current: float
    ma5: float
    ma20: float
    regime: IndexRegime


def classify_index_level(*, current: float, ma5: float, ma20: float) -> IndexRegime:
    """단일 지수 판정 — current > MA5 & MA20 → momentum, both below → cash, else swing."""
    if not all(pd.notna(x) and x > 0 for x in (current, ma5, ma20)):
        return "swing"
    if current > ma5 and current > ma20:
        return "momentum"
    if current < ma5 and current < ma20:
        return "cash"
    return "swing"


def merge_index_regimes(kospi: IndexRegime, kosdaq: IndexRegime) -> Regime:
    """양 지수 병합 — 하나라도 cash면 cash, 둘 다 momentum만 momentum, 나머지 swing."""
    if "cash" in (kospi, kosdaq):
        return "cash"
    if kospi == "momentum" and kosdaq == "momentum":
        return "momentum"
    return "swing"


def compute_rolling_mas(closes: pd.Series, today_level: float) -> tuple[float, float]:
    """과거 종가 + 오늘 현재가를 포함한 MA5·MA20."""
    series = pd.to_numeric(closes, errors="coerce").dropna()
    if series.empty or not pd.notna(today_level) or today_level <= 0:
        return float("nan"), float("nan")
    combined = pd.concat([series, pd.Series([float(today_level)])], ignore_index=True)
    ma5 = float(combined.tail(MA_FAST).mean())
    ma20 = float(combined.tail(MA_SLOW).mean())
    return ma5, ma20


def _normalize_index_ohlcv(raw: pd.DataFrame) -> pd.DataFrame:
    work = raw.copy()
    if not isinstance(work.index, pd.DatetimeIndex):
        work.index = pd.to_datetime(work.index)
    work.index = work.index.normalize()
    work = work.sort_index()
    rename = {
        "종가": "close",
        "Close": "close",
        "close": "close",
    }
    for src, dst in rename.items():
        if src in work.columns:
            work = work.rename(columns={src: dst})
    if "close" not in work.columns:
        raise ValueError(f"지수 OHLCV에 close 컬럼 없음: {list(work.columns)}")
    work["close"] = pd.to_numeric(work["close"], errors="coerce")
    return work.dropna(subset=["close"])


def fetch_index_history_fdr(fdr_ticker: str, *, calendar_days: int = HISTORY_CALENDAR_DAYS) -> pd.DataFrame:
    """FinanceDataReader 지수 일봉 (종가)."""
    import FinanceDataReader as fdr  # type: ignore

    end = _now_kst().date()
    start = end - timedelta(days=int(calendar_days))
    raw = fdr.DataReader(fdr_ticker, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    if raw is None or getattr(raw, "empty", True):
        raise RuntimeError(f"FDR 지수 {fdr_ticker} OHLCV 비어 있음")
    return _normalize_index_ohlcv(raw)


def fetch_index_history_pykrx(ticker: str, *, calendar_days: int = HISTORY_CALENDAR_DAYS) -> pd.DataFrame:
    """pykrx 지수 일봉 (종가)."""
    from pykrx import stock as pykrx_stock  # type: ignore

    end = _now_kst().date()
    start = end - timedelta(days=int(calendar_days))
    s_ymd = start.strftime("%Y%m%d")
    e_ymd = end.strftime("%Y%m%d")
    raw = pykrx_stock.get_index_ohlcv_by_date(s_ymd, e_ymd, str(ticker))
    if raw is None or getattr(raw, "empty", True):
        raise RuntimeError(f"pykrx 지수 {ticker} OHLCV 비어 있음")
    return _normalize_index_ohlcv(raw)


def fetch_index_history(
    *,
    pykrx_ticker: str,
    fdr_ticker: str,
    calendar_days: int = HISTORY_CALENDAR_DAYS,
) -> pd.DataFrame:
    """FDR 우선 · pykrx 폴백."""
    try:
        return fetch_index_history_fdr(fdr_ticker, calendar_days=calendar_days)
    except Exception as exc:
        logger.debug("FDR 지수 %s 이력 실패: %s — pykrx 폴백", fdr_ticker, exc)
    return fetch_index_history_pykrx(pykrx_ticker, calendar_days=calendar_days)


def fetch_today_index_level(
    *,
    pykrx_ticker: str,
    fdr_ticker: str,
    history: pd.DataFrame | None = None,
    gateway: object | None = None,
) -> float:
    """
    오늘 15:15 부근 지수 레벨.
    FDR intraday → pykrx 당일봉 → gateway(주식 API 폴백) → 전일 종가 순.
    """
    today = _now_kst().date()

    try:
        import FinanceDataReader as fdr  # type: ignore

        start = (today - timedelta(days=5)).strftime("%Y-%m-%d")
        end = today.strftime("%Y-%m-%d")
        fdr_df = fdr.DataReader(fdr_ticker, start, end)
        if fdr_df is not None and not fdr_df.empty:
            fdr_df.index = pd.to_datetime(fdr_df.index).normalize()
            row = fdr_df.iloc[-1]
            if fdr_df.index[-1].date() == today:
                px = float(row.get("Close", row.get("close", 0)))
                if px > 0:
                    return px
    except Exception as exc:
        logger.debug("FDR 지수 %s 조회 실패: %s", fdr_ticker, exc)

    if history is None:
        history = fetch_index_history(pykrx_ticker=pykrx_ticker, fdr_ticker=fdr_ticker)
    if not history.empty and history.index[-1].date() == today:
        px = float(history["close"].iloc[-1])
        if px > 0:
            return px

    if gateway is not None and hasattr(gateway, "fetch_quote_bar") and not getattr(gateway, "dry_run", True):
        pass  # 지수 전용 KIS API 미연동 — 추후 확장

    px = float(history["close"].iloc[-1])
    if px <= 0:
        raise RuntimeError(f"지수 {pykrx_ticker} 유효 종가 없음")
    logger.warning("지수 %s 당일 실시간 미확보 — 전일 종가 %.2f 사용", pykrx_ticker, px)
    return px


def classify_single_index(
    *,
    label: str,
    pykrx_ticker: str,
    fdr_ticker: str,
    gateway: object | None = None,
) -> IndexRegimeSnapshot:
    hist = fetch_index_history(pykrx_ticker=pykrx_ticker, fdr_ticker=fdr_ticker)
    prior = hist.iloc[:-1] if len(hist) > 1 and hist.index[-1].date() == _now_kst().date() else hist
    current = fetch_today_index_level(
        pykrx_ticker=pykrx_ticker,
        fdr_ticker=fdr_ticker,
        history=hist,
        gateway=gateway,
    )
    ma5, ma20 = compute_rolling_mas(prior["close"], current)
    regime = classify_index_level(current=current, ma5=ma5, ma20=ma20)
    return IndexRegimeSnapshot(
        label=label,
        ticker=pykrx_ticker,
        current=current,
        ma5=ma5,
        ma20=ma20,
        regime=regime,
    )


def check_market_regime(*, gateway: object | None = None) -> Regime:
    """
    [v10.1 핵심] 15:15 확정 Fact 기반 장세 판정.
    KOSPI·KOSDAQ 양 지수 MA5/MA20 대비 위치로 momentum / swing / cash 반환.
    """
    kospi = classify_single_index(
        label="KOSPI",
        pykrx_ticker=KOSPI_INDEX_PYKRX,
        fdr_ticker=KOSPI_INDEX_FDR,
        gateway=gateway,
    )
    kosdaq = classify_single_index(
        label="KOSDAQ",
        pykrx_ticker=KOSDAQ_INDEX_PYKRX,
        fdr_ticker=KOSDAQ_INDEX_FDR,
        gateway=gateway,
    )
    merged = merge_index_regimes(kospi.regime, kosdaq.regime)
    logger.info(
        "📊 [v10.1 장세] KOSPI %.2f (MA5 %.2f · MA20 %.2f) → %s | "
        "KOSDAQ %.2f (MA5 %.2f · MA20 %.2f) → %s | 최종=%s",
        kospi.current,
        kospi.ma5,
        kospi.ma20,
        kospi.regime.upper(),
        kosdaq.current,
        kosdaq.ma5,
        kosdaq.ma20,
        kosdaq.regime.upper(),
        merged.upper(),
    )
    return merged


def describe_regime(regime: Regime) -> str:
    return {
        "momentum": "상승장 — Momentum 엔진",
        "swing": "횡보장 — Swing 엔진",
        "cash": "하락/위험장 — 신규매수 Blackout",
    }.get(regime, regime)


def regime_for_closes(closes: pd.Series) -> IndexRegime:
    """일봉 종가 시리즈 말일 기준 단일 지수 판정 (백테스트용)."""
    series = pd.to_numeric(closes, errors="coerce").dropna()
    if len(series) < MA_SLOW:
        return "swing"
    current = float(series.iloc[-1])
    prior = series.iloc[:-1]
    ma5, ma20 = compute_rolling_mas(prior, current)
    return classify_index_level(current=current, ma5=ma5, ma20=ma20)


def classify_regime_at_date(
    kospi_closes: pd.Series,
    kosdaq_closes: pd.Series,
    as_of: pd.Timestamp,
) -> Regime:
    """특정 일자 종가 시점 Fact 기반 통합 장세."""
    dt = pd.Timestamp(as_of).normalize()
    ks = pd.to_numeric(kospi_closes.loc[:dt], errors="coerce").dropna()
    kq = pd.to_numeric(kosdaq_closes.loc[:dt], errors="coerce").dropna()
    if ks.empty or kq.empty:
        return "swing"
    return merge_index_regimes(regime_for_closes(ks), regime_for_closes(kq))


def load_index_close_series(
    *,
    fdr_ticker: str,
    start: str,
    end: str,
) -> pd.Series:
    """FDR 지수 종가 시리즈 (백테스트 파이프라인)."""
    import FinanceDataReader as fdr  # type: ignore

    raw = fdr.DataReader(fdr_ticker, start, end)
    if raw is None or getattr(raw, "empty", True):
        raise RuntimeError(f"FDR 지수 {fdr_ticker} 데이터 없음 ({start}~{end})")
    df = _normalize_index_ohlcv(raw)
    return df["close"].sort_index()


def build_regime_schedule(
    bdays: pd.DatetimeIndex,
    kospi_closes: pd.Series,
    kosdaq_closes: pd.Series,
) -> dict[str, Regime]:
    """영업일별 v10.1 프리셋 스케줄 — 타임머신 백테스트 SSOT."""
    schedule: dict[str, Regime] = {}
    for dt in pd.DatetimeIndex(bdays).normalize():
        schedule[dt.strftime("%Y-%m-%d")] = classify_regime_at_date(
            kospi_closes, kosdaq_closes, dt
        )
    return schedule


def summarize_regime_schedule(schedule: dict[str, Regime]) -> dict[str, int]:
    counts = {"momentum": 0, "swing": 0, "cash": 0}
    for r in schedule.values():
        counts[str(r)] = counts.get(str(r), 0) + 1
    return counts
