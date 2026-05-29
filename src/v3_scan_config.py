"""
v3.30 스캔·백테스트 파라미터 단일 출처(SSOT).

유일한 숫자·불리언 기본값: config/settings.yaml 의 v3_0 섹션.
GUI 런타임: last_session.json(있으면 마스터 위 덮어쓰기) → StringVar → 엔진 강제 주입.
엔진(data_loader, pullback_backtest)은 호출자가 반드시 수치를 넘깁니다(기본 인자 없음).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from src.data_loader import load_config

LAST_SESSION_JSON = os.path.join("config", "last_session.json")

_REQUIRED_V3_KEYS = (
    "universe_limit",
    "volume_burst_multiple",
    "vol_shrink_limit",
    "kim_trend_filter",
    "use_momentum_filter",
)


@dataclass(frozen=True)
class PullbackScanParams:
    universe_limit: int
    volume_burst_multiple: float
    vol_shrink_limit: float
    kim_trend_filter: bool
    use_momentum_filter: bool


def _v3_yaml_section(cfg: dict | None = None) -> dict:
    c = cfg if cfg is not None else load_config()
    raw = c.get("v3_0")
    if not isinstance(raw, dict):
        raise KeyError(
            "config/settings.yaml 에 v3_0 섹션이 없습니다. "
            f"필수 키: {', '.join(_REQUIRED_V3_KEYS)}"
        )
    missing = [k for k in _REQUIRED_V3_KEYS if k not in raw]
    if missing:
        raise KeyError(
            "config/settings.yaml v3_0 에 필수 키가 없습니다: "
            + ", ".join(missing)
        )
    return raw


def pullback_scan_params_from_yaml_section(v3: dict) -> PullbackScanParams:
    """YAML v3_0 블록만으로 PullbackScanParams 생성(폴백 없음)."""
    missing = [k for k in _REQUIRED_V3_KEYS if k not in v3]
    if missing:
        raise KeyError("v3_0 필수 키 누락: " + ", ".join(missing))
    return PullbackScanParams(
        universe_limit=int(v3["universe_limit"]),
        volume_burst_multiple=float(v3["volume_burst_multiple"]),
        vol_shrink_limit=float(v3["vol_shrink_limit"]),
        kim_trend_filter=bool(v3["kim_trend_filter"]),
        use_momentum_filter=bool(v3["use_momentum_filter"]),
    )


def default_pullback_scan_params(cfg: dict | None = None) -> PullbackScanParams:
    """settings.yaml v3_0 마스터만 읽음."""
    return pullback_scan_params_from_yaml_section(_v3_yaml_section(cfg))


def read_last_session_mapping() -> dict | None:
    """config/last_session.json 내용. 없거나 손상 시 None."""
    if not os.path.isfile(LAST_SESSION_JSON):
        return None
    try:
        with open(LAST_SESSION_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _overlay_pullback_scan_params(
    base: PullbackScanParams, data: dict
) -> PullbackScanParams:
    """세션·CLI 오버레이 — 지정된 키만 base 위에 덮어씀."""
    ul = base.universe_limit
    if "universe_limit" in data and data["universe_limit"] is not None:
        ul = int(data["universe_limit"])

    burst = base.volume_burst_multiple
    if "volume_burst_multiple" in data and data["volume_burst_multiple"] is not None:
        burst = float(
            str(data["volume_burst_multiple"]).replace(",", "")
        )

    shrink = base.vol_shrink_limit
    if "vol_shrink_limit" in data and data["vol_shrink_limit"] is not None:
        shrink = float(str(data["vol_shrink_limit"]).replace(",", ""))

    kim = base.kim_trend_filter
    if "kim_trend_filter" in data and data["kim_trend_filter"] is not None:
        kim = bool(data["kim_trend_filter"])

    momentum = base.use_momentum_filter
    if "use_momentum_filter" in data and data["use_momentum_filter"] is not None:
        momentum = bool(data["use_momentum_filter"])

    return PullbackScanParams(
        universe_limit=ul,
        volume_burst_multiple=burst,
        vol_shrink_limit=shrink,
        kim_trend_filter=kim,
        use_momentum_filter=momentum,
    )


def resolve_effective_pullback_scan_params(
    cfg: dict | None = None,
) -> PullbackScanParams:
    """
    앱 기동 SSOT: settings.yaml → (있으면) last_session.json 덮어쓰기.
    """
    master = default_pullback_scan_params(cfg)
    session = read_last_session_mapping()
    if not session:
        return master
    return _overlay_pullback_scan_params(master, session)


def pullback_scan_params_from_mapping(
    data: dict, *, cfg: dict | None = None
) -> PullbackScanParams:
    """CLI v3_0 블록·세션 dict 등 — YAML 마스터 위 오버레이."""
    base = default_pullback_scan_params(cfg)
    if not data:
        return base
    return _overlay_pullback_scan_params(base, data)
