"""
데이터 수집·정렬·주봉 집계·설정 YAML 로드.
v3.90: OHLCV 단일 소스 pykrx(벌크 캐시 우선·by_date 폴백) — 스캔·차트·백테스트 정합.
v4.10: FDR 상장표 메모리 캐시(TTL)·OHLCV LRU.
"""
from __future__ import annotations

import calendar
import contextlib
import os
import socket
import threading
import time
from collections import OrderedDict
from datetime import date, datetime

import FinanceDataReader as fdr
import numpy as np
import pandas as pd
import yaml

from pandas.tseries.offsets import BDay

from src.filters import (
    PULLBACK_DUAL_MARKET_LABEL,
    PULLBACK_LONG_MA_DAYS,
    PULLBACK_MIN_OHLCV_BARS,
    PULLBACK_SCAN_HISTORY_BDAY,
    PULLBACK_VERY_LONG_MA_DAYS,
    kim_straight_trend_pass,
    pass_liquidity_gate,
    pullback_bulk_markets_for_scan,
    pullback_scan_is_dual_market,
    resolve_pullback_universe_head,
)

# v4.10: 동일 세션 내 중복 네트워크 호출 완화(스크리너 급 저지연 목표).
_LISTING_LOCK = threading.Lock()
_LISTING_TS: dict[str, float] = {}
_LISTING_DF: dict[str, pd.DataFrame] = {}
FDR_LISTING_CACHE_TTL_SEC = 600.0

_OHLCV_LOCK = threading.Lock()
_OHLCV_LRU: OrderedDict[tuple[str, str, str], pd.DataFrame] = OrderedDict()
OHLCV_CACHE_MAX_ENTRIES = 96
NETWORK_TIMEOUT_SEC = 3.0
socket.setdefaulttimeout(NETWORK_TIMEOUT_SEC)


@contextlib.contextmanager
def _temporary_socket_timeout(timeout_sec: float):
    prev = socket.getdefaulttimeout()
    socket.setdefaulttimeout(float(timeout_sec))
    try:
        yield
    finally:
        socket.setdefaulttimeout(prev)


def months_before(d: date, months: int) -> date:
    """동일 일 기준으로 월을 되돌림(말일 초과 시 해당 월 말일로 클램프)."""
    y, m = d.year, d.month
    m -= months
    while m <= 0:
        m += 12
        y -= 1
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last))


def default_backtest_period_range() -> tuple[date, date]:
    """시작=오늘 기준 6개월 전, 종료=오늘."""
    today = date.today()
    return months_before(today, 6), today


# 일봉: 사용자 시작일 이전 최소 이 거래일만큼 OHLCV 를 당겨와 MA120·v4.0 기울기 필터 워밍업
OHLCV_EXTRA_TRADING_BARS_DAILY = 130
# 주봉: 일봉 원천을 충분히 길게 로드한 뒤 주간 리샘플 (약 130주 + 여유)
OHLCV_WEEKLY_FETCH_CALENDAR_DAYS = 980


def ohlcv_warm_start_date(user_start: str, *, interval: str) -> str:
    """
    차트·시뮬에 쓰는 사용자 시작일은 그대로 두되, OHLCV 로드 시작일만 더 과거로 당김.
    일봉: 거래일 기준 OHLCV_EXTRA_TRADING_BARS_DAILY 만큼 이전부터.
    주봉: 일봉 시계열을 넉넉히 가져온 뒤 주봉으로 집계하므로 캘린더 일 단위로 과거 확장.
    """
    iv = str(interval).strip().lower()
    ts = pd.Timestamp(str(user_start).strip()[:10])
    if iv == "weekly":
        warm_ts = ts - pd.Timedelta(days=OHLCV_WEEKLY_FETCH_CALENDAR_DAYS)
    else:
        warm_ts = ts - pd.offsets.BDay(OHLCV_EXTRA_TRADING_BARS_DAILY)
    return warm_ts.strftime("%Y-%m-%d")


def load_config(path: str | None = None) -> dict:
    """config/settings.yaml 로드. 기간이 비어 있으면 6개월 전~오늘로 채움."""
    cfg_path = path or os.path.join("config", "settings.yaml")
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if cfg is None:
        cfg = {}
    period = cfg.setdefault("period", {})
    ds, de = period.get("start_date"), period.get("end_date")
    if not str(ds or "").strip() or not str(de or "").strip():
        s_d, e_d = default_backtest_period_range()
        if not str(ds or "").strip():
            period["start_date"] = s_d.strftime("%Y-%m-%d")
        if not str(de or "").strip():
            period["end_date"] = e_d.strftime("%Y-%m-%d")
    return cfg


def fdr_stock_listing(market: str) -> pd.DataFrame | None:
    """FinanceDataReader StockListing 호출 전 시장 라벨 정규화."""
    m = str(market or "").strip().upper()
    key = "ETF/KR" if m == "ETF" else m
    now = time.monotonic()
    with _LISTING_LOCK:
        ts = _LISTING_TS.get(key)
        cached_df = _LISTING_DF.get(key)
        if (
            cached_df is not None
            and ts is not None
            and (now - ts) < FDR_LISTING_CACHE_TTL_SEC
        ):
            return cached_df.copy()
    try:
        raw = fdr.StockListing(key) if m == "ETF" else fdr.StockListing(m)
    except Exception:
        raw = None
    if raw is None or getattr(raw, "empty", True):
        return None
    with _LISTING_LOCK:
        _LISTING_DF[key] = raw
        _LISTING_TS[key] = time.monotonic()
    return raw.copy()


def fetch_filtered_universe(market: str, keyword: str) -> dict[str, str]:
    """종목 리스트에서 이름 키워드로 필터. keyword 가 비면 전체."""
    stocks = fdr_stock_listing(market)
    if stocks is None or stocks.empty:
        return {}
    if "Code" not in stocks.columns or "Name" not in stocks.columns:
        return {}
    if keyword and str(keyword).strip():
        kw = str(keyword).strip()
        mask = stocks["Name"].astype(str).str.contains(kw, na=False)
        sub = stocks.loc[mask].copy()
    else:
        sub = stocks.copy()
    codes = sub["Code"].astype(str).str.zfill(6)
    names = sub["Name"].astype(str)
    return dict(zip(codes, names))


