"""
일봉 기준 스크리너: 설정 종료일 이전 영업일 구간만 사용(미래 참조 금지).
**종가 < MA120 역배열 종목 사전 제외 기능 포함** — 랭킹 산출 전 종가와 120일 단순이평 비교해 하향 배열이면 탈락.

최근 N거래일 **ATR%(14)·거래대금** 및 **고점 대비 낙폭(%)**·**거래량 감소 지표**를 순위분위 융합해 눌림목형 후보 상위 산출.
FinanceDataReader 기반으로 GUI 비의존.

변동성은 **항상 atr14**(최근 14영업일 True Range Wilder 평균 대비 종가 %)로 고정한다.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from .data_loader import (
    ensure_datetime_index,
    fetch_filtered_universe,
    fetch_listing_market_cap_krw_by_code,
    load_ohlcv,
)

# 일봉 ATR·MA120·20영업일 윈도우 분석을 위해 충분히 당김(저장소 휴장일 반영)
_SCR_FETCH_CALENDAR_DAYS = 400
SCREEN_MA_LOOKBACK = 120  # 종가 < MA120 역배열 종목 스크린 랭킹 단계 제외
SCREEN_MA20_WINDOW = 20
MA_PAIR_SLOPE_LOOKBACK = 5
# 스코어링 전 하드 필터 시가총액 하한(원): FDR 상장표 스냅샷 근사
SCREEN_MIN_MARKET_CAP_KRW_DEFAULT = int(3000 * (10**8))  # 3000억 원
MAX_SCREEN_WORKERS = 6

_OHLCV_COLS_REQ = frozenset({"Open", "High", "Low", "Close", "Volume"})


def _ols_slope_beta1_mini(y: np.ndarray) -> float | None:
    """X = 0..n-1 에 대한 단순 OLS 기울기."""
    y = np.asarray(y, dtype=float)
    n = y.size
    if n < 2 or not np.all(np.isfinite(y)):
        return None
    x = np.arange(n, dtype=float)
    xm, ym = float(x.mean()), float(y.mean())
    denom = float(np.sum((x - xm) ** 2))
    if denom <= 1e-18:
        return None
    return float(np.sum((x - xm) * (y - ym)) / denom)


def _pass_screener_ma_pair_trend(
    last_ma20: float,
    last_ma120: float,
    slope_source: pd.DataFrame,
) -> bool:
    """
    MA20 대 MA120 우위 또는, 두 이평 모두 최근 구간 우상향(OLS 기울기 > 0).
    스코어링 전 단계에서만 사용한다.
    """
    if not (np.isfinite(last_ma20) and np.isfinite(last_ma120) and last_ma120 > 0):
        return False
    if last_ma20 > last_ma120:
        return True
    if len(slope_source) < MA_PAIR_SLOPE_LOOKBACK:
        return False
    tail = slope_source.iloc[-MA_PAIR_SLOPE_LOOKBACK:]
    ys20 = tail["MA20"].to_numpy(dtype=float)
    ys120 = tail["MA120"].to_numpy(dtype=float)
    s20 = _ols_slope_beta1_mini(ys20)
    s120 = _ols_slope_beta1_mini(ys120)
    if s20 is None or s120 is None:
        return False
    return s20 > 0.0 and s120 > 0.0


def _prepare_screener_ohlcv(df: pd.DataFrame) -> pd.DataFrame | None:
    """OHLCV 수치형·결측 제거 후 과거→현재 순 정렬 및 동일 타임스탬프 행 통합."""
    z = ensure_datetime_index(df.copy())
    if not _OHLCV_COLS_REQ.issubset(set(z.columns)):
        return None

    for col in _OHLCV_COLS_REQ:
        z[col] = pd.to_numeric(z[col], errors="coerce")
    z = z.dropna(subset=list(_OHLCV_COLS_REQ)).sort_index(ascending=True)
    if z.empty:
        return None

    dup = z.index.duplicated(keep="last")
    if bool(dup.any()):
        z = z.loc[~dup].copy()
    return z


def _index_mask_through_end_calendar(index: pd.Index, end_date_str: str) -> np.ndarray:
    """종료일(포함, 캘린더 문자열 기준)·tz 보정까지 반영해 인덱스 길이의 bool 마스크."""
    end_k = pd.Timestamp(str(end_date_str).strip()[:10]).strftime("%Y-%m-%d")
    ix_raw = pd.DatetimeIndex(pd.to_datetime(index, errors="coerce"))
    ix_cmp = (
        ix_raw.tz_convert("Asia/Seoul")
        if getattr(ix_raw, "tz", None) is not None
        else ix_raw
    )
    rk = ix_cmp.strftime("%Y-%m-%d")
    valid_dates = (~ix_cmp.isna()) & (~ix_raw.isna())
    valid_dates_arr = np.asarray(valid_dates, dtype=bool)
    row_key_ok = np.asarray(pd.Index(rk) <= end_k, dtype=bool)
    return np.asarray(valid_dates_arr & row_key_ok, dtype=bool)


def _slice_ohlcv_through_end_calendar(
    df: pd.DataFrame, *, end_date_str: str
) -> pd.DataFrame | None:
    """
    period.end_date(캘린더) 포함까지만 슬라이스한 일봉.
    tz-aware 인덱스는 한국 시간 날짜 문자열 비교로 미래 봉·미래 참조를 배제함.
    """
    z = _prepare_screener_ohlcv(df)
    if z is None:
        return None
    mask_dates = _index_mask_through_end_calendar(z.index, end_date_str)
    z = z.loc[mask_dates].copy()
    if z.empty:
        return None
    return z


@dataclass(frozen=True)
class ScreenerEntry:
    code: str
    name: str
    volatility_raw: float
    turnover_krw_sum: float
    pullback_from_high_pct: float
    volume_contract_pct: float
    combined_score: float


@dataclass(frozen=True)
class RankedUniversePick:
    """시총 순위·돌파 에너지 등 GUI 배치 공용 간단 순위 행 (리스트박스·TSV)."""

    code: str
    name: str
    combined_score: float
    market_cap_krw: float | None = None


def default_screener_config() -> dict:
    """settings.yaml 우선 병합용 기본 블록."""
    return {
        "enabled": True,
        "lookback_trading_days": 20,
        "top_n": 30,
        # 변동성 지표 GUI 제거 후 엔진은 atr14(14일 평균 등락 폭 %)만 사용
        "volatility_metric": "atr14",
        "combine": "sum_rank_pct",
        "min_market_cap_krw": SCREEN_MIN_MARKET_CAP_KRW_DEFAULT,
        "hard_ma_pair_trend_filter": True,
        # 눌림목 랭킹: 고점 대비 낙폭 순위화 시 과도 낙폭 클램프(%); 급락·상투 완화
        "pullback_rank_cap_pct": 35.0,
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
    z_prefetched_end: pd.DataFrame | None = None,
) -> tuple[float | None, float | None]:
    """
    end_ts까지의 일봉만 사용하여 [마지막 lookback거래일] 구간 변동성·거래대금 합 계산.
    ATR은 전체 로드 구간으로 워밍업한 뒤, 마지막 lookback구간의 ATR/종가 비율 평균만 사용.
    변동성은 **항상 atr14**(최근 14일 평균 참범위/종가 %)만 사용 — 구 metric 인자는 하위 호환용.
    z_prefetched_end: 이미 종료일까지 자른 동일 규격 OHLCV(역배열 필터 후 재사용용).
    """
    if z_prefetched_end is not None:
        z = z_prefetched_end
    else:
        end_calendar = pd.Timestamp(end_ts).strftime("%Y-%m-%d")
        z = _slice_ohlcv_through_end_calendar(df, end_date_str=end_calendar)
        if z is None:
            return None, None

    atr_period = 14
    min_len = lookback + atr_period
    if len(z) < min_len:
        return None, None

    tail = z.iloc[-lookback:]
    turnover = (
        pd.to_numeric(tail["Volume"], errors="coerce").fillna(0)
        * pd.to_numeric(tail["Close"], errors="coerce").fillna(0)
    ).sum()

    if turnover <= 0 or not np.isfinite(turnover):
        return None, None

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

    if not np.isfinite(vol):
        return None, None

    return float(vol), float(turnover)


def _pullback_volume_contract_from_lookback_tail(
    tail: pd.DataFrame, lookback: int
) -> tuple[float, float] | None:
    """
    동일 lookback 윈도우(종료일 확정 구간)에서:
    - 고점 대비 낙폭 %: max(High) 대비 마지막 종가
    - 거래량 감소 성격: 말단 n일 평균 / 그 이전 구간 평균 비율이 낮을수록 눌림(건조)에 가깝다고 보고 점수화.
    """
    if len(tail) < lookback:
        return None
    w = tail.iloc[-lookback:]
    hi_s = pd.to_numeric(w["High"], errors="coerce")
    cl_s = pd.to_numeric(w["Close"], errors="coerce")
    hi = float(hi_s.max())
    cl = float(cl_s.iloc[-1])
    if not np.isfinite(hi) or hi <= 0 or not np.isfinite(cl):
        return None
    pullback_pct = 100.0 * (hi - cl) / hi

    vol = pd.to_numeric(w["Volume"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    n_recent = max(3, min(6, max(lookback // 4, 3)))
    n_prior = lookback - n_recent
    if n_prior < 5:
        return None
    v_recent = float(vol[-n_recent:].mean())
    v_prior = float(vol[-lookback:-n_recent].mean())
    if not np.isfinite(v_recent) or not np.isfinite(v_prior) or v_prior < 1e-9:
        return None
    ratio = v_recent / v_prior
    # ratio<1 이면 감소 구간 → (1-ratio)를 %스케일로；과도 급증은 ratio 클램프
    contract_pct = max(0.0, min(100.0, (1.0 - min(ratio, 2.0)) * 100.0))
    return float(pullback_pct), float(contract_pct)


def _screen_fetch_start(end_date: str) -> str:
    t = pd.Timestamp(str(end_date).strip()[:10])
    return (t - pd.Timedelta(days=_SCR_FETCH_CALENDAR_DAYS)).strftime("%Y-%m-%d")


_BREAKOUT_TD = 20
_BREAKOUT_BURST_MULT = 3.0  # 평균 대비 거래대금 300%
_BREAKOUT_NEAR_HIGH = 0.95  # 20일 고점 대비 종가 −5% 이내 근접(≥95%)


def screen_universe_mcap_top(
    *,
    market: str,
    keyword: str,
    top_n: int = 30,
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> list[RankedUniversePick]:
    """기술 지표 배제 · 상장표 시총 기준 상위 top_n 종목만 선별."""
    cand = fetch_filtered_universe(market, keyword)
    if not cand:
        return []

    mkt_upper_local = str(market).strip().upper()
    marcap_krw_map = fetch_listing_market_cap_krw_by_code(market)
    if not marcap_krw_map and mkt_upper_local != "ETF":
        rows: list[tuple[str, str, float, float]] = [
            (
                str(code).strip().zfill(6),
                str(name),
                0.0,
                float("nan"),
            )
            for code, name in sorted(cand.items(), key=lambda x: x[0])
        ]
    else:
        rows = []
        for code, name in sorted(cand.items(), key=lambda x: x[0]):
            cdf = code.strip().zfill(6)
            mc_raw = (
                marcap_krw_map.get(cdf)
                if isinstance(marcap_krw_map, dict)
                else None
            )
            mc = (
                float(mc_raw)
                if mc_raw is not None and np.isfinite(float(mc_raw))
                else float("nan")
            )
            sc = mc if np.isfinite(mc) else float("-inf")
            rows.append((cdf, name, sc, mc))
        rows.sort(key=lambda r: (-r[2], r[0]))

    cap = max(1, min(200, int(top_n)))
    out: list[RankedUniversePick] = []
    done = 0
    for cdf, nm, _sc4sort, mc in rows[:cap]:
        done += 1
        if progress_cb:
            progress_cb(done, cap, cdf)
        mcap_use = mc if np.isfinite(mc) and mc > 0 else None
        norm = mc / max(1.0, 1e13) if mcap_use is not None else 0.0
        out.append(
            RankedUniversePick(
                code=str(cdf).zfill(6),
                name=str(nm),
                combined_score=float(norm),
                market_cap_krw=mcap_use,
            )
        )
    return out


def _breakout_candidate_row(
    code: str,
    name: str,
    fetch_start: str,
    end_date: str,
    *,
    marcap_krw_map: dict[str, float] | None,
    min_market_cap_krw: float,
) -> RankedUniversePick | None:
    cdf = code.strip().zfill(6)

    df = load_ohlcv(code, fetch_start, end_date)
    zw = _slice_ohlcv_through_end_calendar(df, end_date_str=end_date)
    if zw is None or zw.empty:
        return None

    # 시총 하드 게이트(스크리너와 동일 — ETF 에서는 호출 전에 비활성)
    if marcap_krw_map is not None and min_market_cap_krw > 0:
        mc_chk = marcap_krw_map.get(cdf)
        if mc_chk is None or not np.isfinite(float(mc_chk)) or float(
            mc_chk
        ) < float(min_market_cap_krw):
            return None
    zw = zw.dropna(subset=list(_OHLCV_COLS_REQ))
    if len(zw) < _BREAKOUT_TD:
        return None

    tail = zw.iloc[-_BREAKOUT_TD:].copy()
    vol = pd.to_numeric(tail["Volume"], errors="coerce").fillna(0.0)
    cl = pd.to_numeric(tail["Close"], errors="coerce")
    hi = pd.to_numeric(tail["High"], errors="coerce")
    tv = (vol * cl).to_numpy(dtype=float)
    if len(tv) < _BREAKOUT_TD or not np.all(np.isfinite(tv)):
        return None

    avg_tv = float(np.mean(tv))
    max_tv = float(np.max(tv))
    last_close = float(cl.iloc[-1])
    hh = float(hi.max())

    if not np.isfinite(avg_tv) or avg_tv <= 1e-9:
        return None
    if not np.isfinite(max_tv) or not np.isfinite(last_close) or not np.isfinite(hh):
        return None
    if hh <= 0 or last_close < hh * _BREAKOUT_NEAR_HIGH:
        return None
    if max_tv < _BREAKOUT_BURST_MULT * avg_tv:
        return None

    burst_ratio = max_tv / avg_tv
    proximity = last_close / hh
    combo = float(max(1e-9, burst_ratio) * max(proximity, 1e-9))

    mc = None
    if marcap_krw_map is not None:
        mr = marcap_krw_map.get(cdf)
        if mr is not None and np.isfinite(float(mr)) and float(mr) > 0:
            mc = float(mr)

    return RankedUniversePick(
        code=str(cdf),
        name=str(name),
        combined_score=combo,
        market_cap_krw=mc,
    )


def screen_universe_breakout_energy(
    *,
    market: str,
    keyword: str,
    end_date: str,
    top_n: int = 30,
    progress_cb: Callable[[int, int, str], None] | None = None,
    max_workers: int = MAX_SCREEN_WORKERS,
    min_market_cap_krw: float | None = None,
) -> list[RankedUniversePick]:
    """
    최근 20거래일: 일별 거래대금이 구간 평균 대비 최대값이 ≥300%
    이고, 종가가 20일 고점 −5% 이내 또는 돌파에 근접한 종목을 점수순 선별.
    """
    cand = fetch_filtered_universe(market, keyword)
    if not cand:
        return []
    fetch_start = _screen_fetch_start(end_date)

    min_mc_eff = (
        SCREEN_MIN_MARKET_CAP_KRW_DEFAULT
        if min_market_cap_krw is None
        else float(min_market_cap_krw)
    )
    mkt_upper = str(market).strip().upper()
    if mkt_upper == "ETF":
        marcap_krw_map: dict[str, float] | None = None
        min_mc_eff = 0.0
    elif min_mc_eff > 0:
        marcap_krw_map = fetch_listing_market_cap_krw_by_code(market)
        if not marcap_krw_map:
            marcap_krw_map = {}
    else:
        marcap_krw_map = None

    items = sorted(cand.items(), key=lambda x: x[0])
    total = len(items)
    raw_out: list[RankedUniversePick] = []
    done = 0

    def _one(pair: tuple[str, str]) -> RankedUniversePick | None:
        cod, nm = pair
        return _breakout_candidate_row(
            cod,
            nm,
            fetch_start,
            end_date,
            marcap_krw_map=marcap_krw_map,
            min_market_cap_krw=min_mc_eff,
        )

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 12))) as ex:
        futures = {ex.submit(_one, p): p[0] for p in items}
        for fut in as_completed(futures):
            done += 1
            cdf = futures[fut]
            if progress_cb is not None:
                progress_cb(done, total, cdf)
            try:
                row = fut.result()
            except Exception:
                row = None
            if row is not None:
                raw_out.append(row)

    cap = max(1, min(200, int(top_n)))
    raw_out.sort(key=lambda x: (-float(x.combined_score), str(x.code)))
    return raw_out[:cap]


def rank_screener_candidates(
    raw_rows: list[tuple[str, str, float, float, float, float]],
    *,
    pullback_rank_cap_pct: float = 35.0,
) -> list[ScreenerEntry]:
    """
    변동성·거래대금·고점대비 낙폭·거래량 감소 지표를 각각 백분위 순위로 만든 뒤 평균해 융합한다.
    낙폭은 순위화 전 `pullback_rank_cap_pct` 로 상한 클램프(과도 낙폭은 상대적으로 불리).
    """
    if not raw_rows:
        return []
    vols = np.array([x[2] for x in raw_rows], dtype=float)
    turnovers = np.array([x[3] for x in raw_rows], dtype=float)
    pull_raw = np.array([x[4] for x in raw_rows], dtype=float)
    vcont = np.array([x[5] for x in raw_rows], dtype=float)
    cap = max(0.1, float(pullback_rank_cap_pct))
    pull_clamped = np.minimum(pull_raw, cap)

    v_rank = pd.Series(vols).rank(pct=True, method="average", ascending=True)
    t_rank = pd.Series(turnovers).rank(pct=True, method="average", ascending=True)
    p_rank = pd.Series(pull_clamped).rank(pct=True, method="average", ascending=True)
    q_rank = pd.Series(vcont).rank(pct=True, method="average", ascending=True)
    scores = ((v_rank + t_rank + p_rank + q_rank) / 4.0).to_numpy(dtype=float)
    zipped = sorted(
        (
            (
                raw_rows[i][0],
                raw_rows[i][1],
                float(vols[i]),
                float(turnovers[i]),
                float(pull_raw[i]),
                float(vcont[i]),
                float(scores[i]),
            )
            for i in range(len(raw_rows))
        ),
        key=lambda r: (-r[6], r[0]),
    )
    return [
        ScreenerEntry(
            code=a,
            name=b,
            volatility_raw=c,
            turnover_krw_sum=d,
            pullback_from_high_pct=pbf,
            volume_contract_pct=vcp,
            combined_score=sc,
        )
        for a, b, c, d, pbf, vcp, sc in zipped
    ]


def _load_one_candidate(
    code: str,
    name: str,
    fetch_start: str,
    end_date: str,
    lookback: int,
    volatility_metric: str,
    *,
    marcap_krw_map: dict[str, float] | None,
    min_market_cap_krw: float,
    hard_ma_pair_trend_filter: bool,
) -> tuple[str, str, float, float, float, float] | None:
    # 로드 → 정제 → MA20/120 → 종료일 절단 → 시총/역배열/이평추세 하드 필터 → 변동성·거래대금·눌림 지표.
    cdf = code.strip().zfill(6)
    if marcap_krw_map is not None and min_market_cap_krw > 0:
        mc = marcap_krw_map.get(cdf)
        if mc is None or not np.isfinite(mc) or float(mc) < float(min_market_cap_krw):
            return None

    df = load_ohlcv(code, fetch_start, end_date)
    if df is None or df.empty:
        return None

    df_prep = _prepare_screener_ohlcv(df)
    if df_prep is None or len(df_prep) < SCREEN_MA_LOOKBACK:
        return None

    df_prep = df_prep.copy()
    close_n = pd.to_numeric(df_prep["Close"], errors="coerce")
    df_prep["Close"] = close_n
    df_prep["MA20"] = close_n.rolling(
        window=SCREEN_MA20_WINDOW, min_periods=SCREEN_MA20_WINDOW
    ).mean()
    df_prep["MA120"] = close_n.rolling(
        window=SCREEN_MA_LOOKBACK, min_periods=SCREEN_MA_LOOKBACK
    ).mean()

    end_str = pd.Timestamp(str(end_date).strip()[:10]).strftime("%Y-%m-%d")
    mask_end = _index_mask_through_end_calendar(df_prep.index, end_str)
    df_filtered = df_prep.loc[mask_end].copy()
    if df_filtered.empty or len(df_filtered) < SCREEN_MA_LOOKBACK:
        return None

    subset = df_filtered.dropna(subset=["MA120", "MA20", "Close"])
    if subset.empty:
        return None
    try:
        last_valid_row = subset.iloc[-1]
    except (IndexError, KeyError):
        return None

    current_close = float(last_valid_row["Close"])
    current_ma120 = float(last_valid_row["MA120"])
    current_ma20 = float(last_valid_row["MA20"])
    if not np.isfinite(current_close) or not np.isfinite(current_ma120):
        return None

    if current_close < current_ma120:
        return None

    if hard_ma_pair_trend_filter and not _pass_screener_ma_pair_trend(
        current_ma20, current_ma120, subset
    ):
        return None

    zw_cols = df_filtered[list(_OHLCV_COLS_REQ)].copy()
    end_ts = pd.Timestamp(end_str)
    v, tv = _daily_metrics_slice(
        df,
        end_ts=end_ts,
        lookback=lookback,
        volatility_metric=volatility_metric,
        z_prefetched_end=zw_cols,
    )
    if v is None or tv is None:
        return None

    pb_metrics = _pullback_volume_contract_from_lookback_tail(zw_cols, lookback)
    if pb_metrics is None:
        return None
    pb_pct, v_contract = pb_metrics
    return (code, name, v, tv, pb_pct, v_contract)


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
    min_market_cap_krw: float | None = None,
    hard_ma_pair_trend_filter: bool | None = None,
    pullback_rank_cap_pct: float | None = None,
) -> list[ScreenerEntry]:
    """
    키워드로 좁힌 시장 유니버스에 대해 스크리닝 후 상위 top_n 반환.
    progress_cb(done_count, total, last_code) — 스레드에서 호출 시 GUI는 after로 래핑 권장.
    """
    volatility_metric = "atr14"  # 엔진 고정 atr14 (매개변수는 하위 호환용)
    cand = fetch_filtered_universe(market, keyword)
    if not cand:
        return []
    fetch_start = _screen_fetch_start(end_date)

    min_mc_eff = (
        SCREEN_MIN_MARKET_CAP_KRW_DEFAULT
        if min_market_cap_krw is None
        else float(min_market_cap_krw)
    )
    mkt_upper = str(market).strip().upper()
    ma_pair_eff = (
        bool(default_screener_config().get("hard_ma_pair_trend_filter", True))
        if hard_ma_pair_trend_filter is None
        else bool(hard_ma_pair_trend_filter)
    )
    # ETF 상장표 MarCap 단위가 KOSPI/KOSDAQ 과 다름 — 시총 하드 필터 생략
    if mkt_upper == "ETF":
        marcap_krw_map = None
        min_mc_eff = 0.0
    elif min_mc_eff > 0:
        marcap_krw_map = fetch_listing_market_cap_krw_by_code(market)
        if not marcap_krw_map:
            marcap_krw_map = {}
    else:
        marcap_krw_map = None

    items = sorted(cand.items(), key=lambda x: x[0])
    total = len(items)
    raw: list[tuple[str, str, float, float, float, float]] = []
    done = 0

    ds_cfg = default_screener_config()
    pb_cap = (
        float(ds_cfg["pullback_rank_cap_pct"])
        if pullback_rank_cap_pct is None
        else float(pullback_rank_cap_pct)
    )

    def _one(pair: tuple[str, str]) -> tuple[str, str, float, float, float, float] | None:
        code, name = pair
        return _load_one_candidate(
            code,
            name,
            fetch_start,
            end_date,
            lookback_trading_days,
            volatility_metric,
            marcap_krw_map=marcap_krw_map,
            min_market_cap_krw=min_mc_eff,
            hard_ma_pair_trend_filter=ma_pair_eff,
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

    ranked = rank_screener_candidates(raw, pullback_rank_cap_pct=pb_cap)
    cap = max(1, min(200, int(top_n)))
    # 이중 방어: 랭킹 점수 내림차순·코드 오름차순으로 고정 후 상위 cap건만 반환
    ranked = sorted(
        ranked, key=lambda e: (-float(e.combined_score), str(e.code))
    )
    return ranked[:cap]


def summary_line_for_entry(e: ScreenerEntry) -> str:
    return (
        f"{e.code} {e.name} | "
        f"vol={e.volatility_raw:.6g} | "
        f"거래대금합(원)={e.turnover_krw_sum:,.0f} | "
        f"고점낙폭%={e.pullback_from_high_pct:.2f} | "
        f"거래량건조%={e.volume_contract_pct:.1f} | "
        f"score={e.combined_score:.4f}"
    )
