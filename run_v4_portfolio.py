import os
import sys

import pandas as pd

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def _load_env_file(path: str) -> None:
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except Exception:
        return
    for raw in lines:
        s = str(raw).strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        key = k.strip()
        val = v.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        if key and key not in os.environ:
            os.environ[key] = val


# .env → os.environ 주입 (KRX_ID/KRX_PW 등)
_load_env_file(os.path.join(project_root, ".env"))

from src.engine.portfolio_manager import PortfolioManager, load_merged_market_day_frames
from src.v4_config import load_v4_config
START_DATE = "2023-01-01"
END_DATE = "2026-05-31"
EQUITY_CSV = os.path.join(project_root, "outputs", "v4_equity_curve.csv")
TRADES_CSV = os.path.join(project_root, "outputs", "v4_trades.csv")
PASS_LOG_TXT = os.path.join(project_root, "outputs", "v4_pass_log.txt")


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


def validate_phase_g_dod(
    manager,
    trades_detail: pd.DataFrame,
    *,
    fixed_invest: float,
) -> dict[str, object]:
    """Phase G DoD: 기준봉 당일 매수 0건, 단리 slot_budget, 손절 우선."""
    issues: list[str] = []
    if getattr(manager, "phase_g_same_day_entries", 0) > 0:
        issues.append(f"기준봉 당일 진입 시도 {manager.phase_g_same_day_entries}건")

    if trades_detail is not None and not trades_detail.empty:
        buys = trades_detail[trades_detail["side"] == "BUY"]
        if not buys.empty and "slot_budget_at_entry" in buys.columns:
            sb = pd.to_numeric(buys["slot_budget_at_entry"], errors="coerce")
            if (sb - fixed_invest).abs().max() > 1.0:
                issues.append(
                    f"slot_budget_at_entry가 고정 {fixed_invest:,.0f}원과 불일치 "
                    f"(max차={((sb - fixed_invest).abs().max()):,.0f})"
                )
        sells = trades_detail[trades_detail["side"] == "SELL"]
        if not sells.empty and "exit_type" in sells.columns:
            stop_n = int(sells["exit_type"].astype(str).str.contains("STOP", case=False).sum())
            if stop_n == 0:
                issues.append("STOP_LOSS 청산 0건 — 하드 손절 미작동 가능")

    return {"ok": len(issues) == 0, "issues": issues}


