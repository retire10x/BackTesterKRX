import pandas as pd

TRADING_COST = 0.00215
PROFIT_TARGET_PCT = 0.035
MAX_HOLD_DAYS = 3
STAGE_ALLOCATIONS = {1: 0.50, 2: 0.30, 3: 0.20, 4: 0.10}
_OHLCV_COLS = ("open", "high", "low", "close", "volume")


def _normalize_ohlcv_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename = {
        col: str(col).lower()
        for col in df.columns
        if str(col).lower() in _OHLCV_COLS
    }
    return df.rename(columns=rename)
PROFIT_TARGET_PCT = 0.035
MAX_HOLD_DAYS = 3
STAGE_ALLOCATIONS = {1: 0.50, 2: 0.30, 3: 0.20, 4: 0.10}


def scan_smart_money_universe(df_market_today):
    """
    [Pass 0 ~ 1] 스마트머니 절대 자금 장벽 필터
    df_market_today: 당일 전 종목의 [code, name, volume, close] 정보를 담은 데이터프레임
    """
    df = df_market_today.copy()
    df = _normalize_ohlcv_columns(df)
    df["trading_value"] = df["close"] * df["volume"]

    min_value_barrier = 150_000_000_000
    cond_value = df["trading_value"] >= min_value_barrier

    df["value_rank"] = df["trading_value"].rank(ascending=False, method="min")
    cond_rank = df["value_rank"] <= 20

    universe = df[cond_value & cond_rank]
    return universe["code"].tolist()


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
