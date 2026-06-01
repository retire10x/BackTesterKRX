"""
v4.0 포트폴리오·Phase G 파라미터 SSOT.

유일한 숫자 기본값: config/settings.yaml 의 v4_0 섹션.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.data_loader import load_config

_REQUIRED_STRATEGY_KEYS = (
    "nuliim_ratio",
    "fixed_invest_amount",
    "stop_loss_ratio",
    "target_profit_ratio",
    "max_track_days",
    "max_hold_days",
    "min_invest_amount",
)
_REQUIRED_PORTFOLIO_KEYS = ("initial_cash", "max_slots")
_REQUIRED_COSTS_KEYS = ("buy_fee", "sell_fee_tax")


@dataclass(frozen=True)
class V4StrategyConfig:
    nuliim_ratio: float
    fixed_invest_amount: float
    stop_loss_ratio: float
    target_profit_ratio: float
    max_track_days: int
    max_hold_days: int
    min_invest_amount: float
    max_daily_cash_deploy_ratio: float


@dataclass(frozen=True)
class V4PortfolioConfig:
    initial_cash: float
    max_slots: int


@dataclass(frozen=True)
class V4CostsConfig:
    buy_fee: float
    sell_fee_tax: float


@dataclass(frozen=True)
class V4Config:
    strategy: V4StrategyConfig
    portfolio: V4PortfolioConfig
    costs: V4CostsConfig


def _v4_yaml_section(cfg: dict | None = None) -> dict:
    c = cfg if cfg is not None else load_config()
    raw = c.get("v4_0")
    if not isinstance(raw, dict):
        raise KeyError(
            "config/settings.yaml 에 v4_0 섹션이 없습니다. "
            "strategy / portfolio / costs 블록을 추가하세요."
        )
    return raw


def v4_config_from_yaml_section(v4: dict) -> V4Config:
    strategy = v4.get("strategy")
    portfolio = v4.get("portfolio")
    costs = v4.get("costs")
    if not isinstance(strategy, dict):
        raise KeyError("v4_0.strategy 블록이 없습니다.")
    if not isinstance(portfolio, dict):
        raise KeyError("v4_0.portfolio 블록이 없습니다.")
    if not isinstance(costs, dict):
        raise KeyError("v4_0.costs 블록이 없습니다.")

    missing_s = [k for k in _REQUIRED_STRATEGY_KEYS if k not in strategy]
    if missing_s:
        raise KeyError("v4_0.strategy 필수 키 누락: " + ", ".join(missing_s))
    missing_p = [k for k in _REQUIRED_PORTFOLIO_KEYS if k not in portfolio]
    if missing_p:
        raise KeyError("v4_0.portfolio 필수 키 누락: " + ", ".join(missing_p))
    missing_c = [k for k in _REQUIRED_COSTS_KEYS if k not in costs]
    if missing_c:
        raise KeyError("v4_0.costs 필수 키 누락: " + ", ".join(missing_c))

    deploy = strategy.get("max_daily_cash_deploy_ratio", 0.45)
    return V4Config(
        strategy=V4StrategyConfig(
            nuliim_ratio=float(strategy["nuliim_ratio"]),
            fixed_invest_amount=float(strategy["fixed_invest_amount"]),
            stop_loss_ratio=float(strategy["stop_loss_ratio"]),
            target_profit_ratio=float(strategy["target_profit_ratio"]),
            max_track_days=int(strategy["max_track_days"]),
            max_hold_days=int(strategy["max_hold_days"]),
            min_invest_amount=float(strategy["min_invest_amount"]),
            max_daily_cash_deploy_ratio=float(deploy),
        ),
        portfolio=V4PortfolioConfig(
            initial_cash=float(portfolio["initial_cash"]),
            max_slots=int(portfolio["max_slots"]),
        ),
        costs=V4CostsConfig(
            buy_fee=float(costs["buy_fee"]),
            sell_fee_tax=float(costs["sell_fee_tax"]),
        ),
    )


def load_v4_config(cfg: dict | None = None) -> V4Config:
    """settings.yaml v4_0 마스터만 읽음."""
    return v4_config_from_yaml_section(_v4_yaml_section(cfg))


def v4_config_with_strategy_overrides(
    overrides: dict[str, float | int],
    cfg: dict | None = None,
) -> V4Config:
    """YAML v4_0.strategy 필드만 덮어쓴 V4Config (튜닝·시나리오 실행용)."""
    if not overrides:
        return load_v4_config(cfg)
    v4 = dict(_v4_yaml_section(cfg))
    strategy = dict(v4["strategy"])
    strategy.update(overrides)
    v4["strategy"] = strategy
    return v4_config_from_yaml_section(v4)
