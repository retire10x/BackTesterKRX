"""
v5.0 20일선 변곡점 스나이퍼 — SSOT.

유일한 숫자 기본값: config/settings.yaml 의 v5_0 섹션.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.data_loader import load_config


@dataclass(frozen=True)
class V5EnvironmentConfig:
    mode: str
    initial_cash: float


@dataclass(frozen=True)
class V5TradingCostsConfig:
    buy_cost_ratio: float
    sell_cost_ratio: float


@dataclass(frozen=True)
class V5PortfolioConfig:
    max_slots: int
    slot_invest_amount: float
    trading_costs: V5TradingCostsConfig


@dataclass(frozen=True)
class V5StrategyConfig:
    strategy_name: str
    lookback_window: int
    exit_ma_window: int
    price_ceiling: float
    price_floor: float


@dataclass(frozen=True)
class V5Config:
    environment: V5EnvironmentConfig
    portfolio: V5PortfolioConfig
    strategy: V5StrategyConfig


def _v5_yaml_section(cfg: dict | None = None) -> dict:
    c = cfg if cfg is not None else load_config()
    raw = c.get("v5_0")
    if not isinstance(raw, dict):
        raise KeyError(
            "config/settings.yaml 에 v5_0 섹션이 없습니다. "
            "environment / portfolio / strategy 블록을 추가하세요."
        )
    return raw


def v5_config_from_yaml_section(v5: dict) -> V5Config:
    environment = v5.get("environment")
    portfolio = v5.get("portfolio")
    strategy = v5.get("strategy")
    if not isinstance(environment, dict):
        raise KeyError("v5_0.environment 블록이 없습니다.")
    if not isinstance(portfolio, dict):
        raise KeyError("v5_0.portfolio 블록이 없습니다.")
    if not isinstance(strategy, dict):
        raise KeyError("v5_0.strategy 블록이 없습니다.")

    costs_raw = portfolio.get("trading_costs")
    if not isinstance(costs_raw, dict):
        raise KeyError("v5_0.portfolio.trading_costs 블록이 없습니다.")
    for key in ("buy_cost_ratio", "sell_cost_ratio"):
        if key not in costs_raw:
            raise KeyError(f"v5_0.portfolio.trading_costs.{key} 누락")

    if "max_slots" not in portfolio:
        raise KeyError("v5_0.portfolio.max_slots 누락")
    if "initial_cash" not in environment:
        raise KeyError("v5_0.environment.initial_cash 누락")

    required_s = (
        "strategy_name",
        "lookback_window",
        "price_ceiling",
        "price_floor",
    )
    missing_s = [k for k in required_s if k not in strategy]
    if missing_s:
        raise KeyError("v5_0.strategy 필수 키 누락: " + ", ".join(missing_s))

    slot_invest = float(
        portfolio.get(
            "slot_invest_amount",
            environment["initial_cash"] / max(int(portfolio["max_slots"]), 1),
        )
    )

    return V5Config(
        environment=V5EnvironmentConfig(
            mode=str(environment.get("mode", "standard")).strip().lower(),
            initial_cash=float(environment["initial_cash"]),
        ),
        portfolio=V5PortfolioConfig(
            max_slots=int(portfolio["max_slots"]),
            slot_invest_amount=slot_invest,
            trading_costs=V5TradingCostsConfig(
                buy_cost_ratio=float(costs_raw["buy_cost_ratio"]),
                sell_cost_ratio=float(costs_raw["sell_cost_ratio"]),
            ),
        ),
        strategy=V5StrategyConfig(
            strategy_name=str(strategy["strategy_name"]),
            lookback_window=int(strategy["lookback_window"]),
            exit_ma_window=int(strategy.get("exit_ma_window", strategy["lookback_window"])),
            price_ceiling=float(strategy["price_ceiling"]),
            price_floor=float(strategy["price_floor"]),
        ),
    )


def load_v5_config(cfg: dict | None = None) -> V5Config:
    return v5_config_from_yaml_section(_v5_yaml_section(cfg))
