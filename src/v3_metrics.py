from __future__ import annotations

import pandas as pd


def build_v3_trades_log(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """체결된 오버나이트 거래(buy_signal==1, 익일 시가 청산 완료)만 모읍니다."""
    parts: list[pd.DataFrame] = []
    for df in frames:
        if df is None or df.empty or "buy_signal" not in df.columns:
            continue
        rets = pd.to_numeric(df.get("trade_return"), errors="coerce")
        mask = (df["buy_signal"] == 1) & rets.notna()
        if not mask.any():
            continue
        parts.append(df.loc[mask].copy())
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=False)


def compute_v3_metrics(trade_returns: pd.Series) -> dict:
    rets = pd.to_numeric(trade_returns, errors="coerce").dropna()
    if rets.empty:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "avg_return_pct": 0.0,
        }

    total = int(len(rets))
    wins = rets[rets > 0]
    losses = rets[rets <= 0]
    win_rate = len(wins) / total * 100.0
    gross_profit = float(wins.sum())
    gross_loss = float(abs(losses.sum()))
    if gross_loss > 1e-12:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = 999.99
    else:
        profit_factor = 0.0

    return {
        "total_trades": total,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "avg_return_pct": float(rets.mean()) * 100.0,
    }


def print_v3_dashboard(metrics: dict) -> None:
    """v3.0 OVERNIGHT PERFORMANCE REPORT — 터미널 단일 출력."""
    total = int(metrics.get("total_trades", 0))
    if total <= 0:
        print("NO OVERNIGHT TRADES FOUND.")
        return

    win_rate = float(metrics.get("win_rate", 0.0))
    pf = float(metrics.get("profit_factor", 0.0))
    avg_pct = float(metrics.get("avg_return_pct", 0.0))

    print("\n" + "=" * 45)
    print("       📊 SYSTEM v3.0 PERFORMANCE REPORT (OVERNIGHT)      ")
    print("=" * 45)
    print(f" • TOTAL TRADES  : {total} 🚀")
    print(f" • WIN RATE      : {win_rate:.2f} %")
    print(f" • PROFIT FACTOR : {pf:.2f}")
    print(f" • AVG RETURN    : {avg_pct:.2f} %")
    print("=" * 45)


def run_v3_analytics(frames: list[pd.DataFrame]) -> dict:
    log = build_v3_trades_log(frames)
    if log.empty or "trade_return" not in log.columns:
        print_v3_dashboard({"total_trades": 0})
        return {"total_trades": 0}

    metrics = compute_v3_metrics(log["trade_return"])
    print_v3_dashboard(metrics)
    return metrics
