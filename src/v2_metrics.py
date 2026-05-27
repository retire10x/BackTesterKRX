from __future__ import annotations

import pandas as pd


def build_v2_trades_log(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """여러 종목 데이터프레임에서 실제 체결된 거래(buy_signal==1)만 모읍니다."""
    parts: list[pd.DataFrame] = []
    for df in frames:
        if df is None or df.empty or "buy_signal" not in df.columns:
            continue
        mask = df["buy_signal"] == 1
        if not mask.any():
            continue
        parts.append(df.loc[mask].copy())
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=False)


def compute_v2_metrics(trade_returns: pd.Series) -> dict:
    """
    v2.0 성과 지표 산출.

    - Win Rate: (trade_return > 0 건수 / 총 거래) * 100
    - Profit Factor: 총 수익 합 / |총 손실 합|
    - Avg Return: trade_return 평균(%)
    """
    rets = pd.to_numeric(trade_returns, errors="coerce").dropna()
    if rets.empty:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "avg_return_pct": 0.0,
            "type_counts": pd.Series(dtype=int),
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

    type_counts = pd.Series(
        {"WIN": int(len(wins)), "LOSS": int(len(losses))},
        dtype=int,
    )

    return {
        "total_trades": total,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "avg_return_pct": float(rets.mean()) * 100.0,
        "type_counts": type_counts,
    }


def print_v2_dashboard(metrics: dict) -> None:
    """작업지시서 v2.0 표준 PERFORMANCE REPORT 템플릿 출력."""
    total = int(metrics.get("total_trades", 0))
    if total <= 0:
        print("NO TRADES FOUND.")
        return

    win_rate = float(metrics.get("win_rate", 0.0))
    pf = float(metrics.get("profit_factor", 0.0))
    avg_pct = float(metrics.get("avg_return_pct", 0.0))
    type_counts = metrics.get("type_counts")
    type_block = ""
    if isinstance(type_counts, pd.Series) and not type_counts.empty:
        type_block = type_counts.to_string()

    print("\n" + "=" * 45)
    print("       📊 SYSTEM v2.0 PERFORMANCE REPORT      ")
    print("=" * 45)
    print(f" • TOTAL TRADES  : {total} 🚀")
    print(f" • WIN RATE      : {win_rate:.2f} %")
    print(f" • PROFIT FACTOR : {pf:.2f}")
    print(f" • AVG RETURN    : {avg_pct:.2f} %")
    print("-" * 45)
    if type_block:
        print(type_block)
    print("=" * 45)


def run_v2_analytics(frames: list[pd.DataFrame]) -> dict:
    """거래 로그 집계 후 대시보드 출력까지 일괄 수행."""
    log = build_v2_trades_log(frames)
    if log.empty or "trade_return" not in log.columns:
        print_v2_dashboard({"total_trades": 0})
        return {"total_trades": 0}

    metrics = compute_v2_metrics(log["trade_return"])
    print_v2_dashboard(metrics)
    return metrics
