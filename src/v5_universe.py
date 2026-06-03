"""
v5.1 고정 코스닥 탄력주 유니버스 — Look-ahead 없는 시점 박제(Lock) 스캔·JSON 로드.

스캔은 universe_lock.lock_date(백테스트 시작 직전) 당시 시총·거래대금만 사용한다.
당일·미래 상장표/시총으로 과거를 채우지 않는다.
"""
from __future__ import annotations

import json
import os
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from src.data_loader import fetch_filtered_universe, fetch_pykrx_marcap_trade_krw_by_code
from src.data_loader import _prep_pykrx_bulk_ticker_day
from src.engine.portfolio_manager import _load_kosdaq_code_set, _market_snapshot_for_scan
from src.v5_config import V5Config, V5UniverseLockConfig, load_v5_config

V51_SECTION = "v5_1"
DEFAULT_UNIVERSE_REL = "config/kosdaq_sniper_universe.json"
DEFAULT_META_SUFFIX = ".meta.json"
EOK_KRW = 100_000_000  # 1억 원


def format_krw_eok(krw: float) -> str:
    """원화 → '1,450억 원' 형태 (가독성·meta.json용)."""
    if not pd.notna(krw) or krw < 0:
        return "—"
    eok = float(krw) / EOK_KRW
    if abs(eok - round(eok)) < 0.05:
        return f"{int(round(eok)):,}억 원"
    return f"{eok:,.1f}억 원"


def _resolve_path(project_root: str, rel_or_abs: str) -> str:
    p = Path(rel_or_abs)
    if p.is_absolute():
        return str(p)
    return str(Path(project_root) / p)


def _meta_path_for(universe_path: str) -> str:
    p = Path(universe_path)
    if p.suffix.lower() == ".json":
        return str(p.with_name(p.stem + DEFAULT_META_SUFFIX))
    return str(p) + DEFAULT_META_SUFFIX


def assert_universe_lock_valid(lock: V5UniverseLockConfig, *, backtest_start: str | None = None) -> None:
    """lock_date가 백테스트 시작 이전인지 검증 (미래 참조·생존 편향 방지)."""
    lock_ts = pd.Timestamp(lock.lock_date).normalize()
    start_s = (backtest_start or lock.backtest_start).strip()[:10]
    start_ts = pd.Timestamp(start_s).normalize()
    if lock_ts >= start_ts:
        raise ValueError(
            f"universe_lock.lock_date({lock.lock_date})는 backtest_start({start_s})보다 "
            "이전이어야 합니다. 미래·당일 시총/수급으로 과거를 채우면 Look-ahead가 됩니다."
        )


def _lock_ohlcv_cache_path(lock_date: str, *, market: str = "KOSDAQ") -> Path:
    ymd = pd.Timestamp(str(lock_date).strip()[:10]).strftime("%Y%m%d")
    m = str(market or "KOSDAQ").strip().upper()
    root = Path(__file__).resolve().parents[1] / "data" / "cache" / "v5_universe_lock"
    return root / f"{m}_{ymd}.pkl"


def _frame_has_ohlcv_prices(frame: pd.DataFrame) -> bool:
    if frame is None or frame.empty or "Close" not in frame.columns:
        return False
    close = pd.to_numeric(frame["Close"], errors="coerce")
    return int((close > 0).sum()) >= 10


def _fdr_ticker_bar_asof(code: str, lock_date: str) -> dict[str, float] | None:
    """FDR 단일 종목 — lock_date 이하 최근 영업일 봉(과거 lock_date용 pykrx 0 폴백)."""
    import FinanceDataReader as fdr

    c6 = str(code).strip().zfill(6)
    end = pd.Timestamp(str(lock_date).strip()[:10]).normalize()
    start = (end - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    end_s = end.strftime("%Y-%m-%d")
    try:
        df = fdr.DataReader(c6, start, end_s)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    work = df.copy()
    work.index = pd.to_datetime(work.index).normalize()
    work = work.loc[work.index <= end]
    if work.empty:
        return None
    row = work.iloc[-1]
    close = float(row.get("Close", float("nan")))
    vol = float(row.get("Volume", float("nan")))
    if not np.isfinite(close) or not np.isfinite(vol) or close <= 0 or vol <= 0:
        return None
    return {
        "Open": float(row.get("Open", close)),
        "High": float(row.get("High", close)),
        "Low": float(row.get("Low", close)),
        "Close": close,
        "Volume": vol,
        "Amount": close * vol,
    }


def _build_lock_day_ohlcv_fdr(
    lock_date: str,
    *,
    market: str = "KOSDAQ",
    max_workers: int = 8,
) -> pd.DataFrame:
    """pykrx 일별 전종목이 0일 때 FDR로 lock_date 스냅샷 구축(최초 1회·수 분 소요)."""
    codes = sorted(_load_kosdaq_code_set()) if market == "KOSDAQ" else []
    if not codes:
        return pd.DataFrame()

    print(
        f"⚠️ pykrx {lock_date} 일봉이 비어 있어 FDR 폴백으로 {len(codes)}종 조회합니다 "
        f"(최초 1회·workers={max_workers})…"
    )
    rows: dict[str, dict[str, float]] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fdr_ticker_bar_asof, c, lock_date): c for c in codes}
        for fut in as_completed(futures):
            done += 1
            if done % 200 == 0:
                print(f"   FDR 진행 {done}/{len(codes)}…")
            bar = fut.result()
            if bar:
                rows[futures[fut]] = bar
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame.from_dict(rows, orient="index").sort_index()


