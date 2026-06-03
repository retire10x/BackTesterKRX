"""
v5.x 20일선 변곡점 스나이퍼 — SSOT.

유일한 숫자 기본값: config/settings.yaml 의 v5_0 / v5_1 섹션.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.data_loader import load_config

DEFAULT_V5_SECTION = "v5_1"


@dataclass(frozen=True)
class V5UniverseLockConfig:
    lock_date: str
    backtest_start: str
    market: str
    min_mcap_krw: float
    max_mcap_krw: float
    top_n: int
    min_trade_krw: float


@dataclass(frozen=True)
class V5EnvironmentConfig:
    mode: str
    initial_cash: float
    universe_profile: str | None = None
    universe_lock: V5UniverseLockConfig | None = None


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
    price_ceiling: float | None
    price_floor: float | None


@dataclass(frozen=True)
class V5Config:
    section: str
    environment: V5EnvironmentConfig
    portfolio: V5PortfolioConfig
    strategy: V5StrategyConfig


def _v5_yaml_section(cfg: dict | None = None, *, section: str = DEFAULT_V5_SECTION) -> dict:
    c = cfg if cfg is not None else load_config()
    raw = c.get(section)
    if not isinstance(raw, dict):
        raise KeyError(
            f"config/settings.yaml 에 {section} 섹션이 없습니다. "
            "environment / portfolio / strategy 블록을 추가하세요."
        )
    return raw


def v5_config_from_yaml_section(v5: dict, *, section: str = DEFAULT_V5_SECTION) -> V5Config:
    environment = v5.get("environment")
    portfolio = v5.get("portfolio")
    strategy = v5.get("strategy")
    if not isinstance(environment, dict):
        raise KeyError(f"{section}.environment 블록이 없습니다.")
    if not isinstance(portfolio, dict):
        raise KeyError(f"{section}.portfolio 블록이 없습니다.")
    if not isinstance(strategy, dict):
        raise KeyError(f"{section}.strategy 블록이 없습니다.")

    costs_raw = portfolio.get("trading_costs")
    if not isinstance(costs_raw, dict):
        raise KeyError(f"{section}.portfolio.trading_costs 블록이 없습니다.")
    for key in ("buy_cost_ratio", "sell_cost_ratio"):
        if key not in costs_raw:
            raise KeyError(f"{section}.portfolio.trading_costs.{key} 누락")

    if "max_slots" not in portfolio:
        raise KeyError(f"{section}.portfolio.max_slots 누락")
    if "initial_cash" not in environment:
        raise KeyError(f"{section}.environment.initial_cash 누락")

    required_s = ("strategy_name", "lookback_window")
    missing_s = [k for k in required_s if k not in strategy]
    if missing_s:
        raise KeyError(f"{section}.strategy 필수 키 누락: " + ", ".join(missing_s))

    slot_invest = float(
        portfolio.get(
            "slot_invest_amount",
            environment["initial_cash"] / max(int(portfolio["max_slots"]), 1),
        )
    )

    price_ceiling = strategy.get("price_ceiling")
    price_floor = strategy.get("price_floor")

    lock_raw = environment.get("universe_lock")
    universe_lock: V5UniverseLockConfig | None = None
    if isinstance(lock_raw, dict):
        required_lock = (
            "lock_date",
            "backtest_start",
            "min_mcap_krw",
            "max_mcap_krw",
            "top_n",
        )
        missing_lock = [k for k in required_lock if k not in lock_raw]
        if missing_lock:
            raise KeyError(f"{section}.environment.universe_lock 필수 키 누락: " + ", ".join(missing_lock))
        universe_lock = V5UniverseLockConfig(
            lock_date=str(lock_raw["lock_date"]).strip()[:10],
            backtest_start=str(lock_raw["backtest_start"]).strip()[:10],
            market=str(lock_raw.get("market", "KOSDAQ")).strip().upper(),
            min_mcap_krw=float(lock_raw["min_mcap_krw"]),
            max_mcap_krw=float(lock_raw["max_mcap_krw"]),
            top_n=int(lock_raw["top_n"]),
            min_trade_krw=float(lock_raw.get("min_trade_krw", 0)),
        )

    return V5Config(
        section=section,
        environment=V5EnvironmentConfig(
            mode=str(environment.get("mode", "standard")).strip().lower(),
            initial_cash=float(environment["initial_cash"]),
            universe_profile=(
                str(environment["universe_profile"]).strip()
                if environment.get("universe_profile")
                else None
            ),
            universe_lock=universe_lock,
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
            price_ceiling=float(price_ceiling) if price_ceiling is not None else None,
            price_floor=float(price_floor) if price_floor is not None else None,
        ),
    )


def load_v5_config(
    cfg: dict | None = None,
    *,
    section: str = DEFAULT_V5_SECTION,
) -> V5Config:
    return v5_config_from_yaml_section(_v5_yaml_section(cfg, section=section), section=section)
