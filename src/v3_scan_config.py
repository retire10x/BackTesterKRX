"""
v3.30 스캔·백테스트 파라미터 단일 출처(SSOT).

기본값은 config/settings.yaml 의 v3_0 섹션만 정의합니다.
GUI 런타임 값은 last_session.json(종료 시 저장) → GUI StringVar → 엔진 인자 주입.
엔진(data_loader, pullback_backtest)은 호출자가 반드시 수치를 넘깁니다.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.data_loader import load_config


@dataclass(frozen=True)
class PullbackScanParams:
    universe_limit: int
    volume_burst_multiple: float
    vol_shrink_limit: float
    kim_trend_filter: bool = True


def _v3_yaml_section(cfg: dict | None = None) -> dict:
    c = cfg if cfg is not None else load_config()
    raw = c.get("v3_0")
    return raw if isinstance(raw, dict) else {}


def default_pullback_scan_params(cfg: dict | None = None) -> PullbackScanParams:
    """YAML v3_0 기준 기본 스캔 파라미터(프로젝트 유일한 숫자 fallback)."""
    v3 = _v3_yaml_section(cfg)
    return PullbackScanParams(
        universe_limit=int(v3.get("universe_limit", 300)),
        volume_burst_multiple=float(v3.get("volume_burst_multiple", 1.5)),
        vol_shrink_limit=float(v3.get("vol_shrink_limit", 0.8)),
        kim_trend_filter=bool(v3.get("kim_trend_filter", True)),
    )


def pullback_scan_params_from_mapping(data: dict) -> PullbackScanParams:
    """세션 JSON·CLI 오버레이 — 미지정 키는 YAML 기본값."""
    base = default_pullback_scan_params()
    ul = data.get("universe_limit", base.universe_limit)
    try:
        universe_limit = int(ul)
    except (TypeError, ValueError):
        universe_limit = base.universe_limit
    try:
        burst = float(str(data.get("volume_burst_multiple", base.volume_burst_multiple)).replace(",", ""))
    except (TypeError, ValueError):
        burst = base.volume_burst_multiple
    try:
        shrink = float(str(data.get("vol_shrink_limit", base.vol_shrink_limit)).replace(",", ""))
    except (TypeError, ValueError):
        shrink = base.vol_shrink_limit
    return PullbackScanParams(
        universe_limit=universe_limit,
        volume_burst_multiple=burst,
        vol_shrink_limit=shrink,
        kim_trend_filter=base.kim_trend_filter,
    )