def run_v4_portfolio_backtest():
    v4 = load_v4_config()
    s = v4.strategy
    p = v4.portfolio
    phase_mode = str(v4.engine.phase_mode).strip().lower()
    is_phase_h = phase_mode == "h"
    print(
        f"🚀 v4.0 포트폴리오 백테스트 (Phase {phase_mode.upper()} · settings.yaml SSOT · 동결) — 벌크 로딩 중..."
    )
    if is_phase_h:
        bet = s.field_test_invest_amount if v4.environment_mode == "field_test" else s.fixed_invest_amount
        print(
            f"   field_test={v4.environment_mode == 'field_test'} · 베팅 {bet:,.0f}원/슬롯 · "
            f"SL -{s.stop_loss_ratio:.0%} · TP +{s.target_profit_ratio:.0%} · "
            f"황제주 {s.emperor_cap_ratio:.0%} · {s.max_hold_days}일 타임스탑 · "
            f"관망 {s.phase_h_min_wait_bdays}영업일"
        )
    else:
        print(
            f"   눌림 {s.nuliim_ratio:.0%} · 단리 {s.fixed_invest_amount:,.0f}원/슬롯 · "
            f"손절 -{s.stop_loss_ratio:.0%} · 익절 +{s.target_profit_ratio:.1%} · "
            f"{s.max_hold_days}일 타임스탑"
        )
    day_frames, bdays = load_merged_market_day_frames(START_DATE, END_DATE, force_bulk=True)
    print(f"📊 벌크 로드 완료: {len(day_frames)} 영업일 × KOSPI+KOSDAQ")

    manager = PortfolioManager(
        day_frames,
        bdays,
        start_date=START_DATE,
        end_date=END_DATE,
        phase_g_mode=(phase_mode == "g"),
        phase_h_mode=is_phase_h,
        v4_config=v4,
    )
    result = manager.run()

    out_dir = os.path.dirname(EQUITY_CSV)
    os.makedirs(out_dir, exist_ok=True)
    result.equity_curve.to_csv(EQUITY_CSV, index=False, encoding="utf-8-sig")
    result.trades_detail.to_csv(TRADES_CSV, index=False, encoding="utf-8-sig")
    if result.pass_logs:
        with open(PASS_LOG_TXT, "w", encoding="utf-8") as fh:
            fh.write("\n".join(result.pass_logs))

    phase_a = validate_phase_a_trades(result.trades_detail, result.trades)
    fixed_for_dod = (
        s.field_test_invest_amount if v4.environment_mode == "field_test" else s.fixed_invest_amount
    )
    phase_g = validate_phase_g_dod(
        manager,
        result.trades_detail,
        fixed_invest=fixed_for_dod,
    ) if not is_phase_h else {"ok": True, "issues": []}
    buy_count = int((result.trades_detail["side"] == "BUY").sum()) if not result.trades_detail.empty else 0
    sell_count = int((result.trades_detail["side"] == "SELL").sum()) if not result.trades_detail.empty else 0

    m = result.metrics
    print("\n========================================================")
    print("📈 v4.0 스마트머니 포트폴리오 최종 성적표")
    print("========================================================")
    print(f"기간              : {START_DATE} ~ {END_DATE}")
    print(f"초기 자산         : {p.initial_cash:,.0f} 원")
    print(f"최종 자산         : {m['final_equity']:,.0f} 원")
    print(f"누적 수익률       : {m['cumulative_return_pct']:.2f} %")
    print(f"총 거래 횟수      : {m['total_trades']} 회")
    print(f"승률              : {m['win_rate_pct']:.2f} %")
    pf = m["profit_factor"]
    pf_text = "∞ (손실 없음)" if pf == float("inf") else f"{pf:.2f}"
    print(f"프로핏 팩터       : {pf_text}")
    print(f"포트폴리오 MDD    : {m['mdd_pct']:.2f} %")
    print(f"Equity Curve CSV  : {EQUITY_CSV}")
    print(f"Trades Detail CSV : {TRADES_CSV}")
    print(f"  BUY {buy_count} / SELL {sell_count} 행")
    if result.pass_logs:
        print(f"진입 Pass 로그    : {len(result.pass_logs)} 건 → {PASS_LOG_TXT}")
    print("--- Phase A 검증 ---")
    if result.trades_detail.empty:
        print("⚠️ 거래 0건 — tracked/히스토리/만료 로직 점검 필요")
    elif phase_a["ok"]:
        print("✅ Phase A DoD 통과 (SELL 건수·cash_after·PnL 샘플)")
    else:
        print("⚠️ Phase A 이슈:")
        for msg in phase_a.get("issues", []):
            print(f"   - {msg}")
    print("--- Phase G 검증 ---" if not is_phase_h else "--- Phase H (YAML 동결) ---")
    if is_phase_h:
        print(
            f"✅ Phase H SSOT — SL {manager.phase_h_sl_ratio:.0%} / TP {manager.phase_h_tp_ratio:.0%} / "
            f"emperor {manager.phase_h_emperor_price_ratio:.0%} / wait {manager.phase_h_min_wait_bdays}bd"
        )
    elif phase_g["ok"]:
        print("✅ Phase G DoD 통과 (기준봉 당일 매수 없음·단리·손절 작동)")
    else:
        print("⚠️ Phase G 이슈:")
        for msg in phase_g.get("issues", []):
            print(f"   - {msg}")
    print("========================================================")


if __name__ == "__main__":
    run_v4_portfolio_backtest()