def _fetch_lock_day_ohlcv_frame(lock_date: str, *, market: str = "KOSDAQ") -> pd.DataFrame:
    """lock_date 당일 전종목 OHLCV — pykrx 우선, 과거 0이면 FDR 캐시."""
    cache_path = _lock_ohlcv_cache_path(lock_date, market=market)
    if cache_path.is_file():
        try:
            cached = pd.read_pickle(cache_path)
            if _frame_has_ohlcv_prices(cached):
                print(f"📂 lock OHLCV 캐시 로드: {cache_path.name}")
                return cached
        except Exception:
            pass

    frame = pd.DataFrame()
    krx_id = str(os.getenv("KRX_ID") or "").strip()
    krx_pw = str(os.getenv("KRX_PW") or "").strip()
    m = str(market or "KOSDAQ").strip().upper()
    if len(krx_id) >= 2 and len(krx_pw) >= 2 and m in ("KOSPI", "KOSDAQ"):
        try:
            from pykrx import stock as pykrx_stock  # type: ignore

            ymd = pd.Timestamp(str(lock_date).strip()[:10]).strftime("%Y%m%d")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FutureWarning)
                raw = pykrx_stock.get_market_ohlcv_by_ticker(ymd, market=m)
            if raw is not None and not getattr(raw, "empty", True):
                frame = _prep_pykrx_bulk_ticker_day(raw)
        except Exception:
            frame = pd.DataFrame()

    if not _frame_has_ohlcv_prices(frame):
        frame = _build_lock_day_ohlcv_fdr(lock_date, market=market)
        if _frame_has_ohlcv_prices(frame):
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_pickle(cache_path)
            print(f"   FDR 스냅샷 캐시 저장: {cache_path}")
    return frame


def _resolve_lock_trading_day(
    lock_date: str,
    *,
    market: str = "KOSDAQ",
) -> tuple[pd.Timestamp, pd.DataFrame, dict[str, float]]:
    """lock_date 당일 pykrx OHLCV 스냅샷 + 거래대금(Amount) 맵."""
    lock_ts = pd.Timestamp(lock_date).normalize()
    raw_frame = _fetch_lock_day_ohlcv_frame(lock_date, market=market)
    if raw_frame.empty:
        raise RuntimeError(
            f"lock_date {lock_date} OHLCV 스냅샷을 가져오지 못했습니다. "
            "KRX 자격·영업일·네트워크를 확인하세요."
        )

    day_df = _market_snapshot_for_scan(raw_frame)
    amount_by_code: dict[str, float] = {}
    if "Amount" in raw_frame.columns:
        for idx_code in raw_frame.index:
            c6 = str(idx_code).zfill(6)
            amt = pd.to_numeric(raw_frame.loc[idx_code, "Amount"], errors="coerce")
            if pd.notna(amt) and float(amt) > 0:
                amount_by_code[c6] = float(amt)
    return lock_ts, day_df, amount_by_code


def _fetch_pykrx_listed_shares_by_code(as_of_date: str, *, market: str = "KOSDAQ") -> dict[str, float]:
    """lock_date pykrx 스냅샷의 상장주식수(종가·거래대금 0이어도 주식수는 유효한 경우 많음)."""
    krx_id = str(os.getenv("KRX_ID") or "").strip()
    krx_pw = str(os.getenv("KRX_PW") or "").strip()
    if len(krx_id) < 2 or len(krx_pw) < 2:
        return {}
    m = str(market or "KOSDAQ").strip().upper()
    if m not in ("KOSPI", "KOSDAQ"):
        return {}
    try:
        from pykrx import stock as pykrx_stock  # type: ignore

        d = pd.Timestamp(str(as_of_date).strip()[:10]).strftime("%Y%m%d")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            raw = pykrx_stock.get_market_cap_by_ticker(d, market=m)
    except Exception:
        return {}
    if raw is None or getattr(raw, "empty", True):
        return {}
    shares_col = None
    for c in raw.columns:
        if "상장주식수" in str(c) or str(c).lower() in ("shares", "listed_shares"):
            shares_col = c
            break
    if shares_col is None:
        return {}
    out: dict[str, float] = {}
    for idx, row in raw.iterrows():
        code = str(idx).strip().zfill(6)
        try:
            sh = float(row[shares_col])
        except (TypeError, ValueError):
            continue
        if np.isfinite(sh) and sh > 0:
            out[code] = sh
    return out


