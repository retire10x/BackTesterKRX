"""
매일 장마감 전 코스닥 주도주 실시간 스캔 → config/live_today_universe.json
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from src.live.live_config import LiveScreenerConfig, LiveTradingConfig, load_live_config, resolve_live_paths
from src.v5_config import V5UniverseLockConfig
from src.v5_universe import (
    _build_universe_meta_document,
    _rank_codes_at_lock,
    format_krw_eok,
    write_universe_bundle,
)

KST = ZoneInfo("Asia/Seoul")


def screener_to_lock_config(
    screener: LiveScreenerConfig,
    *,
    as_of_date: str | None = None,
) -> V5UniverseLockConfig:
    today = as_of_date or datetime.now(KST).strftime("%Y-%m-%d")
    return V5UniverseLockConfig(
        lock_date=today,
        backtest_start=(pd.Timestamp(today) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        market=screener.market,
        min_mcap_krw=screener.min_market_cap,
        max_mcap_krw=screener.max_market_cap,
        top_n=screener.top_n,
        min_trade_krw=screener.min_live_volume_amt,
    )


def run_live_screener(
    *,
    config: LiveTradingConfig | None = None,
    project_root: str | None = None,
    as_of_date: str | None = None,
) -> list[str]:
    """당일 시총·거래대금 필터 → Top-N JSON/meta 저장."""
    cfg = config if config is not None else load_live_config()
    root = project_root or str(Path(__file__).resolve().parents[2])
    paths = resolve_live_paths(cfg, root)
    lock = screener_to_lock_config(cfg.screener, as_of_date=as_of_date)

    print(
        f"📡 라이브 스크리너 ({lock.lock_date}) · "
        f"시총 {format_krw_eok(lock.min_mcap_krw)}~{format_krw_eok(lock.max_mcap_krw)} · "
        f"거래대금≥{format_krw_eok(lock.min_trade_krw)} · Top{lock.top_n}"
    )
    codes, meta, ranked = _rank_codes_at_lock(lock, project_root=root)
    meta = dict(meta)
    meta["live_screener"] = True
    meta["screener_time_kst"] = cfg.screener.screener_time

    out_path = paths["universe_json"]
    write_universe_bundle(codes, meta, out_path)
    print(f"✅ 라이브 유니버스 {len(codes)}종 → {out_path}")
    if ranked:
        top = ranked[0]
        print(
            f"   1위 {top['code']} {top.get('name')} · "
            f"시총 {format_krw_eok(float(top['marcap']))} · "
            f"거래대금 {format_krw_eok(float(top['trading_value']))}"
        )
    return codes


def load_live_universe(
    *,
    config: LiveTradingConfig | None = None,
    project_root: str | None = None,
) -> list[str]:
    cfg = config if config is not None else load_live_config()
    paths = resolve_live_paths(cfg, project_root)
    path = paths["universe_json"]
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"라이브 유니버스 없음: {path}\n"
            "먼저 실행: python run_live_bot.py screener"
        )
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    return [str(c).strip().zfill(6) for c in raw if str(c).strip()]