def fetch_listing_market_cap_krw_by_code(market: str) -> dict[str, float]:
    """
    FDR 상장 표의 시가총액(원화 근사, 종가×상장주식수와 동일 규모).
    Pykrx KRX 로그인 없이 스크리너 하드 필터에 사용 가능.
    """
    stocks = fdr_stock_listing(market)
    if stocks is None or stocks.empty:
        return {}
    mku = str(market or "").strip().upper()
    if mku == "ETF":
        sym_c = stocks.get("Symbol")
        mc = stocks.get("MarCap") if sym_c is not None else None
        if sym_c is None or mc is None:
            return {}
        codes = sym_c.astype(str).str.strip().str.zfill(6)
    else:
        if "Code" not in stocks.columns:
            return {}
        codes = stocks["Code"].astype(str).str.strip().str.zfill(6)
        mc = stocks.get("Marcap")
        if mc is None:
            return {}
    vals = pd.to_numeric(mc, errors="coerce")
    out: dict[str, float] = {}
    for cd, mv in zip(codes, vals):
        if mv is None or (isinstance(mv, float) and not np.isfinite(mv)):
            continue
        out[str(cd)] = float(mv)
    return out


def ensure_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    """인덱스를 DatetimeIndex로 맞춘 뒤 과거→현재 순으로 정렬."""
    out = df.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index)
    return out.sort_index()


