import numpy as np
import pandas as pd

TRADING_COST = 0.00215
PROFIT_TARGET_PCT = 0.035
MAX_HOLD_DAYS = 3
STAGE_ALLOCATIONS = {1: 0.50, 2: 0.30, 3: 0.20, 4: 0.10}
TRACKED_EXPIRE_BDAYS = 30
MAX_DAILY_CASH_DEPLOY_RATIO = 0.45


def compute_stage_invest_amount(
    *,
    total_equity: float,
    max_slots: int,
    stage: int,
    cash: float,
    available_slots: int,
    max_daily_remaining_cash: float | None = None,
) -> float:
    """회차별 투입 금액 — equity 슬롯 예산과 가용 현금·당일 배분 상한을 동시에 적용."""
    if available_slots <= 0 or cash <= 0 or max_slots <= 0:
        return 0.0
    slot_budget = total_equity / max_slots
    alloc_ratio = STAGE_ALLOCATIONS.get(int(stage), 0.0)
    if alloc_ratio <= 0:
        return 0.0
    invest = slot_budget * alloc_ratio
    invest = min(invest, cash / float(available_slots))
    if max_daily_remaining_cash is not None:
        invest = min(invest, max(0.0, max_daily_remaining_cash))
    return max(0.0, invest)


_OHLCV_COLS = ("open", "high", "low", "close", "volume")


def _normalize_ohlcv_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename = {
        col: str(col).lower()
        for col in df.columns
        if str(col).lower() in _OHLCV_COLS
    }
    return df.rename(columns=rename)


def scan_smart_money_universe(df_market_today):
    """
    [Pass 0 ~ 1] 스마트머니 절대 자금 장벽 필터
    df_market_today: 당일 전 종목의 [code, name, volume, close] 정보를 담은 데이터프레임
    """
    df = df_market_today.copy()
    df = _normalize_ohlcv_columns(df)
    if "code" not in df.columns:
        df = df.reset_index()
        if "index" in df.columns and "code" not in df.columns:
            df = df.rename(columns={"index": "code"})
    df["close"] = pd.to_numeric(df["close"], errors="coerce").astype("float64")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").astype("float64")
    df["trading_value"] = df["close"] * df["volume"]

    min_value_barrier = 150_000_000_000
    cond_value = df["trading_value"] >= min_value_barrier

    df["value_rank"] = df["trading_value"].rank(ascending=False, method="min")
    cond_rank = df["value_rank"] <= 20

    universe = df[cond_value & cond_rank]
    return universe["code"].astype(str).str.zfill(6).tolist()


# Phase I: 코스닥 중소형 탄력주(시총 700억~5,000억) + 기준봉 거래대금 폭발
PHASE_I_MIN_MCAP_KRW = 70_000_000_000
PHASE_I_MAX_MCAP_KRW = 500_000_000_000
PHASE_I_MIN_ANCHOR_TRADE_KRW = 3_000_000_000  # 기준봉 최소 거래대금 30억
PHASE_I_ANCHOR_TOP_N = 30
PHASE_I_VOLUME_DRY_RATIO = 0.15


def scan_phase_i_kosdaq_universe(
    df_market_today: pd.DataFrame,
    marcap_by_code: dict[str, float],
    kosdaq_codes: frozenset[str],
    *,
    min_mcap: float = PHASE_I_MIN_MCAP_KRW,
    max_mcap: float = PHASE_I_MAX_MCAP_KRW,
    min_anchor_trade_krw: float = PHASE_I_MIN_ANCHOR_TRADE_KRW,
    top_n: int = PHASE_I_ANCHOR_TOP_N,
) -> list[str]:
    """
    [Phase I] 코스닥 전용 — 시총 밴드 내 당일 거래대금 상위(주포 유입) 종목만 기준봉 후보.
    """
    df = df_market_today.copy()
    df = _normalize_ohlcv_columns(df)
    if "code" not in df.columns:
        df = df.reset_index()
        if "index" in df.columns and "code" not in df.columns:
            df = df.rename(columns={"index": "code"})
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["close"] = pd.to_numeric(df["close"], errors="coerce").astype("float64")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").astype("float64")
    df["trading_value"] = df["close"] * df["volume"]

    rows: list[dict[str, object]] = []
    for _, row in df.iterrows():
        c6 = str(row["code"]).zfill(6)
        if c6 not in kosdaq_codes:
            continue
        cap = marcap_by_code.get(c6)
        if cap is None or not np.isfinite(cap):
            continue
        if cap < min_mcap or cap > max_mcap:
            continue
        tv = float(row["trading_value"])
        if not np.isfinite(tv) or tv < min_anchor_trade_krw:
            continue
        rows.append({"code": c6, "trading_value": tv})

    if not rows:
        return []
    ranked = pd.DataFrame(rows).sort_values("trading_value", ascending=False).head(int(top_n))
    return ranked["code"].astype(str).str.zfill(6).tolist()


