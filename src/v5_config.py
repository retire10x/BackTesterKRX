"""
v5.0 코스닥 스나이퍼 — SSOT.

유일한 숫자 기본값: config/settings.yaml 의 v5_0 섹션.
엔진은 v4 portfolio_manager Phase I 경로를 재사용하며, V4Config 어댑터로 주입한다.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.data_loader import load_config
from src.v4_config import (
    V4Config,
    V4CostsConfig,
    V4EngineConfig,
    V4PortfolioConfig,
    V4StrategyConfig,
    v4_config_from_yaml_section,
)


@dataclass(frozen=True)
class V5KosdaqUniverseConfig:
    min_mcap_krw: float
    max_mcap_krw: float
    min_anchor_trade_krw: float
    anchor_top_n: int
    volume_dry_ratio: float


@dataclass(frozen=True)
class V5Config:
    environment_mode: str
    environment_initial_cash: float
    strategy: V4StrategyConfig
    portfolio: V4PortfolioConfig
    costs: V4CostsConfig
    kosdaq: V5KosdaqUniverseConfig


def _v5_yaml_section(cfg: dict | None = None) -> dict:
    c = cfg if cfg is not None else load_config()
    raw = c.get("v5_0")
    if not isinstance(raw, dict):
        raise KeyError(
            "config/settings.yaml 에 v5_0 섹션이 없습니다. "
            "environment / strategy / portfolio / kosdaq_universe / costs 블록을 추가하세요."
        )
    return raw


def v5_config_from_yaml_section(v5: dict) -> V5Config:
    environment = v5.get("environment") or {}
    if not isinstance(environment, dict):
        raise KeyError("v5_0.environment 블록 형식이 올바르지 않습니다.")
    kosdaq = v5.get("kosdaq_universe")
    if not isinstance(kosdaq, dict):
        raise KeyError("v5_0.kosdaq_universe 블록이 없습니다.")

    required_k = (
        "min_mcap_krw",
        "max_mcap_krw",
        "min_anchor_trade_krw",
        "anchor_top_n",
        "volume_dry_ratio",
    )
    missing_k = [k for k in required_k if k not in kosdaq]
    if missing_k:
        raise KeyError("v5_0.kosdaq_universe 필수 키 누락: " + ", ".join(missing_k))

    v4 = v4_config_from_yaml_section(v5)
    return V5Config(
        environment_mode=v4.environment_mode,
        environment_initial_cash=v4.environment_initial_cash,
        strategy=v4.strategy,
        portfolio=v4.portfolio,
        costs=v4.costs,
        kosdaq=V5KosdaqUniverseConfig(
            min_mcap_krw=float(kosdaq["min_mcap_krw"]),
            max_mcap_krw=float(kosdaq["max_mcap_krw"]),
            min_anchor_trade_krw=float(kosdaq["min_anchor_trade_krw"]),
            anchor_top_n=int(kosdaq["anchor_top_n"]),
            volume_dry_ratio=float(kosdaq["volume_dry_ratio"]),
        ),
    )


def load_v5_config(cfg: dict | None = None) -> V5Config:
    return v5_config_from_yaml_section(_v5_yaml_section(cfg))


def v5_to_v4_config(v5: V5Config) -> V4Config:
    """portfolio_manager 주입용 — engine.phase_mode=i 고정."""
    return V4Config(
        environment_mode=v5.environment_mode,
        environment_initial_cash=v5.environment_initial_cash,
        engine=V4EngineConfig(phase_mode="i"),
        strategy=v5.strategy,
        portfolio=v5.portfolio,
        costs=v5.costs,
    )