def resample_weekly_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """
    일봉 → 주봉. 한국 주식 관례에 가깝게 **금요일 말** 기준 주간 봉.
    """
    d = ensure_datetime_index(df)
    agg: dict = {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
    if "Volume" in d.columns:
        agg["Volume"] = "sum"
    elif "Amount" in d.columns:
        agg["Amount"] = "sum"
    w = d.resample("W-FRI", label="right", closed="right").agg(agg)
    return w.dropna(how="any", subset=["Open", "High", "Low", "Close"])


def normalize_krx_listing_market(raw: object) -> str | None:
    """KOSPI/KOSDAQ/ETF 허용. 그 외·빈 값은 None."""
    m = str(raw or "").strip().upper()
    return m if m in ("KOSPI", "KOSDAQ", "ETF") else None


def _stitch_ticker_ohlcv_from_bulk_cache(
    symbol: str,
    start: str,
    end: str,
    *,
    market: str | None = None,
) -> pd.DataFrame | None:
    """pykrx 벌크 일별 pkl 캐시에서 단일 티커 OHLCV 조립(스캔과 동일 소스)."""
    from src.market_ohlcv_bulk_cache import load_cached_bulk_day

    code6 = str(symbol or "").strip().zfill(6)
    sd = pd.Timestamp(str(start).strip()[:10]).normalize()
    ed = pd.Timestamp(str(end).strip()[:10]).normalize()
    bdays = pd.bdate_range(sd, ed)
    if bdays.empty:
        return None

    markets: tuple[str, ...]
    mk_hint = str(market or "").strip().upper()
    if mk_hint in ("KOSPI", "KOSDAQ"):
        markets = (mk_hint,)
    else:
        markets = ("KOSPI", "KOSDAQ")

    for mk in markets:
        rows: list[dict] = []
        complete = True
        for d_ts in bdays:
            ymd = d_ts.strftime("%Y%m%d")
            fr = load_cached_bulk_day(mk, ymd)
            if fr is None or code6 not in fr.index:
                complete = False
                break
            r = fr.loc[code6]
            rows.append(
                {
                    "Date": pd.Timestamp(d_ts).normalize(),
                    "Open": float(r.get("Open", float("nan"))),
                    "High": float(r.get("High", float("nan"))),
                    "Low": float(r.get("Low", float("nan"))),
                    "Close": float(r.get("Close", float("nan"))),
                    "Volume": float(r.get("Volume", float("nan"))),
                }
            )
        if not complete or not rows:
            continue
        df = pd.DataFrame(rows).set_index("Date").sort_index()
        df = df.loc[(df.index >= sd) & (df.index <= ed)].copy()
        if df.empty:
            continue
        df.attrs["v3_source"] = "pykrx_bulk_cache"
        return df
    return None


def _load_ohlcv_pykrx_by_date(symbol: str, start: str, end: str) -> pd.DataFrame | None:
    """pykrx get_market_ohlcv_by_date — 단일 티커 기간 OHLCV."""
    cdf = str(symbol or "").strip().zfill(6)
    s_ymd = pd.Timestamp(str(start).strip()[:10]).strftime("%Y%m%d")
    e_ymd = pd.Timestamp(str(end).strip()[:10]).strftime("%Y%m%d")
    try:
        from pykrx import stock as pykrx_stock  # type: ignore

        with _temporary_socket_timeout(NETWORK_TIMEOUT_SEC):
            raw = pykrx_stock.get_market_ohlcv_by_date(s_ymd, e_ymd, cdf)
    except Exception:
        return None
    if raw is None or getattr(raw, "empty", True):
        return None
    df = _normalize_pykrx_ohlcv_columns(raw)
    if df.empty:
        return None
    out = ensure_datetime_index(df)
    out.attrs["v3_source"] = "pykrx_by_date"
    return out


def load_ohlcv(
    symbol: str,
    start: str,
    end: str,
    *,
    market: str | None = None,
) -> pd.DataFrame | None:
    """pykrx OHLCV 단일 티커 조회 — 벌크 캐시 우선, 없으면 by_date.

    v3.90: FinanceDataReader 폐기 — 스캔·차트·백테스트 OHLCV 소스 단일화.
    v4.10: (코드,start,end,provider) 키 LRU.
    """
    cdf = str(symbol or "").strip().zfill(6)
    sk = (cdf, str(start).strip()[:10], str(end).strip()[:10], "pykrx")
    with _OHLCV_LOCK:
        if sk in _OHLCV_LRU:
            _OHLCV_LRU.move_to_end(sk)
            return _OHLCV_LRU[sk].copy()

    df = _stitch_ticker_ohlcv_from_bulk_cache(cdf, start, end, market=market)
    if df is None or df.empty:
        df = _load_ohlcv_pykrx_by_date(cdf, start, end)
    if df is None or df.empty:
        return None

    with _OHLCV_LOCK:
        _OHLCV_LRU[sk] = df
        _OHLCV_LRU.move_to_end(sk)
        while len(_OHLCV_LRU) > OHLCV_CACHE_MAX_ENTRIES:
            _OHLCV_LRU.popitem(last=False)
    return df.copy()


def _merge_intraday_session_bar(
    df: pd.DataFrame | None,
    symbol: str,
    end: str,
) -> pd.DataFrame | None:
    """
  GUI 차트용: 종료일이 '오늘'(KST)이고 장이 열린 뒤면 pykrx로 당일 봉(누적 OHLC)을 보강한다.

  FDR 일봉은 장중·종가 전에는 당일 행이 없는 경우가 많아, 실시간이 아니라
  데이터 소스·집계 시점 차이로 오늘 봉이 비어 보일 수 있다.
    """
    if df is None:
        base = pd.DataFrame()
    else:
        base = ensure_datetime_index(df.copy())

    try:
        from src.utils.date_helper import (
            KRX_REGULAR_SESSION_OPEN_TIME,
            KST,
        )
    except ImportError:
        return base if not base.empty else None

    end_ts = pd.Timestamp(str(end).strip()[:10]).normalize()
    now = datetime.now(KST)
    today = pd.Timestamp(now.date()).normalize()
    if end_ts != today or now.time() < KRX_REGULAR_SESSION_OPEN_TIME:
        return base if not base.empty else None

    krx_id = str(os.getenv("KRX_ID") or "").strip()
    krx_pw = str(os.getenv("KRX_PW") or "").strip()
    if len(krx_id) < 2 or len(krx_pw) < 2:
        return base if not base.empty else None

    cdf = str(symbol or "").strip().zfill(6)
    s_day = end_ts.strftime("%Y%m%d")
    try:
        from pykrx import stock as pykrx_stock  # type: ignore

        with _temporary_socket_timeout(NETWORK_TIMEOUT_SEC):
            raw = pykrx_stock.get_market_ohlcv_by_date(s_day, s_day, cdf)
    except Exception:
        return base if not base.empty else None

    if raw is None or getattr(raw, "empty", True):
        return base if not base.empty else None

    row_df = _normalize_pykrx_ohlcv_columns(raw)
    if row_df.empty:
        return base if not base.empty else None

    row_df = ensure_datetime_index(row_df)
    row_df.index = row_df.index.normalize()
    last = row_df.iloc[-1:]
    if last.empty:
        return base if not base.empty else None

    o = float(last["Open"].iloc[0])
    if not (np.isfinite(o) and o > 0):
        return base if not base.empty else None

    if base.empty:
        return last

    base.index = pd.DatetimeIndex(base.index).normalize()
    if end_ts in base.index:
        base.loc[end_ts, ["Open", "High", "Low", "Close", "Volume"]] = last.iloc[0][
            ["Open", "High", "Low", "Close", "Volume"]
        ].values
    else:
        base = pd.concat([base, last])
    return ensure_datetime_index(base)


def load_ohlcv_for_chart(
    symbol: str,
    start: str,
    end: str,
    *,
    market: str | None = None,
) -> pd.DataFrame | None:
    """차트 OHLCV — v3.90 pykrx 단일 소스(스캔 벌크 캐시와 동일 경로)."""
    df = load_ohlcv(symbol, start, end, market=market)
    if df is None or df.empty:
        return None
    return df


def clear_ohlcv_cache() -> None:
    """테스트 또는 메모리 회수 시 사용."""
    with _OHLCV_LOCK:
        _OHLCV_LRU.clear()


# ============================================================
# v3.0 (Overnight Scalper) — Data Loader
# ============================================================

def _normalize_pykrx_ohlcv_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    pykrx `get_market_ohlcv_by_date()` 반환 컬럼을 엔진 공용 포맷으로 정규화.

    내부 시뮬레이션은 `Open/High/Low/Close/Volume` 컬럼명을 사용합니다.
    """
    if df is None or df.empty:
        return pd.DataFrame()

    col_map = {
        "시가": "Open",
        "open": "Open",
        "Open": "Open",
        "고가": "High",
        "high": "High",
        "High": "High",
        "저가": "Low",
        "low": "Low",
        "Low": "Low",
        "종가": "Close",
        "close": "Close",
        "Close": "Close",
        "거래량": "Volume",
        "volume": "Volume",
        "Volume": "Volume",
    }

    # 케이스/키 차이를 흡수하기 위해, 존재하는 컬럼만 매핑
    out = df.copy()
    rename: dict[str, str] = {}
    for c in list(out.columns):
        if c in col_map:
            rename[c] = col_map[c]
    if rename:
        out = out.rename(columns=rename)

    needed = ["Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in needed if c not in out.columns]
    if missing:
        return pd.DataFrame()

    out = out[needed].copy()
    for c in needed:
        out[c] = pd.to_numeric(out[c], errors="coerce")

    return out


def fetch_pykrx_marcap_trade_krw_by_code(
    as_of_date: str,
    *,
    market: str = "KOSPI",
) -> dict[str, tuple[float | None, float | None]]:
    """
    pykrx 일자별 시가총액·거래대금(원화) — v3.1 스캐너 표시용.

    - KRX_ID/KRX_PW 미설정 시 빈 dict 반환(호출부에서 OHLCV 근사 대체).
    - market: KOSPI 또는 KOSDAQ 만 지원.
    """
    m = str(market or "KOSPI").strip().upper()
    if m not in ("KOSPI", "KOSDAQ"):
        return {}

    krx_id = str(os.getenv("KRX_ID") or "").strip()
    krx_pw = str(os.getenv("KRX_PW") or "").strip()
    if len(krx_id) < 2 or len(krx_pw) < 2:
        return {}

    try:
        from pykrx import stock as pykrx_stock  # type: ignore

        d = pd.Timestamp(str(as_of_date).strip()[:10]).strftime("%Y%m%d")
        with _temporary_socket_timeout(NETWORK_TIMEOUT_SEC):
            raw = pykrx_stock.get_market_cap_by_ticker(d, market=m)
    except (TimeoutError, socket.timeout, OSError):
        return {}
    except Exception:
        return {}

    if raw is None or getattr(raw, "empty", True):
        return {}

    cap_col = None
    amt_col = None
    for c in raw.columns:
        cs = str(c)
        if cap_col is None and ("시가총액" in cs or cs.lower() == "marcap"):
            cap_col = c
        if amt_col is None and ("거래대금" in cs or "amount" in cs.lower()):
            amt_col = c
    if cap_col is None or amt_col is None:
        return {}

    out: dict[str, tuple[float | None, float | None]] = {}
    for idx, row in raw.iterrows():
        code = str(idx).strip().zfill(6)
        if not code or code == "000000":
            continue
        try:
            mc = float(row[cap_col])
        except (TypeError, ValueError):
            mc = None
        try:
            ta = float(row[amt_col])
        except (TypeError, ValueError):
            ta = None
        if mc is not None and (not np.isfinite(mc) or mc < 0):
            mc = None
        if ta is not None and (not np.isfinite(ta) or ta < 0):
            ta = None
        out[code] = (mc, ta)
    return out


_V31_PYKRX_EN_TO_KO: dict[str, str] = {
    "open": "시가",
    "high": "고가",
    "low": "저가",
    "close": "종가",
    "volume": "거래량",
    "amount": "거래대금",
    "amount_cum": "거래대금",
    "value": "거래대금",
    "fluctuation": "등락률",
    "change": "등락률",
}


def _normalize_pykrx_columns_to_ko(df: pd.DataFrame) -> pd.DataFrame:
    """pykrx/래퍼에 따라 영문·소문영문 컬럼이 오는 경우까지 한글 정규화."""
    x = df.copy()
    low = {str(c).strip().lower(): c for c in x.columns}
    for en, ko in _V31_PYKRX_EN_TO_KO.items():
        if ko in x.columns:
            continue
        if en in low:
            x = x.rename(columns={low[en]: ko})
    titled = (
        ("Open", "시가"),
        ("High", "고가"),
        ("Low", "저가"),
        ("Close", "종가"),
        ("Volume", "거래량"),
        ("Amount", "거래대금"),
    )
    for c in list(x.columns):
        cs = str(c).strip()
        for tn, ko in titled:
            if cs != tn:
                continue
            if ko not in x.columns:
                x = x.rename(columns={c: ko})
            break
    return x


def _prep_pykrx_bulk_ticker_day(df: pd.DataFrame) -> pd.DataFrame:
    """일별 전종목 스냅샷 → ticker index, Open/High/Low/Close/Volume."""
    x = _normalize_pykrx_columns_to_ko(df)
    x.index = x.index.map(lambda v: str(v).zfill(6))
    out = pd.DataFrame(index=x.index)
    for ko, en in (
        ("시가", "Open"),
        ("고가", "High"),
        ("저가", "Low"),
        ("종가", "Close"),
        ("거래량", "Volume"),
        ("거래대금", "Amount"),
    ):
        if ko in x.columns:
            out[en] = pd.to_numeric(x[ko], errors="coerce")
    return out


def leader_pullback_prev_day_yang(
    prev_open: float, prev_close: float
) -> bool:
    """v3.80 Pass1: t-1 양봉 (종가 > 시가)."""
    return (
        np.isfinite(prev_open)
        and np.isfinite(prev_close)
        and prev_close > prev_open
    )


def _ingest_pykrx_marcap_by_ticker(
    raw_cap_rank: pd.DataFrame,
    rank_cap_map: dict[str, float],
    rank_cap_trade: dict[str, float | None],
) -> None:
    """get_market_cap_by_ticker 결과를 code→시총/거래대금 dict에 누적."""
    if raw_cap_rank is None or getattr(raw_cap_rank, "empty", True):
        return
    rc = None
    ac = None
    for z in raw_cap_rank.columns:
        zs = str(z)
        if rc is None and ("시가총액" in zs or zs.lower() == "marcap"):
            rc = z
        if ac is None and ("거래대금" in zs or "amount" in zs.lower()):
            ac = z
    if rc is None:
        return
    for idx2, row2 in raw_cap_rank.iterrows():
        code7 = str(idx2).strip().zfill(6)
        try:
            mcv = float(row2[rc])
        except (TypeError, ValueError):
            continue
        if not (np.isfinite(mcv) and mcv > 0):
            continue
        rank_cap_map[code7] = mcv
        tg = None
        if ac is not None:
            try:
                tg = float(row2[ac])
            except (TypeError, ValueError):
                tg = None
        rank_cap_trade[code7] = tg


def _fetch_bulk_ohlcv_day_frames(
    *,
    pykrx_stock: object,
    market: str,
    bdays: pd.DatetimeIndex,
    anchor_ymd: str,
    cancel_event: threading.Event | None,
) -> list[pd.DataFrame] | None:
    from src.market_ohlcv_bulk_cache import fetch_bulk_day_frames_cached

    mk = str(market).strip().upper()

    def _fetch_raw(ymd: str) -> pd.DataFrame:
        with _temporary_socket_timeout(NETWORK_TIMEOUT_SEC):
            return pykrx_stock.get_market_ohlcv_by_ticker(ymd, market=mk)

    return fetch_bulk_day_frames_cached(
        market=mk,
        bdays=bdays,
        fetch_day_raw=_fetch_raw,
        prep_day=_prep_pykrx_bulk_ticker_day,
        cancel_event=cancel_event,
        anchor_ymd=anchor_ymd,
        refresh_anchor=True,
    )


def _merge_multi_market_bulk_day_frames(
    per_market_frames: list[list[pd.DataFrame]],
) -> list[pd.DataFrame] | None:
    """영업일별로 시장별 OHLCV 프레임을 세로 결합(티커 유니온)."""
    if not per_market_frames:
        return None
    n_days = len(per_market_frames[0])
    for mfs in per_market_frames[1:]:
        if len(mfs) != n_days:
            return None
    merged_days: list[pd.DataFrame] = []
    for j in range(n_days):
        parts = [mfs[j] for mfs in per_market_frames if mfs[j] is not None]
        if not parts:
            return None
        merged_days.append(pd.concat(parts, axis=0))
    return merged_days


def leader_pullback_center_defense(
    prev_high: float, prev_low: float, close_t: float
) -> bool:
    """v3.80 Pass2: t 종가 >= t-1 봉 중심선 (고가+저가)/2."""
    if not all(np.isfinite(v) for v in (prev_high, prev_low, close_t)):
        return False
    return close_t >= (float(prev_high) + float(prev_low)) / 2.0


def qualifies_leader_pullback_from_ohlcv(
    df: pd.DataFrame,
    *,
    volume_burst_multiple: float,
    vol_shrink_limit: float,
    use_momentum_filter: bool,
    min_liquidity_market_cap_krw: float = 0.0,
    min_liquidity_trade_amount_krw: float = 0.0,
    market_cap_krw: float | None = None,
    trade_amount_krw: float | None = None,
) -> tuple[bool, float]:
    """
    단일 종목 일봉에서 v3.30 눌림목 + v3.80 + v3.95 Perfect Trend + v4.00 유동성.
    반환: (통과 여부, 당일 시가 대비 상승률 % — 리스트 정렬용).
    """
    if df is None or df.empty:
        return False, 0.0
    work = ensure_datetime_index(df.copy()).sort_index()
    if len(work) < PULLBACK_MIN_OHLCV_BARS:
        return False, 0.0

    vol = pd.to_numeric(work["Volume"], errors="coerce")
    low = pd.to_numeric(work["Low"], errors="coerce")
    high = pd.to_numeric(work["High"], errors="coerce")
    close = pd.to_numeric(work["Close"], errors="coerce")
    opn = pd.to_numeric(work["Open"], errors="coerce")

    close_t = float(close.iloc[-1])
    today_vol = float(vol.iloc[-1])
    open_t = float(opn.iloc[-1])
    proxy_trade = (
        (close_t * today_vol)
        if np.isfinite(close_t) and np.isfinite(today_vol) and today_vol >= 0
        else None
    )
    trd_eff = trade_amount_krw if trade_amount_krw is not None else proxy_trade
    if not pass_liquidity_gate(
        market_cap_krw,
        trd_eff,
        min_market_cap_krw=min_liquidity_market_cap_krw,
        min_trade_amount_krw=min_liquidity_trade_amount_krw,
    ):
        return False, 0.0

    vol_ma20_prior = float(vol.iloc[-22:-2].mean())
    prev_vol = float(vol.iloc[-2])
    prev_open = float(opn.iloc[-2])
    prev_close = float(close.iloc[-2])
    prev_high = float(high.iloc[-2])
    prev_low = float(low.iloc[-2])
    ma20 = float(close.iloc[-20:].mean())
    low_t = float(low.iloc[-1])

    if not (
        np.isfinite(vol_ma20_prior)
        and vol_ma20_prior > 0
        and np.isfinite(prev_vol)
        and prev_vol > 0
        and np.isfinite(ma20)
    ):
        return False, 0.0

    burst = float(volume_burst_multiple)
    shrink = float(vol_shrink_limit)
    cond_burst = prev_vol > (vol_ma20_prior * burst)
    cond_prev_yang = leader_pullback_prev_day_yang(prev_open, prev_close)
    cond_price = (low_t < ma20) and (close_t >= ma20)
    cond_center = leader_pullback_center_defense(prev_high, prev_low, close_t)
    cond_vol = today_vol <= (prev_vol * shrink)
    if not (
        cond_burst
        and cond_prev_yang
        and cond_price
        and cond_center
        and cond_vol
    ):
        return False, 0.0

    kim_ok, long_ok, short_ok = kim_straight_trend_pass(close)
    if not long_ok:
        return False, 0.0
    if use_momentum_filter and not short_ok:
        return False, 0.0

    rise_pct = ((close_t - open_t) / open_t * 100.0) if open_t > 0 else 0.0
    return True, float(rise_pct)


def scan_leader_pullback_candidates_bulk(
    end_date: str,
    *,
    market: str,
    universe_limit: int,
    volume_burst_multiple: float,
    vol_shrink_limit: float,
    use_momentum_filter: bool,
    min_liquidity_market_cap_krw: float,
    min_liquidity_trade_amount_krw: float,
    cancel_event: threading.Event | None = None,
) -> dict[str, object]:
    """
    v3.30 주도주 눌림목 벌크 스캐너.

    - v4.00 Pass 0: 시총·당일 거래대금 유동성 (Top-N 슬라이스 직후)
    - pykrx 일별 전종목 OHLCV 스냅샷 22영업일(t-21~t0) — 당일(t) 거래량이 MA20에 섞이지 않음
    - cond_prev_burst: t-1 거래량 > mean(t-2..t-21) × volume_burst_multiple
    - v3.80 cond_prev_yang: t-1 종가 > t-1 시가 (전일 양봉)
    - cond_price: t 저가 < MA20(종가 20일) & t 종가 >= MA20
    - v3.80 cond_center: t 종가 >= (t-1 고가+t-1 저가)/2 (전일 중심선 수호)
    - cond_volume: t 거래량 <= t-1 거래량 × vol_shrink_limit
    - v3.95 cond_kim_long: t 종가 > MA60 AND t 종가 > MA120 AND MA60 > MA120
    - v3.50 cond_kim_short: MA5 >= MA10
    """
    from src.utils.date_helper import resolve_overnight_scan_anchor

    krx_id = str(os.getenv("KRX_ID") or "").strip()
    krx_pw = str(os.getenv("KRX_PW") or "").strip()
    if len(krx_id) < 2 or len(krx_pw) < 2:
        return {"ok": False, "reason": "krx_auth_missing"}

    try:
        from pykrx import stock as pykrx_stock  # type: ignore
    except Exception:
        return {"ok": False, "reason": "pykrx_import_failed"}

    burst_mult = max(0.1, float(volume_burst_multiple))
    shrink_lim = max(0.01, float(vol_shrink_limit))
    cap = resolve_pullback_universe_head(universe_limit)
    scan_markets = pullback_bulk_markets_for_scan(market, universe_limit)
    dual_market = pullback_scan_is_dual_market(universe_limit)

    if cancel_event is not None and cancel_event.is_set():
        return {"ok": False, "reason": "cancelled"}

    ainfo = resolve_overnight_scan_anchor(str(end_date).strip()[:10])
    d_today = pd.Timestamp(ainfo.anchor_date).normalize()
    bdays = pd.bdate_range(d_today - BDay(PULLBACK_SCAN_HISTORY_BDAY), d_today)
    if len(bdays) < PULLBACK_MIN_OHLCV_BARS:
        return {"ok": False, "reason": "ohlcv_history_short"}

    anchor_ymd = d_today.strftime("%Y%m%d")

    per_market_day_frames: list[list[pd.DataFrame]] = []
    for mk in scan_markets:
        m_frames = _fetch_bulk_ohlcv_day_frames(
            pykrx_stock=pykrx_stock,
            market=mk,
            bdays=bdays,
            anchor_ymd=anchor_ymd,
            cancel_event=cancel_event,
        )
        if m_frames is None:
            if cancel_event is not None and cancel_event.is_set():
                return {"ok": False, "reason": "cancelled"}
            return {"ok": False, "reason": "ohlcv_bulk_failed"}
        per_market_day_frames.append(m_frames)

    if len(per_market_day_frames) == 1:
        day_frames = per_market_day_frames[0]
    else:
        day_frames = _merge_multi_market_bulk_day_frames(per_market_day_frames)
    if day_frames is None:
        if cancel_event is not None and cancel_event.is_set():
            return {"ok": False, "reason": "cancelled"}
        return {"ok": False, "reason": "ohlcv_bulk_failed"}

    common_idx = day_frames[0].index
    for fr in day_frames[1:]:
        common_idx = common_idx.intersection(fr.index)
    if common_idx.empty:
        return {"ok": False, "reason": "ohlcv_join_empty"}

    n_days = len(day_frames)
    codes = [str(c).zfill(6) for c in common_idx]
    n_codes = len(codes)
    vol_m = np.full((n_codes, n_days), np.nan, dtype=float)
    low_m = np.full((n_codes, n_days), np.nan, dtype=float)
    high_m = np.full((n_codes, n_days), np.nan, dtype=float)
    close_m = np.full((n_codes, n_days), np.nan, dtype=float)
    open_m = np.full((n_codes, n_days), np.nan, dtype=float)

    for j, fr in enumerate(day_frames):
        sub = fr.reindex(common_idx)
        vol_m[:, j] = pd.to_numeric(sub["Volume"], errors="coerce").to_numpy()
        low_m[:, j] = pd.to_numeric(sub["Low"], errors="coerce").to_numpy()
        high_m[:, j] = pd.to_numeric(sub["High"], errors="coerce").to_numpy()
        close_m[:, j] = pd.to_numeric(sub["Close"], errors="coerce").to_numpy()
        open_m[:, j] = pd.to_numeric(sub["Open"], errors="coerce").to_numpy()

    merged = pd.DataFrame(index=codes)
    merged["vol_ma20_strictly_prior"] = np.nanmean(vol_m[:, n_days - 22 : n_days - 2], axis=1)
    merged["prev_vol"] = vol_m[:, n_days - 2]
    merged["today_vol"] = vol_m[:, n_days - 1]
    merged["MA20"] = np.nanmean(close_m[:, n_days - 20 : n_days], axis=1)
    merged["MA60"] = np.nanmean(
        close_m[:, n_days - PULLBACK_LONG_MA_DAYS : n_days], axis=1
    )
    merged["MA120"] = np.nanmean(
        close_m[:, n_days - PULLBACK_VERY_LONG_MA_DAYS : n_days], axis=1
    )
    merged["MA5"] = np.nanmean(close_m[:, n_days - 5 : n_days], axis=1)
    merged["MA10"] = np.nanmean(close_m[:, n_days - 10 : n_days], axis=1)
    merged["Low_t0"] = low_m[:, n_days - 1]
    merged["Close_t0"] = close_m[:, n_days - 1]
    merged["Open_t0"] = open_m[:, n_days - 1]
    # v3.80: t-1 봉 — Top-N 슬라이스 전에 붙여 merged 와 동일 인덱스 유지
    merged["Prev_open"] = open_m[:, n_days - 2]
    merged["Prev_close"] = close_m[:, n_days - 2]
    merged["Prev_high"] = high_m[:, n_days - 2]
    merged["Prev_low"] = low_m[:, n_days - 2]

    s_today = d_today.strftime("%Y%m%d")
    rank_cap_map: dict[str, float] = {}
    rank_cap_trade: dict[str, float | None] = {}
    try:
        with _temporary_socket_timeout(NETWORK_TIMEOUT_SEC):
            for mk in scan_markets:
                raw_cap_rank = pykrx_stock.get_market_cap_by_ticker(s_today, market=mk)
                _ingest_pykrx_marcap_by_ticker(
                    raw_cap_rank, rank_cap_map, rank_cap_trade
                )
    except Exception:
        rank_cap_map = {}
        rank_cap_trade = {}

    mcap_scores = merged.index.map(lambda c: float(rank_cap_map.get(str(c).zfill(6), -1.0)))
    merged["_mcap_sort_tmp"] = mcap_scores
    merged["tmp_code"] = merged.index.astype(str).str.zfill(6)
    merged = merged.sort_values(
        by=["_mcap_sort_tmp", "tmp_code"],
        ascending=[False, True],
    )
    if cap is not None:
        merged = merged.head(cap)
    merged = merged.drop(columns=["_mcap_sort_tmp", "tmp_code"], errors="ignore")

    code_ser = merged.index.astype(str).str.zfill(6)
    merged["_mcap_krw"] = code_ser.map(
        lambda c: rank_cap_map.get(str(c).zfill(6), np.nan)
    )
    merged["_trade_krw"] = code_ser.map(
        lambda c: rank_cap_trade.get(str(c).zfill(6), np.nan)
    )
    proxy_trade = pd.to_numeric(merged["Close_t0"], errors="coerce") * pd.to_numeric(
        merged["today_vol"], errors="coerce"
    )
    merged["_trade_krw"] = merged["_trade_krw"].where(
        merged["_trade_krw"].notna() & (merged["_trade_krw"] > 0),
        proxy_trade,
    )
    min_cap = float(min_liquidity_market_cap_krw)
    min_trd = float(min_liquidity_trade_amount_krw)
    cond_liquidity = pd.Series(True, index=merged.index)
    if min_cap > 0:
        cond_liquidity &= merged["_mcap_krw"].notna() & (
            merged["_mcap_krw"] >= min_cap
        )
    if min_trd > 0:
        cond_liquidity &= merged["_trade_krw"].notna() & (
            merged["_trade_krw"] >= min_trd
        )
    total_universe = int(len(merged))
    pass_liquidity = int(cond_liquidity.sum())
    merged = merged.loc[cond_liquidity].copy()

    o = merged.get("Open_t0")
    c = merged.get("Close_t0")
    if o is None or c is None:
        return {"ok": False, "reason": "ohlcv_columns_missing"}

    return_pct = np.where(
        pd.to_numeric(o, errors="coerce").to_numpy() > 0,
        (pd.to_numeric(c, errors="coerce") - pd.to_numeric(o, errors="coerce"))
        / pd.to_numeric(o, errors="coerce")
        * 100.0,
        0.0,
    )
    merged["return_pct"] = pd.to_numeric(return_pct, errors="coerce")

    cond_burst = merged["prev_vol"] > (
        merged["vol_ma20_strictly_prior"] * burst_mult
    )
    po = pd.to_numeric(merged["Prev_open"], errors="coerce")
    pc = pd.to_numeric(merged["Prev_close"], errors="coerce")
    ph = pd.to_numeric(merged["Prev_high"], errors="coerce")
    pl = pd.to_numeric(merged["Prev_low"], errors="coerce")
    cond_prev_yang = (pc > po) & pc.notna() & po.notna()
    cond_price = (merged["Low_t0"] < merged["MA20"]) & (
        merged["Close_t0"] >= merged["MA20"]
    )
    prev_mid = (ph + pl) / 2.0
    cond_center = (merged["Close_t0"] >= prev_mid) & prev_mid.notna()
    cond_volume = merged["today_vol"] <= (merged["prev_vol"] * shrink_lim)
    pass_pullback = (
        cond_burst & cond_prev_yang & cond_price & cond_center & cond_volume
    )
    cond_kim_long = (
        (merged["Close_t0"] > merged["MA60"])
        & (merged["Close_t0"] > merged["MA120"])
        & (merged["MA60"] > merged["MA120"])
    )
    cond_kim_short = merged["MA5"] >= merged["MA10"]
    if use_momentum_filter:
        final_mask = pass_pullback & cond_kim_long & cond_kim_short
    else:
        final_mask = pass_pullback & cond_kim_long
    final = merged[final_mask].copy()

    _stats_diag = {
        "requested_end_date": ainfo.requested_calendar_date.isoformat(),
        "effective_anchor_date": d_today.strftime("%Y-%m-%d"),
        "anchor_policy_reason": ainfo.anchor_policy_reason,
        "universe_limit_applied": int(cap) if cap is not None else 0,
        "dual_market": bool(dual_market),
        "markets_pipeline": (
            PULLBACK_DUAL_MARKET_LABEL if dual_market else str(scan_markets[0])
        ),
        "volume_burst_multiple": burst_mult,
        "vol_shrink_limit": shrink_lim,
        "use_momentum_filter": bool(use_momentum_filter),
        "history_bdays": int(n_days),
        "min_liquidity_market_cap_krw": min_cap,
        "min_liquidity_trade_amount_krw": min_trd,
        "total_universe": total_universe,
        "pass_liquidity": pass_liquidity,
    }

    if final.empty:
        return {
            "ok": True,
            "rows": [],
            "stats": {
                "total_universe": total_universe,
                "pass_liquidity": pass_liquidity,
                "total_loaded": int(len(merged)),
                "pass_burst": int((cond_burst & cond_prev_yang).sum()),
                "pass_price": int(
                    (cond_burst & cond_prev_yang & cond_price & cond_center).sum()
                ),
                "pass_volume": int(pass_pullback.sum()),
                "pass_kim_long": int((pass_pullback & cond_kim_long).sum()),
                "pass_kim_short": int((pass_pullback & cond_kim_long & cond_kim_short).sum()),
                "pass_all": 0,
                "prev_1": ainfo.prev_1.isoformat(),
                "prev_2": ainfo.prev_2.isoformat(),
                **_stats_diag,
            },
        }

    idx_order = sorted(
        final.index,
        key=lambda c: (
            -float(pd.to_numeric(final.loc[c, "return_pct"], errors="coerce")),
            str(c).zfill(6),
        ),
    )
    out_rows: list[tuple[str, float, float | None, float | None]] = []
    for code in idx_order:
        row = final.loc[code]
        code6 = str(code).zfill(6)
        rise = float(row["return_pct"])
        proxy_amt = None
        if pd.notna(row.get("Close_t0")) and pd.notna(row.get("today_vol")):
            proxy_amt = float(row["Close_t0"]) * float(row["today_vol"])
        mar_krw: float | None = rank_cap_map.get(code6)
        trd_krw: float | None = rank_cap_trade.get(code6)
        if trd_krw is None:
            trd_krw = proxy_amt
        out_rows.append((code6, rise, mar_krw, trd_krw))

    return {
        "ok": True,
        "rows": out_rows,
        "stats": {
            "total_universe": total_universe,
            "pass_liquidity": pass_liquidity,
            "total_loaded": int(len(merged)),
            "pass_burst": int((cond_burst & cond_prev_yang).sum()),
            "pass_price": int(
                (cond_burst & cond_prev_yang & cond_price & cond_center).sum()
            ),
            "pass_volume": int(pass_pullback.sum()),
            "pass_kim_long": int((pass_pullback & cond_kim_long).sum()),
            "pass_kim_short": int((pass_pullback & cond_kim_long & cond_kim_short).sum()),
            "pass_all": int(len(final)),
            "prev_1": ainfo.prev_1.isoformat(),
            "prev_2": ainfo.prev_2.isoformat(),
            **_stats_diag,
        },
    }


def scan_v3_overnight_candidates_bulk(
    end_date: str,
    *,
    market: str,
    universe_limit: int,
    volume_burst_multiple: float,
    vol_shrink_limit: float,
    use_momentum_filter: bool,
    min_liquidity_market_cap_krw: float,
    min_liquidity_trade_amount_krw: float,
    cancel_event: threading.Event | None = None,
) -> dict[str, object]:
    """레거시 이름 — v3.30 `scan_leader_pullback_candidates_bulk` 로 위임."""
    return scan_leader_pullback_candidates_bulk(
        end_date,
        market=market,
        universe_limit=universe_limit,
        volume_burst_multiple=volume_burst_multiple,
        vol_shrink_limit=vol_shrink_limit,
        use_momentum_filter=use_momentum_filter,
        min_liquidity_market_cap_krw=min_liquidity_market_cap_krw,
        min_liquidity_trade_amount_krw=min_liquidity_trade_amount_krw,
        cancel_event=cancel_event,
    )


def load_v3_0_overnight_scalper_data(
    *,
    start_date: str,
    end_date: str,
    market: str = "KOSPI",
    universe_limit: int = 300,
    warm_bdays: int = 2,
) -> list[tuple[str, pd.DataFrame]]:
    """
    v3.0 오버나이트 스캘퍼용 Universe + 일봉 OHLCV 로드.

    - Universe: pykrx 시점 기준 ticker list (미설정 시 FDR 폴백)
    - OHLCV: Open/High/Low/Close/Volume 정규화만 수행 (시그널·청산은 v3 모듈에서 shift 처리)
    - 최소 3거래일 이상 데이터가 있는 종목만 반환 (익일 시가 청산 필요)
    """
    sd = str(start_date).strip()[:10]
    ed = str(end_date).strip()[:10]
    if not sd or not ed:
        raise ValueError("start_date / end_date 는 YYYY-MM-DD 형식 문자열이어야 합니다.")

    m = str(market or "KOSPI").strip().upper()
    if m not in ("KOSPI", "KOSDAQ"):
        m = "KOSPI"

    warm_start = (pd.Timestamp(sd) - BDay(max(1, int(warm_bdays)))).strftime("%Y-%m-%d")

    # Universe 동적 확보(시점 기준, pykrx 1순위).
    # 단, 현재 환경에 KRX 로그인 정보가 없으면 pykrx가 실패할 수 있으므로,
    # 그 경우에는 프로젝트 기존 FDR 데이터 소스로 폴백합니다.
    source = "pykrx"
    tickers: list[str] = []
    krx_id = str(os.getenv("KRX_ID") or "").strip()
    krx_pw = str(os.getenv("KRX_PW") or "").strip()
    krx_ready = len(krx_id) >= 2 and len(krx_pw) >= 2

    if krx_ready:
        try:
            # pykrx는 KRX_ID/KRX_PW 미설정 시 import/호출 단계에서 메시지를 출력할 수 있어
            # 환경 준비가 확인될 때만 lazy import 합니다.
            from pykrx import stock as pykrx_stock  # type: ignore

            with _temporary_socket_timeout(NETWORK_TIMEOUT_SEC):
                ref_ymd = pd.Timestamp(ed).strftime("%Y%m%d")
                tickers = pykrx_stock.get_market_ticker_list(ref_ymd, market=m) or []
            tickers = [str(x).strip().zfill(6) for x in tickers if str(x).strip()]
        except Exception:
            tickers = []

    if universe_limit and universe_limit > 0:
        lim = int(universe_limit)
        cap_ranked = False
        if source == "pykrx" and tickers:
            try:
                from pykrx import stock as pykrx_stock  # type: ignore

                d_cap = pd.Timestamp(ed).strftime("%Y%m%d")
                with _temporary_socket_timeout(NETWORK_TIMEOUT_SEC):
                    cap_raw = pykrx_stock.get_market_cap_by_ticker(d_cap, market=m)
                if cap_raw is not None and not getattr(cap_raw, "empty", True):
                    cap_col = None
                    for c0 in cap_raw.columns:
                        cs = str(c0)
                        if "시가총액" in cs or cs.lower() == "marcap":
                            cap_col = c0
                            break
                    if cap_col is not None:
                        cap_map: dict[str, float] = {}
                        for idx, row in cap_raw.iterrows():
                            code = str(idx).strip().zfill(6)
                            try:
                                mv = float(row[cap_col])
                            except (TypeError, ValueError):
                                continue
                            if np.isfinite(mv) and mv > 0:
                                cap_map[code] = mv
                        tickers = sorted(
                            tickers,
                            key=lambda c: (-float(cap_map.get(c, 0.0)), str(c)),
                        )[:lim]
                        cap_ranked = True
            except Exception:
                cap_ranked = False

        if not cap_ranked:
            tickers = tickers[:lim]

    if not tickers:
        source = "fdr"
        uni_map = fetch_filtered_universe(m, "")
        tickers = sorted(str(c).strip().zfill(6) for c in uni_map.keys() if str(c).strip())
        if universe_limit and universe_limit > 0:
            lim = int(universe_limit)
            cap_map = fetch_listing_market_cap_krw_by_code(m)
            if cap_map:
                tickers = sorted(
                    tickers,
                    key=lambda c: (-float(cap_map.get(c, 0.0)), str(c)),
                )[:lim]
            else:
                tickers = tickers[:lim]

    out: list[tuple[str, pd.DataFrame]] = []
    bulk_cache_ok = False
    if source == "pykrx" and tickers and krx_ready:
        try:
            from pykrx import stock as pykrx_stock  # type: ignore
            from src.market_ohlcv_bulk_cache import fetch_bulk_day_frames_cached

            warm_ts = pd.Timestamp(warm_start).normalize()
            ed_ts = pd.Timestamp(ed).normalize()
            bdays_fb = pd.bdate_range(warm_ts, ed_ts)
            anchor_ymd = ed_ts.strftime("%Y%m%d")

            def _fetch_raw_fb(ymd: str) -> pd.DataFrame:
                with _temporary_socket_timeout(NETWORK_TIMEOUT_SEC):
                    return pykrx_stock.get_market_ohlcv_by_ticker(ymd, market=m)

            day_frames_fb = fetch_bulk_day_frames_cached(
                market=m,
                bdays=bdays_fb,
                fetch_day_raw=_fetch_raw_fb,
                prep_day=_prep_pykrx_bulk_ticker_day,
                anchor_ymd=anchor_ymd,
                refresh_anchor=True,
            )
            if day_frames_fb:
                sd_ts = pd.Timestamp(sd)
                ed_ts_clip = ed_ts
                for ticker in tickers:
                    code6 = str(ticker).strip().zfill(6)
                    rows: list[dict] = []
                    for d_ts, fr in zip(bdays_fb, day_frames_fb):
                        if code6 not in fr.index:
                            continue
                        r = fr.loc[code6]
                        rows.append(
                            {
                                "Date": pd.Timestamp(d_ts).normalize(),
                                "Open": float(r.get("Open", float("nan"))),
                                "High": float(r.get("High", float("nan"))),
                                "Low": float(r.get("Low", float("nan"))),
                                "Close": float(r.get("Close", float("nan"))),
                                "Volume": float(r.get("Volume", float("nan"))),
                            }
                        )
                    if len(rows) < 3:
                        continue
                    df = pd.DataFrame(rows).set_index("Date").sort_index()
                    df = df.loc[
                        (df.index >= sd_ts) & (df.index <= ed_ts_clip)
                    ].copy()
                    if len(df) < 3:
                        continue
                    df.attrs["v3_source"] = "pykrx_bulk_cache"
                    out.append((code6, df))
                bulk_cache_ok = bool(out)
        except Exception:
            bulk_cache_ok = False

    if bulk_cache_ok:
        return out

    for ticker in tickers:
        if source == "pykrx":
            try:
                from pykrx import stock as pykrx_stock  # type: ignore

                with _temporary_socket_timeout(NETWORK_TIMEOUT_SEC):
                    raw = pykrx_stock.get_market_ohlcv_by_date(warm_start, ed, ticker)
            except Exception:
                continue

            if raw is None or getattr(raw, "empty", True):
                continue

            # Index 정규화(날짜 오름차순)
            try:
                raw.index = pd.to_datetime(raw.index)
            except Exception:
                continue
            raw = raw.sort_index()

            if len(raw) < 3:
                continue

            df = _normalize_pykrx_ohlcv_columns(raw)
            if df.empty or len(df) < 3:
                continue
        else:
            df_raw = load_ohlcv(ticker, warm_start, ed)
            if df_raw is None or df_raw.empty or len(df_raw) < 3:
                continue
            df = ensure_datetime_index(df_raw)

        df = df.loc[(df.index >= pd.Timestamp(sd)) & (df.index <= pd.Timestamp(ed))].copy()
        if len(df) < 3:
            continue

        df.attrs["v3_source"] = source
        out.append((ticker, df))

    return out