def _stage_entry_triggered(df_stock, idx, stage):
    row = df_stock.iloc[idx]
    prev_row = df_stock.iloc[idx - 1]

    if stage == 1:
        return row["close"] <= row["MA3"]
    if stage == 2:
        return row["close"] <= row["MA5"] and row["volume"] <= prev_row["volume"] * 0.70
    if stage == 3:
        return row["close"] <= row["MA10"] and row["volume"] <= prev_row["volume"] * 0.50
    if stage == 4:
        recent_vol_avg = df_stock["volume"].iloc[max(0, idx - 5):idx].mean()
        return row["close"] <= row["MA20"] and row["volume"] <= recent_vol_avg * 0.30
    return False


def stage_entry_triggered(df_stock, stage):
    """당일(마지막 봉) 기준 회차별 진입 조건."""
    work = _normalize_ohlcv_columns(df_stock.copy())
    work["MA3"] = work["close"].rolling(window=3).mean()
    work["MA5"] = work["close"].rolling(window=5).mean()
    work["MA10"] = work["close"].rolling(window=10).mean()
    work["MA20"] = work["close"].rolling(window=20).mean()
    idx = len(work) - 1
    if idx <= 0:
        return False
    return _stage_entry_triggered(work, idx, stage)


def evaluate_daily_exit(high, close, entry_price, hold_days):
    """보유 n일차 장중 고가·종가 기준 청산 판정. (exit_price, pnl_rate) 또는 None."""
    target_profit_price = entry_price * (1 + PROFIT_TARGET_PCT)
    if high >= target_profit_price:
        return target_profit_price, PROFIT_TARGET_PCT - TRADING_COST, "익절"
    if hold_days >= MAX_HOLD_DAYS:
        pnl_rate = (close / entry_price) - 1 - TRADING_COST
        exit_type = "타임스탑" if pnl_rate >= 0 else "손절"
        return close, pnl_rate, exit_type
    return None


def _resolve_exit(df_stock, entry_idx, entry_price):
    target_profit_price = entry_price * (1 + PROFIT_TARGET_PCT)

    for hold_days, t_idx in enumerate(range(entry_idx + 1, min(entry_idx + 4, len(df_stock))), start=1):
        t_row = df_stock.iloc[t_idx]

        if t_row["high"] >= target_profit_price:
            pnl = PROFIT_TARGET_PCT - TRADING_COST
            return {
                "exit_idx": t_idx,
                "exit_date": df_stock.index[t_idx],
                "pnl": pnl,
                "type": "익절 🟢",
            }

        if hold_days == MAX_HOLD_DAYS:
            exit_price = t_row["close"]
            pnl = (exit_price / entry_price) - 1 - TRADING_COST
            exit_type = "타임스탑 ⚪" if pnl >= 0 else "손절 🚨"
            return {
                "exit_idx": t_idx,
                "exit_date": df_stock.index[t_idx],
                "pnl": pnl,
                "type": exit_type,
            }

    return None


def calculate_cascade_backtest(df_stock, start_idx):
    """
    [Pass 2 ~ 3] N회차 연쇄 종가 매수 및 3영업일 타임스탑 청산 시뮬레이터
    df_stock: 단일 주도주 일봉 (index=Date, columns=[open, high, low, close, volume])
    start_idx: 스마트머니 유입 기준봉 인덱스
    """
    df_stock = _normalize_ohlcv_columns(df_stock.copy())
    df_stock["MA3"] = df_stock["close"].rolling(window=3).mean()
    df_stock["MA5"] = df_stock["close"].rolling(window=5).mean()
    df_stock["MA10"] = df_stock["close"].rolling(window=10).mean()
    df_stock["MA20"] = df_stock["close"].rolling(window=20).mean()

    trade_logs = []
    current_stage = 1
    idx = start_idx + 1
    open_entry_idx = None
    open_entry_price = None
    open_entry_date = None

    while idx < len(df_stock) and current_stage <= 4:
        if open_entry_idx is not None:
            exit_info = _resolve_exit(df_stock, open_entry_idx, open_entry_price)
            if exit_info is None:
                break

            trade_logs.append({
                "stage": f"{current_stage}회차",
                "entry_date": open_entry_date,
                "exit_date": exit_info["exit_date"],
                "allocation": STAGE_ALLOCATIONS[current_stage],
                "pnl": exit_info["pnl"],
                "type": exit_info["type"],
            })

            current_stage += 1
            open_entry_idx = None
            open_entry_price = None
            open_entry_date = None
            idx = exit_info["exit_idx"] + 1
            continue

        if idx <= 0:
            idx += 1
            continue

        if _stage_entry_triggered(df_stock, idx, current_stage):
            open_entry_idx = idx
            open_entry_price = float(df_stock.iloc[idx]["close"])
            open_entry_date = df_stock.index[idx]
            idx += 1
            continue

        idx += 1

    return pd.DataFrame(trade_logs)
