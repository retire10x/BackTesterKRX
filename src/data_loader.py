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


def scan_v3_overnight_candidates_bulk(
    end_date: str,
    *,
    market: str = "KOSPI",
    cancel_event: threading.Event | None = None,
    universe_limit: int | None = None,
) -> dict[str, object]:
    """
    v3.1 스캐너 고속 경로(벌크+벡터화).

    - pykrx `get_market_ohlcv_by_ticker` 3회(t0 / prev_1 / prev_2)
    - `resolve_overnight_scan_anchor` 와 동일한 영업일 인덱스 (CLI와 공통)
    - 설정 `v3_0.universe_limit`(기본 100)·시가총액 상위 순으로 벌크 병합 직후 슬라이스
    - 시총/거래대금 출력은 벌크 시총 테이블 1회 재사용(join 후 최종 종목 한정 가능)
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

    lim = universe_limit
    if lim is None:
        try:
            vpart = load_config().get("v3_0") or {}
            lim = int(vpart.get("universe_limit", 100))
        except Exception:
            lim = 100
    lim = max(20, min(300, int(lim)))

    if cancel_event is not None and cancel_event.is_set():
        return {"ok": False, "reason": "cancelled"}

    ainfo = resolve_overnight_scan_anchor(str(end_date).strip()[:10])
    d_today = pd.Timestamp(ainfo.anchor_date).normalize()
    d_prev1 = pd.Timestamp(ainfo.prev_1).normalize()
    d_prev2 = pd.Timestamp(ainfo.prev_2).normalize()

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

    def _prep(df: pd.DataFrame, suffix: str) -> pd.DataFrame:
        x = _normalize_pykrx_columns_to_ko(df)
        x.index = x.index.map(lambda v: str(v).zfill(6))
        # pykrx 일자별 전종목 시세 컬럼: 시가/고가/저가/종가/거래량/거래대금/등락률
        x = x.rename(
            columns={
                "시가": f"Open{suffix}",
                "고가": f"High{suffix}",
                "저가": f"Low{suffix}",
                "종가": f"Close{suffix}",
                "거래량": f"Volume{suffix}",
                "거래대금": f"Amount{suffix}",
                "등락률": f"ChangePct{suffix}",
            }
        )
        keep = [
            f"Open{suffix}",
            f"High{suffix}",
            f"Low{suffix}",
            f"Close{suffix}",
            f"Volume{suffix}",
            f"Amount{suffix}",
            f"ChangePct{suffix}",
        ]
        out = x[[c for c in keep if c in x.columns]].copy()
        for c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
        return out

    def _fetch_merge_triplet(
        ts0: pd.Timestamp, ts1: pd.Timestamp, ts2: pd.Timestamp
    ) -> tuple[pd.DataFrame | None, str]:
        s0 = ts0.strftime("%Y%m%d")
        s1 = ts1.strftime("%Y%m%d")
        s2 = ts2.strftime("%Y%m%d")
        try:
            with _temporary_socket_timeout(NETWORK_TIMEOUT_SEC):
                df_today = pykrx_stock.get_market_ohlcv_by_ticker(s0, market=m)
                if cancel_event is not None and cancel_event.is_set():
                    return None, "cancelled"
                df_prev1 = pykrx_stock.get_market_ohlcv_by_ticker(s1, market=m)
                if cancel_event is not None and cancel_event.is_set():
                    return None, "cancelled"
                df_prev2 = pykrx_stock.get_market_ohlcv_by_ticker(s2, market=m)
        except (TimeoutError, socket.timeout, OSError):
            return None, "timeout_bulk_ohlcv"
        except Exception:
            return None, "ohlcv_bulk_failed"

        if (
            df_today is None
            or getattr(df_today, "empty", True)
            or df_prev1 is None
            or getattr(df_prev1, "empty", True)
            or df_prev2 is None
            or getattr(df_prev2, "empty", True)
        ):
            return None, "ohlcv_bulk_empty"

        t0 = _prep(df_today, "_t0")
        t1 = _prep(df_prev1, "_t1")
        t2 = _prep(df_prev2, "_t2")

        merged_inner = t0.join(t1, how="inner").join(t2, how="inner")
        if merged_inner.empty:
            return None, "ohlcv_join_empty"
        return merged_inner, "ok"

    merged, triple_ok = _fetch_merge_triplet(d_today, d_prev1, d_prev2)
    if merged is None:
        if triple_ok == "cancelled":
            return {"ok": False, "reason": "cancelled"}
        return {"ok": False, "reason": triple_ok}

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
    h = merged.get("High_t0")
    c = merged.get("Close_t0")
    v0 = merged.get("Volume_t0")
    v1 = merged.get("Volume_t1")
    if o is None or h is None or c is None or v0 is None or v1 is None:
        return {"ok": False, "reason": "ohlcv_columns_missing"}

    vol_growth = np.where(v1 > 0, v0 / v1, 0.0)
    return_pct = np.where(o > 0, (c - o) / o * 100.0, 0.0)
    tail_ratio = np.where((h - o) > 0, (h - c) / (h - o), 1.0)

    merged["vol_growth"] = pd.to_numeric(vol_growth, errors="coerce")
    merged["return_pct"] = pd.to_numeric(return_pct, errors="coerce")
    merged["tail_ratio"] = pd.to_numeric(tail_ratio, errors="coerce")

    cond1 = merged["vol_growth"] >= 1.5
    cond2 = merged["return_pct"] >= 4.0
    cond3 = merged["tail_ratio"] <= 0.2
    final = merged[cond1 & cond2 & cond3].copy()

    v0z = pd.to_numeric(v0, errors="coerce").fillna(0.0)
    v0_zero_frac = float((v0z <= 0).mean()) if len(v0z) else 1.0
    try:
        mx_vg = float(np.nanmax(merged["vol_growth"].to_numpy(dtype=float)))
    except (TypeError, ValueError):
        mx_vg = 0.0
    try:
        mx_ret = float(np.nanmax(merged["return_pct"].to_numpy(dtype=float)))
    except (TypeError, ValueError):
        mx_ret = 0.0

    _stats_diag = {
        "requested_end_date": ainfo.requested_calendar_date.isoformat(),
        "effective_anchor_date": d_today.strftime("%Y-%m-%d"),
        "anchor_policy_reason": ainfo.anchor_policy_reason,
        "universe_limit_applied": int(lim),
        "volume_t0_zero_frac": round(v0_zero_frac, 4),
        "max_vol_growth_sample": round(mx_vg, 4),
        "max_intraday_return_pct_sample": round(mx_ret, 4),
    }

    if final.empty:
        return {
            "ok": True,
            "rows": [],
            "stats": {
                "total_loaded": int(len(merged)),
                "pass_vol": int(cond1.sum()),
                "pass_ret": int((cond1 & cond2).sum()),
                "pass_tail": 0,
                "prev_1": ainfo.prev_1.isoformat(),
                "prev_2": ainfo.prev_2.isoformat(),
                **_stats_diag,
            },
        }

    idx_order = sorted(
        final.index,
        key=lambda c: (-float(pd.to_numeric(final.loc[c, "return_pct"], errors="coerce")), str(c).zfill(6)),
    )
    out_rows: list[tuple[str, float, float | None, float | None]] = []
    for code in idx_order:
        row = final.loc[code]
        code6 = str(code).zfill(6)
        rise = float(row["return_pct"])
        proxy_amt = None
        if pd.notna(row.get("Close_t0")) and pd.notna(row.get("Volume_t0")):
            proxy_amt = float(row["Close_t0"]) * float(row["Volume_t0"])
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
            "pass_vol": int(cond1.sum()),
            "pass_ret": int((cond1 & cond2).sum()),
            "pass_tail": int(len(final)),
            "prev_1": ainfo.prev_1.isoformat(),
            "prev_2": ainfo.prev_2.isoformat(),
            **_stats_diag,
        },
    }


def load_v3_0_overnight_scalper_data(
    *,
    start_date: str,
    end_date: str,
    market: str = "KOSPI",
    universe_limit: int = 100,
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
