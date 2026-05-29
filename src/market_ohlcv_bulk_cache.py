"""
pykrx 일별 전종목 OHLCV 벌크 스냅샷 로컬 캐시.

과거 영업일은 data/cache/ohlcv_by_ticker/{MARKET}/{YYYYMMDD}.pkl 에 저장하고,
스캔 시 캐시 히트 시 네트워크 호출을 생략합니다. 앵커일(최신 영업일)만 재조회합니다.
"""
from __future__ import annotations

import threading
from pathlib import Path

import pandas as pd

_CACHE_LOCK = threading.Lock()
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
CACHE_ROOT = _PROJECT_ROOT / "data" / "cache" / "ohlcv_by_ticker"


def bulk_day_cache_path(market: str, ymd: str) -> Path:
    m = str(market or "KOSPI").strip().upper()
    if m not in ("KOSPI", "KOSDAQ"):
        m = "KOSPI"
    d = str(ymd).strip().replace("-", "")[:8]
    return CACHE_ROOT / m / f"{d}.pkl"


def _is_anchor_ymd(ymd: str, anchor_ymd: str | None) -> bool:
    if not anchor_ymd:
        return False
    return str(ymd).strip()[:8] == str(anchor_ymd).strip()[:8]


def load_cached_bulk_day(market: str, ymd: str) -> pd.DataFrame | None:
    path = bulk_day_cache_path(market, ymd)
    if not path.is_file():
        return None
    try:
        df = pd.read_pickle(path)
    except Exception:
        return None
    if df is None or getattr(df, "empty", True):
        return None
    return df


def save_cached_bulk_day(market: str, ymd: str, df: pd.DataFrame) -> None:
    if df is None or getattr(df, "empty", True):
        return
    path = bulk_day_cache_path(market, ymd)
    with _CACHE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_pickle(path)


def fetch_bulk_day_frames_cached(
    *,
    market: str,
    bdays: pd.DatetimeIndex,
    fetch_day_raw,
    prep_day,
    cancel_event: threading.Event | None = None,
    anchor_ymd: str | None = None,
    refresh_anchor: bool = True,
) -> list[pd.DataFrame] | None:
    """
    영업일별 전종목 OHLCV DataFrame 리스트.
    fetch_day_raw(ymd) -> raw pykrx DataFrame
    prep_day(raw) -> ticker index, Open/High/Low/Close/Volume columns
    """
    if bdays is None or len(bdays) == 0:
        return None

    anchor = str(anchor_ymd or bdays[-1].strftime("%Y%m%d"))[:8]
    day_frames: list[pd.DataFrame] = []

    for d_ts in bdays:
        if cancel_event is not None and cancel_event.is_set():
            return None
        ymd = d_ts.strftime("%Y%m%d")
        use_cache = not (_is_anchor_ymd(ymd, anchor) and refresh_anchor)
        fr: pd.DataFrame | None = None
        if use_cache:
            fr = load_cached_bulk_day(market, ymd)
        if fr is None:
            try:
                raw = fetch_day_raw(ymd)
            except Exception:
                return None
            if raw is None or getattr(raw, "empty", True):
                return None
            fr = prep_day(raw)
            save_cached_bulk_day(market, ymd, fr)
        day_frames.append(fr)
    return day_frames
