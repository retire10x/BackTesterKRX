"""
v5.3 6개월 릴레이 유니버스 스캐너 — 구간별 lock_date 박제·JSON/meta 일괄 생성.

7구간 타임라인(2023-01-01 ~ 2026-05-31) SSOT. lock_date 휴장 시 실질 영업일은
_rank_codes_at_lock → meta.lock_date_actual 에 기록된다.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.v5_config import V5Config, V5ScreenerConfig, V5UniverseLockConfig, load_v5_relay_config
from src.v5_universe import (
    _meta_path_for,
    _rank_codes_at_lock,
    _resolve_lock_trading_day,
    format_krw_eok,
    write_universe_bundle,
)

RELAY_BACKTEST_START = "2023-01-01"
RELAY_BACKTEST_END = "2026-05-31"


@dataclass(frozen=True)
class RelayPhaseSpec:
    phase_id: int
    segment_start: str
    segment_end: str
    lock_date_nominal: str
    json_basename: str


RELAY_PHASES: tuple[RelayPhaseSpec, ...] = (
    RelayPhaseSpec(1, "2023-01-01", "2023-06-30", "2022-12-29", "univ_phase_1.json"),
    RelayPhaseSpec(2, "2023-07-01", "2023-12-31", "2023-06-29", "univ_phase_2.json"),
    RelayPhaseSpec(3, "2024-01-01", "2024-06-30", "2023-12-28", "univ_phase_3.json"),
    RelayPhaseSpec(4, "2024-07-01", "2024-12-31", "2024-06-27", "univ_phase_4.json"),
    RelayPhaseSpec(5, "2025-01-01", "2025-06-30", "2024-12-30", "univ_phase_5.json"),
    RelayPhaseSpec(6, "2025-07-01", "2025-12-31", "2025-06-27", "univ_phase_6.json"),
    RelayPhaseSpec(7, "2026-01-01", "2026-05-31", "2025-12-29", "univ_phase_7.json"),
)


@dataclass
class RelayPhaseScanResult:
    phase: RelayPhaseSpec
    codes: list[str]
    universe_path: str
    meta_path: str
    lock_date_actual: str
    lock_date_nominal: str
    is_holiday_adjusted: bool


def _resolve_project_root(project_root: str | None) -> str:
    return project_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_universe_dir(v5: V5Config, project_root: str) -> str:
    rel = v5.environment.universe_dir or "config/relay_universes/"
    p = Path(rel)
    if p.is_absolute():
        return str(p)
    return str(Path(project_root) / p)


def lock_config_for_phase(
    phase: RelayPhaseSpec,
    screener: V5ScreenerConfig,
) -> V5UniverseLockConfig:
    return V5UniverseLockConfig(
        lock_date=phase.lock_date_nominal,
        backtest_start=phase.segment_start,
        market=screener.market,
        min_mcap_krw=screener.min_mcap_krw,
        max_mcap_krw=screener.max_mcap_krw,
        top_n=screener.top_n,
        min_trade_krw=screener.min_trade_krw,
    )


def check_lock_trading_day(
    lock_date_nominal: str,
    *,
    market: str = "KOSDAQ",
) -> tuple[str, bool]:
    """락 기준일 → 실질 영업일. (actual, 휴장조정여부)."""
    nominal = pd.Timestamp(lock_date_nominal).normalize()
    actual_ts, _, _ = _resolve_lock_trading_day(lock_date_nominal, market=market)
    actual = actual_ts.normalize()
    adjusted = actual != nominal
    return actual.strftime("%Y-%m-%d"), adjusted


def scan_relay_phase(
    phase: RelayPhaseSpec,
    *,
    screener: V5ScreenerConfig,
    universe_dir: str,
    project_root: str | None = None,
) -> RelayPhaseScanResult:
    root = _resolve_project_root(project_root)
    lock = lock_config_for_phase(phase, screener)
    actual_nom, adjusted = check_lock_trading_day(
        phase.lock_date_nominal,
        market=screener.market,
    )
    if adjusted:
        print(
            f"  ℹ️ [{phase.phase_id}구간] 락 {phase.lock_date_nominal} 휴장 → "
            f"실질 영업일 {actual_nom}"
        )

    codes, meta, _ = _rank_codes_at_lock(lock, project_root=root)
    meta = dict(meta)
    meta["relay_phase"] = phase.phase_id
    meta["segment_start"] = phase.segment_start
    meta["segment_end"] = phase.segment_end
    meta["lock_date_nominal"] = phase.lock_date_nominal

    out_path = str(Path(universe_dir) / phase.json_basename)
    write_universe_bundle(codes, meta, out_path)
    meta_path = _meta_path_for(out_path)

    n = meta["total_scanned_count"]
    top = meta["scanned_items_report"][0] if meta["scanned_items_report"] else None
    print(
        f"✅ [{phase.phase_id}구간] {phase.segment_start}~{phase.segment_end} · "
        f"락 {phase.lock_date_nominal}→{meta['lock_date_actual']} · {n}종"
    )
    if top:
        print(
            f"   1위 {top['code']} {top['name']} · "
            f"시총 {top['market_cap']} · 거래대금 {top['daily_volume_amt']}"
        )
    print(f"   → {out_path}")

    return RelayPhaseScanResult(
        phase=phase,
        codes=codes,
        universe_path=out_path,
        meta_path=meta_path,
        lock_date_actual=str(meta["lock_date_actual"]),
        lock_date_nominal=phase.lock_date_nominal,
        is_holiday_adjusted=adjusted,
    )


def scan_all_relay_universes(
    *,
    v5: V5Config | None = None,
    project_root: str | None = None,
    phases: tuple[RelayPhaseSpec, ...] = RELAY_PHASES,
) -> list[RelayPhaseScanResult]:
    """7구간 유니버스 JSON + meta + relay_manifest.json 일괄 생성."""
    cfg = v5 if v5 is not None else load_v5_relay_config()
    screener = cfg.screener
    if screener is None:
        raise KeyError("v5_3.strategy.screener 가 필요합니다.")

    root = _resolve_project_root(project_root)
    universe_dir = _resolve_universe_dir(cfg, root)
    os.makedirs(universe_dir, exist_ok=True)

    print(f"📡 v5.3 릴레이 유니버스 스캔 ({len(phases)}구간) → {universe_dir}")
    results: list[RelayPhaseScanResult] = []
    manifest_phases: list[dict] = []

    for phase in phases:
        print(f"\n--- {phase.phase_id}구간 ({phase.segment_start} ~ {phase.segment_end}) ---")
        r = scan_relay_phase(
            phase,
            screener=screener,
            universe_dir=universe_dir,
            project_root=root,
        )
        results.append(r)
        top_leader: dict = {}
        if os.path.isfile(r.meta_path):
            with open(r.meta_path, encoding="utf-8") as mfh:
                meta_doc = json.load(mfh)
            report = meta_doc.get("scanned_items_report") or []
            if report:
                top_leader = report[0]
        manifest_phases.append({
            "phase_id": phase.phase_id,
            "segment_start": phase.segment_start,
            "segment_end": phase.segment_end,
            "lock_date_nominal": phase.lock_date_nominal,
            "lock_date_actual": r.lock_date_actual,
            "holiday_adjusted": r.is_holiday_adjusted,
            "universe_json": phase.json_basename,
            "meta_json": Path(phase.json_basename).stem + ".meta.json",
            "count": len(r.codes),
            "top_leader": top_leader,
        })

    manifest = {
        "strategy": cfg.strategy.strategy_name,
        "relay_interval_months": cfg.environment.relay_interval_months,
        "backtest_start": RELAY_BACKTEST_START,
        "backtest_end": RELAY_BACKTEST_END,
        "screener": {
            "market": screener.market,
            "min_market_cap": format_krw_eok(screener.min_mcap_krw),
            "max_market_cap": format_krw_eok(screener.max_mcap_krw),
            "min_daily_volume_amt": format_krw_eok(screener.min_trade_krw),
            "top_n_limit": screener.top_n,
        },
        "phases": manifest_phases,
    }
    manifest_path = str(Path(universe_dir) / "relay_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"\n📋 릴레이 마스터 리포트 → {manifest_path}")
    return results


def load_relay_universe_codes(
    phase: RelayPhaseSpec,
    *,
    universe_dir: str,
) -> list[str]:
    path = str(Path(universe_dir) / phase.json_basename)
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, list):
        raise ValueError(f"유니버스 JSON은 배열이어야 합니다: {path}")
    codes = [str(c).strip().zfill(6) for c in raw if str(c).strip()]
    if not codes:
        raise ValueError(f"유니버스 JSON이 비어 있습니다: {path}")
    return codes
