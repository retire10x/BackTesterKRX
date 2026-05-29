"""
데이터 수집·정렬·주봉 집계·설정 YAML 로드 (FinanceDataReader 등).
필요 시 pykrx 등으로 확장. GUI 비의존.
v4.10: FDR 상장표 메모리 캐시(TTL)·OHLCV LRU—스크리너·백테스트 반복 I/O 완화.
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


def load_ohlcv(symbol: str, start: str, end: str) -> pd.DataFrame | None:
    """FinanceDataReader 로 OHLCV 티커 조회.(시장 인자 불필요 — KRX 코드 기준 로드.)

    v4.8: 게이트 검증 시 상장 시장 선택은 호출측(metrics·GUI 설정) 책임. 본 함수는 코드만 받는다.
    v4.10: (코드,start,end) 키 LRU—동일 구간 재조회 시 네트워크 생략.
    """
    cdf = str(symbol or "").strip().zfill(6)
    sk = (cdf, str(start).strip()[:10], str(end).strip()[:10])
    with _OHLCV_LOCK:
        if sk in _OHLCV_LRU:
            _OHLCV_LRU.move_to_end(sk)
            return _OHLCV_LRU[sk].copy()
    try:
        df = fdr.DataReader(symbol, start=start, end=end)
    except Exception:
        df = None
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


def load_ohlcv_for_chart(symbol: str, start: str, end: str) -> pd.DataFrame | None:
    """차트 전용 OHLCV: FDR 로드 후 장중 당일 봉(pykrx) 보강."""
    df = load_ohlcv(symbol, start, end)
    merged = _merge_intraday_session_bar(df, symbol, end)
    if merged is None or merged.empty:
        return None
    return merged


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


# v3.50 김직선 정배열 추세 필터 — MA120·MA5/MA10 동시 검증용 벌크 OHLCV 길이
PULLBACK_SCAN_HISTORY_BDAY = 119  # t-119..t0 = 120영업일


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


def kim_straight_trend_pass(
    close: pd.Series | np.ndarray,
    *,
    at_index: int | None = None,
) -> tuple[bool, bool, bool]:
    """
    v3.50 김직선 정배열 추세 필터.
    - 장기 정배열: 종가 > MA120
    - 단기 모멘텀: MA5 >= MA10
    반환: (통과, 장기통과, 단기통과)
    """
    if isinstance(close, pd.Series):
        arr = pd.to_numeric(close, errors="coerce").to_numpy(dtype=float)
    else:
        arr = np.asarray(close, dtype=float)
    n = len(arr)
    if n < 120:
        return False, False, False
    i = int(at_index) if at_index is not None else n - 1
    if i < 119 or i >= n:
        return False, False, False

    close_t = float(arr[i])
    ma120 = float(np.nanmean(arr[i - 119 : i + 1]))
    ma5 = float(np.nanmean(arr[i - 4 : i + 1]))
    ma10 = float(np.nanmean(arr[i - 9 : i + 1]))
    if not all(np.isfinite(v) for v in (close_t, ma120, ma5, ma10)):
        return False, False, False

    long_ok = close_t > ma120
    short_ok = ma5 >= ma10
    return bool(long_ok and short_ok), bool(long_ok), bool(short_ok)


def qualifies_leader_pullback_from_ohlcv(
    df: pd.DataFrame,
    *,
    volume_burst_multiple: float,
    vol_shrink_limit: float,
) -> tuple[bool, float]:
    """
    단일 종목 일봉에서 v3.30 주도주 눌림목 3중 조건 + v3.50 김직선 정배열 추세 필터.
    반환: (통과 여부, 당일 시가 대비 상승률 % — 리스트 정렬용).
    """
    if df is None or df.empty:
        return False, 0.0
    work = ensure_datetime_index(df.copy()).sort_index()
    if len(work) < 120:
        return False, 0.0

    vol = pd.to_numeric(work["Volume"], errors="coerce")
    low = pd.to_numeric(work["Low"], errors="coerce")
    close = pd.to_numeric(work["Close"], errors="coerce")
    opn = pd.to_numeric(work["Open"], errors="coerce")

    vol_ma20_prior = float(vol.iloc[-22:-2].mean())
    prev_vol = float(vol.iloc[-2])
    today_vol = float(vol.iloc[-1])
    ma20 = float(close.iloc[-20:].mean())
    low_t = float(low.iloc[-1])
    close_t = float(close.iloc[-1])
    open_t = float(opn.iloc[-1])

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
    cond_price = (low_t < ma20) and (close_t >= ma20)
    cond_vol = today_vol <= (prev_vol * shrink)
    if not (cond_burst and cond_price and cond_vol):
        return False, 0.0

    kim_ok, _, _ = kim_straight_trend_pass(close)
    if not kim_ok:
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
    cancel_event: threading.Event | None = None,
) -> dict[str, object]:
    """
    v3.30 주도주 눌림목 벌크 스캐너.

    - pykrx 일별 전종목 OHLCV 스냅샷 22영업일(t-21~t0) — 당일(t) 거래량이 MA20에 섞이지 않음
    - cond_prev_burst: t-1 거래량 > mean(t-2..t-21) × volume_burst_multiple
    - cond_price: t 저가 < MA20(종가 20일) & t 종가 >= MA20
    - cond_volume: t 거래량 <= t-1 거래량 × vol_shrink_limit
    - v3.50 cond_kim_long: t 종가 > MA120(120일)
    - v3.50 cond_kim_short: MA5 >= MA10
    """
    from src.utils.date_helper import resolve_overnight_scan_anchor

    m = str(market or "KOSPI").strip().upper()
    if m not in ("KOSPI", "KOSDAQ"):
        m = "KOSPI"

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
    lim = max(20, min(500, int(universe_limit)))

    if cancel_event is not None and cancel_event.is_set():
        return {"ok": False, "reason": "cancelled"}

    ainfo = resolve_overnight_scan_anchor(str(end_date).strip()[:10])
    d_today = pd.Timestamp(ainfo.anchor_date).normalize()
    bdays = pd.bdate_range(d_today - BDay(PULLBACK_SCAN_HISTORY_BDAY), d_today)
    if len(bdays) < 120:
        return {"ok": False, "reason": "ohlcv_history_short"}

    from src.market_ohlcv_bulk_cache import fetch_bulk_day_frames_cached

    anchor_ymd = d_today.strftime("%Y%m%d")

    def _fetch_raw(ymd: str) -> pd.DataFrame:
        with _temporary_socket_timeout(NETWORK_TIMEOUT_SEC):
            return pykrx_stock.get_market_ohlcv_by_ticker(ymd, market=m)

    day_frames = fetch_bulk_day_frames_cached(
        market=m,
        bdays=bdays,
        fetch_day_raw=_fetch_raw,
        prep_day=_prep_pykrx_bulk_ticker_day,
        cancel_event=cancel_event,
        anchor_ymd=anchor_ymd,
        refresh_anchor=True,
    )
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
    close_m = np.full((n_codes, n_days), np.nan, dtype=float)
    open_m = np.full((n_codes, n_days), np.nan, dtype=float)

    for j, fr in enumerate(day_frames):
        sub = fr.reindex(common_idx)
        vol_m[:, j] = pd.to_numeric(sub["Volume"], errors="coerce").to_numpy()
        low_m[:, j] = pd.to_numeric(sub["Low"], errors="coerce").to_numpy()
        close_m[:, j] = pd.to_numeric(sub["Close"], errors="coerce").to_numpy()
        open_m[:, j] = pd.to_numeric(sub["Open"], errors="coerce").to_numpy()

    merged = pd.DataFrame(index=codes)
    merged["vol_ma20_strictly_prior"] = np.nanmean(vol_m[:, n_days - 22 : n_days - 2], axis=1)
    merged["prev_vol"] = vol_m[:, n_days - 2]
    merged["today_vol"] = vol_m[:, n_days - 1]
    merged["MA20"] = np.nanmean(close_m[:, n_days - 20 : n_days], axis=1)
    merged["MA120"] = np.nanmean(close_m[:, n_days - 120 : n_days], axis=1)
    merged["MA5"] = np.nanmean(close_m[:, n_days - 5 : n_days], axis=1)
    merged["MA10"] = np.nanmean(close_m[:, n_days - 10 : n_days], axis=1)
    merged["Low_t0"] = low_m[:, n_days - 1]
    merged["Close_t0"] = close_m[:, n_days - 1]
    merged["Open_t0"] = open_m[:, n_days - 1]

    s_today = d_today.strftime("%Y%m%d")
    rank_cap_map: dict[str, float] = {}
    rank_cap_trade: dict[str, float | None] = {}
    try:
        with _temporary_socket_timeout(NETWORK_TIMEOUT_SEC):
            raw_cap_rank = pykrx_stock.get_market_cap_by_ticker(s_today, market=m)
        if raw_cap_rank is not None and not getattr(raw_cap_rank, "empty", True):
            rc = None
            ac = None
            for z in raw_cap_rank.columns:
                zs = str(z)
                if rc is None and ("시가총액" in zs or zs.lower() == "marcap"):
                    rc = z
                if ac is None and ("거래대금" in zs or "amount" in zs.lower()):
                    ac = z
            if rc is not None:
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
    except Exception:
        rank_cap_map = {}
        rank_cap_trade = {}

    mcap_scores = merged.index.map(lambda c: float(rank_cap_map.get(str(c).zfill(6), -1.0)))
    merged["_mcap_sort_tmp"] = mcap_scores
    merged["tmp_code"] = merged.index.astype(str).str.zfill(6)
    merged = merged.sort_values(
        by=["_mcap_sort_tmp", "tmp_code"],
        ascending=[False, True],
    ).head(lim)
    merged = merged.drop(columns=["_mcap_sort_tmp", "tmp_code"], errors="ignore")

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
    cond_price = (merged["Low_t0"] < merged["MA20"]) & (
        merged["Close_t0"] >= merged["MA20"]
    )
    cond_volume = merged["today_vol"] <= (merged["prev_vol"] * shrink_lim)
    pass_pullback = cond_burst & cond_price & cond_volume
    cond_kim_long = merged["Close_t0"] > merged["MA120"]
    cond_kim_short = merged["MA5"] >= merged["MA10"]
    final = merged[pass_pullback & cond_kim_long & cond_kim_short].copy()

    _stats_diag = {
        "requested_end_date": ainfo.requested_calendar_date.isoformat(),
        "effective_anchor_date": d_today.strftime("%Y-%m-%d"),
        "anchor_policy_reason": ainfo.anchor_policy_reason,
        "universe_limit_applied": int(lim),
        "volume_burst_multiple": burst_mult,
        "vol_shrink_limit": shrink_lim,
        "history_bdays": int(n_days),
    }

    if final.empty:
        return {
            "ok": True,
            "rows": [],
            "stats": {
                "total_loaded": int(len(merged)),
                "pass_burst": int(cond_burst.sum()),
                "pass_price": int((cond_burst & cond_price).sum()),
                "pass_volume": int((cond_burst & cond_price & cond_volume).sum()),
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
            "total_loaded": int(len(merged)),
            "pass_burst": int(cond_burst.sum()),
            "pass_price": int((cond_burst & cond_price).sum()),
            "pass_volume": int((cond_burst & cond_price & cond_volume).sum()),
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
    cancel_event: threading.Event | None = None,
) -> dict[str, object]:
    """레거시 이름 — v3.30 `scan_leader_pullback_candidates_bulk` 로 위임."""
    return scan_leader_pullback_candidates_bulk(
        end_date,
        market=market,
        universe_limit=universe_limit,
        volume_burst_multiple=volume_burst_multiple,
        vol_shrink_limit=vol_shrink_limit,
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
            df_raw = ensure_datetime_index(df_raw)
            # 프로젝트 공용 포맷(Open/High/Low/Close/Volume)을 그대로 사용
            needed = ["Open", "High", "Low", "Close", "Volume"]
            missing = [c for c in needed if c not in df_raw.columns]
            if missing:
                continue
            df = df_raw[needed].copy()
            for c in needed:
                df[c] = pd.to_numeric(df[c], errors="coerce")

        df = df.loc[(df.index >= pd.Timestamp(sd)) & (df.index <= pd.Timestamp(ed))].copy()
        if len(df) < 3:
            continue

        df.attrs["v3_source"] = source
        out.append((ticker, df))

    return out
