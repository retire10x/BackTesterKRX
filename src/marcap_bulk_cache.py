"""
pykrx 일별 시가총액 벌크 스냅샷 로컬 캐시.

과거 영업일은 data/cache/marcap_by_ticker/{MARKET}/{YYYYMMDD}.pkl 에 저장.
중단 후 재실행 시 캐시 히트 구간은 네트워크 호출을 생략합니다.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

_CACHE_LOCK = threading.Lock()
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
CACHE_ROOT = _PROJECT_ROOT / "data" / "cache" / "marcap_by_ticker"


def marcap_day_cache_path(market: str, ymd: str) -> Path:
    m = str(market or "KOSPI").strip().upper()
    if m not in ("KOSPI", "KOSDAQ"):
        m = "KOSPI"
    d = str(ymd).strip().replace("-", "")[:8]
    return CACHE_ROOT / m / f"{d}.pkl"


def load_cached_marcap_day(market: str, ymd: str) -> dict[str, float] | None:
    path = marcap_day_cache_path(market, ymd)
    if not path.is_file():
        return None
    try:
        raw = pd.read_pickle(path)
    except Exception:
        return None
    if not isinstance(raw, dict) or not raw:
        return None
    out: dict[str, float] = {}
    for code, mc in raw.items():
        try:
            v = float(mc)
        except (TypeError, ValueError):
            continue
        if np.isfinite(v) and v > 0:
            out[str(code).zfill(6)] = v
    return out or None


def save_cached_marcap_day(market: str, ymd: str, data: dict[str, float]) -> None:
    if not data:
        return
    path = marcap_day_cache_path(market, ymd)
    with _CACHE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.to_pickle({str(k).zfill(6): float(v) for k, v in data.items()}, path)


def build_marcap_cache_dual(
    bdays: pd.DatetimeIndex,
    fetch_fn: Callable[..., dict],
    *,
    verbose: bool = True,
) -> dict[tuple[str, str], float]:
    """
    KOSPI+KOSDAQ 일별 시총 캐시 — 디스크 히트 우선, 미스만 pykrx 조회 후 즉시 저장.
    """
    cache: dict[tuple[str, str], float] = {}
    total = len(bdays)
    cache_hit_days = 0
    fetch_days = 0

    for i, dt in enumerate(bdays):
        date_s = pd.Timestamp(dt).normalize().strftime("%Y-%m-%d")
        ymd = date_s.replace("-", "")
        day_had_fetch = False

        for market in ("KOSPI", "KOSDAQ"):
            cached = load_cached_marcap_day(market, ymd)
            if cached is not None:
                for c6, mc in cached.items():
                    cache[(date_s, c6)] = mc
                continue

            snap = fetch_fn(date_s, market=market)
            day_map: dict[str, float] = {}
            for code, pair in snap.items():
                mc = pair[0] if isinstance(pair, (tuple, list)) else pair
                c6 = str(code).zfill(6)
                if mc is not None and np.isfinite(mc) and float(mc) > 0:
                    fv = float(mc)
                    cache[(date_s, c6)] = fv
                    day_map[c6] = fv
            save_cached_marcap_day(market, ymd, day_map)
            day_had_fetch = True

        if day_had_fetch:
            fetch_days += 1
        else:
            cache_hit_days += 1

        if verbose and (i == 0 or (i + 1) % 60 == 0 or i + 1 == total):
            print(
                f"   시총 캐시 {i + 1}/{total}일 · {len(cache):,}건 "
                f"(디스크 {cache_hit_days} · 신규조회 {fetch_days})",
                flush=True,
            )

    if verbose:
        print(
            f"   시총 캐시 완료 — 총 {len(cache):,}건 · "
            f"디스크 히트 {cache_hit_days}일 · pykrx 조회 {fetch_days}일",
            flush=True,
        )
    return cache
