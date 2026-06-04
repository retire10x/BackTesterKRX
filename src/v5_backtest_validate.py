"""v5 릴레이 백테스트 DoD 검증 (구 run_v4_portfolio 에서 이관)."""
from __future__ import annotations

import pandas as pd


def validate_phase_a_trades(trades_detail, trades_sell) -> dict[str, object]:
    """Phase A DoD: SELL 건수 일치, cash_after>=0, PnL 관계식 샘플 검증."""
    issues: list[str] = []
    if trades_detail is None or trades_detail.empty:
        issues.append("trades_detail 비어 있음")
        return {"ok": False, "issues": issues}

    sells = trades_detail[trades_detail["side"] == "SELL"]
    buys = trades_detail[trades_detail["side"] == "BUY"]
    sell_count = int(len(sells))
    buy_count = int(len(buys))
    metrics_sell_count = int(len(trades_sell)) if trades_sell is not None else 0

    if sell_count != metrics_sell_count:
        issues.append(
            f"SELL 건수 불일치: detail={sell_count}, metrics={metrics_sell_count}"
        )

    neg_cash = trades_detail[trades_detail["cash_after"] < -1e-6]
    if len(neg_cash) > 0:
        issues.append(f"cash_after 음수 {len(neg_cash)}건 (min={neg_cash['cash_after'].min():,.0f})")

    sample_n = min(10, sell_count)
    pnl_mismatch = 0
    if sample_n > 0:
        sample = sells.head(sample_n)
        for _, row in sample.iterrows():
            inv = float(row["invest_amount"])
            proc = float(row["proceeds"])
            pnl = float(row["pnl_amount"])
            if abs((proc - inv) - pnl) > 0.01:
                pnl_mismatch += 1
    if pnl_mismatch > 0:
        issues.append(f"PnL 관계식 불일치 샘플 {pnl_mismatch}/{sample_n}건")

    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "neg_cash_count": int(len(neg_cash)),
    }