def _pykrx_liquidity_usable(marcap_trade: dict[str, tuple[float | None, float | None]]) -> bool:
    """과거 일자에 pykrx가 시총·거래대금 0만 줄 때 False → 벌크 하이브리드."""
    ok = 0
    for _c, (mc, ta) in marcap_trade.items():
        if mc is not None and ta is not None and float(mc) > 0 and float(ta) > 0:
            ok += 1
            if ok >= 10:
                return True
    return False


def _load_kosdaq_name_map() -> dict[str, str]:
    try:
        return fetch_filtered_universe("KOSDAQ", "")
    except Exception:
        return {}


def _build_universe_meta_document(
    lock: V5UniverseLockConfig,
    *,
    lock_date_actual: str,
    source: str,
    ranked_rows: list[dict[str, object]],
) -> dict:
    """스캔 결과 마스터 메타 (kosdaq_sniper_universe.meta.json)."""
    public_source = source
    if source.startswith("pykrx"):
        public_source = "pykrx_market_snapshot"
    elif source.startswith("bulk"):
        public_source = "bulk_ohlcv_snapshot"
    elif source.startswith("fdr"):
        public_source = "fdr_lock_day_snapshot"

    report: list[dict[str, object]] = []
    for i, r in enumerate(ranked_rows, start=1):
        code = str(r["code"]).zfill(6)
        mc = float(r["marcap"]) if r.get("marcap") is not None else 0.0
        ta = float(r["trading_value"])
        report.append({
            "rank": i,
            "code": code,
            "name": str(r.get("name") or code),
            "market_cap": format_krw_eok(mc),
            "daily_volume_amt": format_krw_eok(ta),
        })

    return {
        "lock_date_actual": lock_date_actual,
        "source": public_source,
        "hard_filters": {
            "market": lock.market,
            "min_market_cap": format_krw_eok(lock.min_mcap_krw),
            "max_market_cap": format_krw_eok(lock.max_mcap_krw),
            "min_daily_volume_amt": format_krw_eok(lock.min_trade_krw),
        },
        "total_scanned_count": len(report),
        "scanned_items_report": report,
    }


def _rank_codes_at_lock(
    lock: V5UniverseLockConfig,
    *,
    project_root: str | None = None,
) -> tuple[list[str], dict, list[dict[str, object]]]:
    """
    lock_date 시점 코스닥 · 시총 [min,max] · 거래대금 하한 · Top-N.
    pykrx 과거 일자 시총/거래대금 0이면 벌크 거래대금(Amount)·종가×상장주식수 하이브리드.
    """
    assert_universe_lock_valid(lock)
    kosdaq = _load_kosdaq_code_set()
    name_map = _load_kosdaq_name_map()
    market = lock.market if lock.market in ("KOSPI", "KOSDAQ") else "KOSDAQ"

    actual_day, day_df, amount_by_code = _resolve_lock_trading_day(lock.lock_date)
    actual_s = actual_day.strftime("%Y-%m-%d")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        marcap_trade = fetch_pykrx_marcap_trade_krw_by_code(actual_s, market=market)
    shares_by_code = _fetch_pykrx_listed_shares_by_code(actual_s, market=market)

    use_pykrx_liq = _pykrx_liquidity_usable(marcap_trade)
    source = "pykrx_market_snapshot" if use_pykrx_liq else "fdr_lock_day_pykrx_shares_hybrid"
    rows: list[dict[str, object]] = []
    snap = day_df.set_index("code")

    for code in kosdaq:
        if code not in snap.index:
            continue
        srow = snap.loc[code]
        close_px = float(srow["close"])
        vol = float(srow["volume"])

        ta_f: float | None = None
        mc_f: float | None = None
        pair = marcap_trade.get(code) if marcap_trade else None

        if use_pykrx_liq and pair:
            mc, ta = pair
            if mc is not None and ta is not None and float(mc) > 0 and float(ta) > 0:
                mc_f, ta_f = float(mc), float(ta)
        else:
            ta_f = amount_by_code.get(code)
            if ta_f is None or ta_f <= 0:
                if np.isfinite(close_px) and np.isfinite(vol) and close_px > 0 and vol > 0:
                    ta_f = close_px * vol
            if pair and pair[0] is not None and float(pair[0]) > 0:
                mc_f = float(pair[0])
            elif np.isfinite(close_px) and close_px > 0 and code in shares_by_code:
                mc_f = close_px * float(shares_by_code[code])

        if ta_f is None or mc_f is None:
            continue
        if not (lock.min_mcap_krw <= mc_f <= lock.max_mcap_krw):
            continue
        if ta_f < lock.min_trade_krw:
            continue
        rows.append({
            "code": code,
            "trading_value": ta_f,
            "marcap": mc_f,
            "name": name_map.get(code, code),
        })

    if not rows:
        raise RuntimeError(
            f"lock_date={actual_s} 에서 조건에 맞는 코스닥 종목이 없습니다. "
            f"(source={source}, bulk_amount={len(amount_by_code)}, shares={len(shares_by_code)}) "
            "— lock_date·50억 거래대금·시총 700억~3,000억을 확인하세요."
        )

    ranked = sorted(rows, key=lambda r: float(r["trading_value"]), reverse=True)[: int(lock.top_n)]
    codes = [str(r["code"]).zfill(6) for r in ranked]
    meta = _build_universe_meta_document(
        lock,
        lock_date_actual=actual_s,
        source=source,
        ranked_rows=ranked,
    )
    return codes, meta, ranked


