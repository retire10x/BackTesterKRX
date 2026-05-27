"""
데이터 수집·정렬·주봉 집계·설정 YAML 로드 (FinanceDataReader 등).
필요 시 pykrx 등으로 확장. GUI 비의존.
v4.10: FDR 상장표 메모리 캐시(TTL)·OHLCV LRU—스크리너·백테스트 반복 I/O 완화.
"""
from __future__ import annotations

import calendar
import os
import threading
import time
from collections import OrderedDict
from datetime import date

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


def clear_ohlcv_cache() -> None:
    """테스트 또는 메모리 회수 시 사용."""
    with _OHLCV_LOCK:
        _OHLCV_LRU.clear()


# ============================================================
# v2.0 (Intraday Gap Scalper) — [1단계] Data Loader
# ============================================================

def _normalize_v2_0_pykrx_ohlcv_columns(df: pd.DataFrame) -> pd.DataFrame:
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
        # v2.0 엔진의 신호/청산은 OHLCV 필수이므로, 누락되면 빈 df로 반환
        return pd.DataFrame()

    out = out[needed].copy()
    for c in needed:
        out[c] = pd.to_numeric(out[c], errors="coerce")

    return out


def load_v2_0_intraday_gap_scalper_data(
    *,
    start_date: str,
    end_date: str,
    market: str = "KOSPI",
    universe_limit: int = 100,
    warm_bdays: int = 3,
) -> list[tuple[str, pd.DataFrame]]:
    """
    v2.0용 KOSPI Universe 동적 확보 + 일봉 OHLCV 로드 + Look-ahead-safe 전처리

    - Universe: `pykrx.stock.get_market_ticker_list(start_date, market=...)`
    - OHLCV: `pykrx.stock.get_market_ohlcv_by_date(warm_start, end_date, ticker)`
    - 전처리:
        * gap_pct = (오늘 Open - 전일 Close) / 전일 Close * 100
        * vol_ratio = (전일 Volume / 전전일 Volume) * 100  (요청 보정 반영)
    - 시그널 생성에서 필요한 과거봉은 `shift()`로 계산되며,
      warm_bdays 이전 데이터만 로드해도 (시프트 기반이라) 미래 참조가 발생하지 않습니다.
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

            tickers = pykrx_stock.get_market_ticker_list(sd, market=m) or []
            tickers = [str(x).strip().zfill(6) for x in tickers if str(x).strip()]
        except Exception:
            tickers = []

    if universe_limit and universe_limit > 0:
        tickers = tickers[: int(universe_limit)]

    if not tickers:
        source = "fdr"
        uni_map = fetch_filtered_universe(m, "")
        tickers = sorted(str(c).strip().zfill(6) for c in uni_map.keys() if str(c).strip())
        if universe_limit and universe_limit > 0:
            tickers = tickers[: int(universe_limit)]

    out: list[tuple[str, pd.DataFrame]] = []
    for ticker in tickers:
        if source == "pykrx":
            try:
                from pykrx import stock as pykrx_stock  # type: ignore

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

            if len(raw) < 2:
                continue

            df = _normalize_v2_0_pykrx_ohlcv_columns(raw)
            if df.empty or len(df) < 2:
                continue
        else:
            df_raw = load_ohlcv(ticker, warm_start, ed)
            if df_raw is None or df_raw.empty or len(df_raw) < 2:
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

        # 사용자 지정 기간으로 슬라이싱(전처리에 필요한 warm rows는 shift 계산에만 사용)
        df = df.loc[(df.index >= pd.Timestamp(sd)) & (df.index <= pd.Timestamp(ed))].copy()
        if len(df) < 2:
            continue

        prev_close = df["Close"].shift(1)
        prev_vol = df["Volume"].shift(1)
        prevprev_vol = df["Volume"].shift(2)

        # 0/NaN 방어
        gap_pct = np.where(
            (prev_close > 0) & np.isfinite(prev_close),
            (df["Open"] - prev_close) / prev_close * 100.0,
            np.nan,
        )
        vol_ratio = np.where(
            (prevprev_vol > 0) & np.isfinite(prevprev_vol) & np.isfinite(prev_vol),
            (prev_vol / prevprev_vol) * 100.0,
            np.nan,
        )

        df["gap_pct"] = gap_pct.astype(float)
        df["vol_ratio"] = vol_ratio.astype(float)
        df.attrs["v2_source"] = source

        out.append((ticker, df))

    return out
