"""
실전 매매 SSOT — config/live_settings.yaml
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from src.data_loader import load_config

DEFAULT_LIVE_SETTINGS_REL = "config/live_settings.yaml"


@dataclass(frozen=True)
class LiveAccountConfig:
    broker: str
    mode: str
    bet_amount_per_slot: float
    min_slots_limit: int
    max_slots_limit: int
    minimum_operational_capital: float
    buy_cost_ratio: float
    sell_cost_ratio: float


@dataclass(frozen=True)
class LiveScreenerConfig:
    market: str
    min_market_cap: float
    max_market_cap: float
    min_live_volume_amt: float
    top_n: int
    screener_time: str
    universe_output: str


@dataclass(frozen=True)
class LiveMacroFilterConfig:
    ma_lines: tuple[int, ...]
    price_above_ma: int


@dataclass(frozen=True)
class LiveStrategyConfig:
    lookback_window: int
    target_profit_ratio: float
    stop_loss_ratio: float
    max_hold_days: int
    entry_time: str
    macro_filter: LiveMacroFilterConfig


@dataclass(frozen=True)
class LiveWatchConfig:
    poll_interval_sec: float
    market_open: str
    market_close: str


@dataclass(frozen=True)
class LiveTradingConfig:
    account: LiveAccountConfig
    screener: LiveScreenerConfig
    strategy: LiveStrategyConfig
    watch: LiveWatchConfig


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_live_settings_yaml(
    path: str | None = None,
    *,
    cfg: dict | None = None,
) -> dict:
    if cfg is not None:
        raw = cfg.get("live_trading")
        if isinstance(raw, dict):
            return raw
    p = path or str(_project_root() / DEFAULT_LIVE_SETTINGS_REL)
    with open(p, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    raw = doc.get("live_trading")
    if not isinstance(raw, dict):
        raise KeyError(f"{p} 에 live_trading 블록이 없습니다.")
    return raw


def live_config_from_dict(raw: dict) -> LiveTradingConfig:
    acct = raw.get("account")
    scr = raw.get("screener")
    strat = raw.get("strategy")
    watch = raw.get("watch")
    if not all(isinstance(x, dict) for x in (acct, scr, strat, watch)):
        raise KeyError("live_trading: account / screener / strategy / watch 필수")

    macro_raw = strat.get("macro_filter") or {}
    ma_lines_raw = macro_raw.get("ma_lines", [60, 120])
    ma_lines = tuple(int(x) for x in ma_lines_raw)
    price_above = int(macro_raw.get("price_above_ma", max(ma_lines) if ma_lines else 120))

    return LiveTradingConfig(
        account=LiveAccountConfig(
            broker=str(acct.get("broker", "korea_investment")),
            mode=str(acct.get("mode", "real_money")),
            bet_amount_per_slot=float(acct["bet_amount_per_slot"]),
            min_slots_limit=int(acct.get("min_slots_limit", 1)),
            max_slots_limit=int(acct.get("max_slots_limit", acct.get("max_slots", 5))),
            minimum_operational_capital=float(acct.get("minimum_operational_capital", 50000)),
            buy_cost_ratio=float(acct.get("buy_cost_ratio", 0.00015)),
            sell_cost_ratio=float(acct.get("sell_cost_ratio", 0.00195)),
        ),
        screener=LiveScreenerConfig(
            market=str(scr.get("market", "KOSDAQ")).upper(),
            min_market_cap=float(scr["min_market_cap"]),
            max_market_cap=float(scr["max_market_cap"]),
            min_live_volume_amt=float(scr["min_live_volume_amt"]),
            top_n=int(scr["top_n"]),
            screener_time=str(scr.get("screener_time", "15:15")),
            universe_output=str(scr.get("universe_output", "config/live_today_universe.json")),
        ),
        strategy=LiveStrategyConfig(
            lookback_window=int(strat.get("lookback_window", 20)),
            target_profit_ratio=float(strat["target_profit_ratio"]),
            stop_loss_ratio=float(strat["stop_loss_ratio"]),
            max_hold_days=int(strat["max_hold_days"]),
            entry_time=str(strat.get("entry_time", "15:20")),
            macro_filter=LiveMacroFilterConfig(ma_lines=ma_lines, price_above_ma=price_above),
        ),
        watch=LiveWatchConfig(
            poll_interval_sec=float(watch.get("poll_interval_sec", 0.5)),
            market_open=str(watch.get("market_open", "09:00")),
            market_close=str(watch.get("market_close", "15:30")),
        ),
    )


def load_live_config(
    path: str | None = None,
    *,
    cfg: dict | None = None,
) -> LiveTradingConfig:
    return live_config_from_dict(load_live_settings_yaml(path, cfg=cfg))


def resolve_live_paths(cfg: LiveTradingConfig, project_root: str | None = None) -> dict[str, str]:
    root = Path(project_root or _project_root())
    uni_rel = cfg.screener.universe_output
    uni_path = Path(uni_rel) if Path(uni_rel).is_absolute() else root / uni_rel
    meta_path = uni_path.with_suffix(".meta.json")
    return {
        "universe_json": str(uni_path),
        "universe_meta": str(meta_path),
        "positions_json": str(root / "config" / "live_positions.json"),
        "db_path": str(root / "data" / "live_trading.db"),
    }