def load_v5_target_universe(
    *,
    project_root: str | None = None,
    config: V5Config | None = None,
    cfg: dict | None = None,
    validate_lock: bool = True,
) -> list[str]:
    """settings.yaml v5_1.environment.universe_profile JSON → 6자리 종목코드 리스트."""
    v5 = config if config is not None else load_v5_config(section=V51_SECTION, cfg=cfg)
    profile = v5.environment.universe_profile
    if not profile:
        raise KeyError("v5_1.environment.universe_profile 이 비어 있습니다.")

    root = project_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = _resolve_path(root, profile)
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, list):
        raise ValueError(f"유니버스 JSON은 배열이어야 합니다: {path}")
    codes = [str(c).strip().zfill(6) for c in raw if str(c).strip()]
    if not codes:
        raise ValueError(f"유니버스 JSON이 비어 있습니다: {path}")

    if validate_lock and v5.environment.universe_lock is not None:
        assert_universe_lock_valid(v5.environment.universe_lock)
        meta_path = _meta_path_for(path)
        if os.path.isfile(meta_path):
            with open(meta_path, encoding="utf-8") as fh:
                meta = json.load(fh)
            meta_lock = str(meta.get("lock_date_actual") or "").strip()
            try:
                meta_ts = pd.Timestamp(meta_lock).normalize() if meta_lock else None
            except Exception:
                meta_ts = None
            if meta_ts is not None and meta_ts >= pd.Timestamp(v5.environment.universe_lock.backtest_start).normalize():
                raise ValueError(
                    f"유니버스 메타 lock_date({meta_lock})가 backtest_start 이후입니다. "
                    "재스캔이 필요합니다."
                )
        else:
            print(
                f"⚠️ 유니버스 메타 없음({meta_path}). "
                "Look-ahead 검증을 위해 --scan-universe 로 lock_date 기준 재박제를 권장합니다."
            )
    return codes


def scan_and_write_kosdaq_sniper_universe(
    *,
    project_root: str | None = None,
    config: V5Config | None = None,
    lock: V5UniverseLockConfig | None = None,
    output_rel: str | None = None,
) -> list[str]:
    """
    universe_lock SSOT 기준 1회 박제 → JSON + .meta.json 저장.
    as_of_date 인자 없음 — 오늘 날짜·미래 상장표 사용 금지.
    """
    v5 = config if config is not None else load_v5_config(section=V51_SECTION)
    env = v5.environment
    lock_cfg = lock or env.universe_lock
    if lock_cfg is None:
        raise KeyError(
            "v5_1.environment.universe_lock 이 없습니다. "
            "lock_date·시총 밴드·top_n을 settings.yaml에 설정하세요."
        )
    profile = output_rel or env.universe_profile or DEFAULT_UNIVERSE_REL
    root = project_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_path = _resolve_path(root, profile)
    meta_path = _meta_path_for(out_path)

    codes, meta, _ = _rank_codes_at_lock(lock_cfg, project_root=root)
    os.makedirs(os.path.dirname(out_path) or root, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(codes, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    n = meta["total_scanned_count"]
    top = meta["scanned_items_report"][0] if meta["scanned_items_report"] else None
    print(
        f"✅ 유니버스 락 박제: {lock_cfg.lock_date} → {meta['lock_date_actual']} "
        f"· 정예 {n}종 (Top{lock_cfg.top_n} 상한) · {meta['source']}"
    )
    if top:
        print(
            f"   1위 {top['code']} {top['name']} · "
            f"시총 {top['market_cap']} · 거래대금 {top['daily_volume_amt']}"
        )
    print(f"   codes → {out_path}")
    print(f"   meta  → {meta_path}")
    return codes
