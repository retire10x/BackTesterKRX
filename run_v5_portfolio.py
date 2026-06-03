"""
v5.0 20일선 변곡점 스나이퍼 백테스트.

SSOT: config/settings.yaml v5_0
엔진: PortfolioManagerV5 (변곡 진입 · MA20 이탈 청산)
"""
import os
import sys

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


_load_env_file(os.path.join(project_root, ".env"))

from run_v4_portfolio import START_DATE, END_DATE, validate_phase_a_trades  # noqa: E402
from src.engine.portfolio_manager import load_merged_market_day_frames  # noqa: E402
from src.engine.portfolio_manager_v5 import PortfolioManagerV5  # noqa: E402
from src.v5_config import load_v5_config  # noqa: E402

EQUITY_CSV = os.path.join(project_root, "outputs", "v5_equity_curve.csv")
TRADES_CSV = os.path.join(project_root, "outputs", "v5_trades.csv")
PASS_LOG_TXT = os.path.join(project_root, "outputs", "v5_pass_log.txt")


def run_v5_portfolio_backtest():
    v5 = load_v5_config()
    env = v5.environment
    port = v5.portfolio
    strat = v5.strategy
    costs = port.trading_costs

    print("🚀 v5.0 20일선 변곡점 스나이퍼 (settings.yaml v5_0 SSOT) — 벌크 로딩 중...")
    print(
        f"   {strat.strategy_name} · field_test={env.mode == 'field_test'} · "
        f"초기 {env.initial_cash:,.0f}원 · 슬롯 {port.max_slots} × {port.slot_invest_amount:,.0f}원"
    )
    print(
        f"   진입 MA{strat.lookback_window} 변곡(어제≤MA · 오늘>20영업일전종가) · "
        f"청산 MA{strat.exit_ma_window} 종가 이탈 · 주가 {strat.price_floor:,.0f}~{strat.price_ceiling:,.0f}원"
    )
    print(
        f"   비용 매수 {costs.buy_cost_ratio:.4%} / 매도 {costs.sell_cost_ratio:.4%}"
    )

    day_frames, bdays = load_merged_market_day_frames(START_DATE, END_DATE, force_bulk=True)
    print(f"📊 벌크 로드 완료: {len(day_frames)} 영업일 × KOSPI+KOSDAQ")

    manager = PortfolioManagerV5(
        day_frames,
        bdays,
        start_date=START_DATE,
        end_date=END_DATE,
        v5_config=v5,
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
    buy_count = int((result.trades_detail["side"] == "BUY").sum()) if not result.trades_detail.empty else 0
    sell_count = int((result.trades_detail["side"] == "SELL").sum()) if not result.trades_detail.empty else 0
    m = result.metrics

    print("\n========================================================")
    print("📈 v5.0 20일선 변곡점 스나이퍼 최종 성적표")
    print("========================================================")
    print(f"기간              : {START_DATE} ~ {END_DATE}")
    print(f"초기 자산         : {env.initial_cash:,.0f} 원")
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
    print("--- Phase A 검증 ---")
    if result.trades_detail.empty:
        print("⚠️ 거래 0건 — 변곡 조건·가격 캡·히스토리 점검 필요")
    elif phase_a["ok"]:
        print("✅ Phase A DoD 통과")
    else:
        for msg in phase_a.get("issues", []):
            print(f"   - {msg}")
    print("========================================================")


if __name__ == "__main__":
    run_v5_portfolio_backtest()
