"""
v5.x 20일선 변곡점 스나이퍼 — SSOT.

유일한 숫자 기본값: config/settings.yaml 의 v5_0 / v5_1 / v5_2 / v5_3 / v5_4 섹션.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.data_loader import load_config

DEFAULT_V5_SECTION = "v5_2"


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
class V5ScreenerConfig:
    market: str
    min_mcap_krw: float
    max_mcap_krw: float
    min_trade_krw: float
    top_n: int


@dataclass(frozen=True)
class V5EnvironmentConfig:
    mode: str
    initial_cash: float
    universe_profile: str | None = None
    universe_lock: V5UniverseLockConfig | None = None
    relay_interval_months: int | None = None
    universe_dir: str | None = None


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
class V5MacroTrendFilterConfig:
    enabled: bool
    ma_window: int


@dataclass(frozen=True)
class V5StrategyConfig:
    strategy_name: str
    lookback_window: int
    exit_ma_window: int | None
    price_ceiling: float | None
    price_floor: float | None
    stop_loss_ratio: float | None = None
    target_profit_ratio: float | None = None
    max_hold_days: int | None = None
    macro_trend_filter: V5MacroTrendFilterConfig | None = None

    @property
    def use_hit_and_run_exit(self) -> bool:
        return (
            self.stop_loss_ratio is not None
            and self.target_profit_ratio is not None
            and self.max_hold_days is not None
        )


@dataclass(frozen=True)
class V5Config:
    section: str
    environment: V5EnvironmentConfig
    portfolio: V5PortfolioConfig
    strategy: V5StrategyConfig
    screener: V5ScreenerConfig | None = None


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

    screener_raw = strategy.get("screener")
    screener: V5ScreenerConfig | None = None
    if isinstance(screener_raw, dict):
        screener = V5ScreenerConfig(
            market=str(screener_raw.get("market", "KOSDAQ")).strip().upper(),
            min_mcap_krw=float(
                screener_raw.get("min_mcap_krw", screener_raw.get("min_market_cap", 0))
            ),
            max_mcap_krw=float(
                screener_raw.get("max_mcap_krw", screener_raw.get("max_market_cap", 0))
            ),
            min_trade_krw=float(
                screener_raw.get("min_trade_krw", screener_raw.get("min_daily_volume_amt", 0))
            ),
            top_n=int(screener_raw.get("top_n", screener_raw.get("top_n_limit", 40))),
        )

    macro_raw = strategy.get("macro_trend_filter")
    macro_trend_filter: V5MacroTrendFilterConfig | None = None
    if isinstance(macro_raw, dict):
        macro_trend_filter = V5MacroTrendFilterConfig(
            enabled=bool(macro_raw.get("enabled", False)),
            ma_window=int(macro_raw.get("ma_window", 60)),
        )

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
            relay_interval_months=(
                int(environment["relay_interval_months"])
                if environment.get("relay_interval_months") is not None
                else None
            ),
            universe_dir=(
                str(environment["universe_dir"]).strip()
                if environment.get("universe_dir")
                else None
            ),
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
            exit_ma_window=(
                int(strategy["exit_ma_window"])
                if strategy.get("exit_ma_window") is not None
                else None
            ),
            price_ceiling=float(price_ceiling) if price_ceiling is not None else None,
            price_floor=float(price_floor) if price_floor is not None else None,
            stop_loss_ratio=(
                float(strategy["stop_loss_ratio"])
                if strategy.get("stop_loss_ratio") is not None
                else None
            ),
            target_profit_ratio=(
                float(strategy["target_profit_ratio"])
                if strategy.get("target_profit_ratio") is not None
                else None
            ),
            max_hold_days=(
                int(strategy["max_hold_days"])
                if strategy.get("max_hold_days") is not None
                else None
            ),
            macro_trend_filter=macro_trend_filter,
        ),
        screener=screener,
    )


V53_SECTION = "v5_3"
V54_SECTION = "v5_4"
DEFAULT_V5_RELAY_SECTION = V54_SECTION


def load_v5_config(
    cfg: dict | None = None,
    *,
    section: str = DEFAULT_V5_SECTION,
) -> V5Config:
    return v5_config_from_yaml_section(_v5_yaml_section(cfg, section=section), section=section)


def load_v5_relay_config(
    cfg: dict | None = None,
    *,
    section: str = DEFAULT_V5_RELAY_SECTION,
) -> V5Config:
    """v5.3/v5.4 릴레이 SSOT — screener·universe_dir 필수."""
    v5 = load_v5_config(cfg=cfg, section=section)
    if v5.screener is None:
        raise KeyError(f"{section}.strategy.screener 블록이 필요합니다.")
    if not v5.environment.universe_dir:
        raise KeyError(f"{section}.environment.universe_dir 이 필요합니다.")
    return v5


UNIVERSE_LOCK_FALLBACK_SECTION = "v5_1"


def get_effective_universe_lock(
    v5: V5Config,
    *,
    cfg: dict | None = None,
    fallback_section: str = UNIVERSE_LOCK_FALLBACK_SECTION,
) -> V5UniverseLockConfig | None:
    """현재 섹션 lock 없으면 v5_1 등 폴백 섹션 lock 반환."""
    if v5.environment.universe_lock is not None:
        return v5.environment.universe_lock
    fb = load_v5_config(cfg=cfg, section=fallback_section)
    return fb.environment.universe_lock


def v5_config_for_universe_scan(
    v5: V5Config,
    *,
    cfg: dict | None = None,
    fallback_section: str = UNIVERSE_LOCK_FALLBACK_SECTION,
) -> V5Config:
    """유니버스 박제 스캔용 — universe_lock 이 없으면 폴백 섹션 lock 을 주입."""
    lock = get_effective_universe_lock(v5, cfg=cfg, fallback_section=fallback_section)
    if lock is None:
        raise KeyError(
            f"스캔하려면 {v5.section} 또는 {fallback_section}.environment.universe_lock "
            "설정이 필요합니다."
        )
    if v5.environment.universe_lock is not None:
        return v5
    return V5Config(
        section=v5.section,
        environment=V5EnvironmentConfig(
            mode=v5.environment.mode,
            initial_cash=v5.environment.initial_cash,
            universe_profile=v5.environment.universe_profile,
            universe_lock=lock,
        ),
        portfolio=v5.portfolio,
        strategy=v5.strategy,
    )
