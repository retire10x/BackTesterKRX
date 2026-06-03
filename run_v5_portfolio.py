"""
v5.0 코스닥 스나이퍼 포트폴리오 백테스트 진입점.

SSOT: config/settings.yaml v5_0
엔진: portfolio_manager Phase I (v4 경로 재사용)
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

from run_v4_portfolio import (  # noqa: E402
    PASS_LOG_TXT,
    START_DATE,
    END_DATE,
    validate_phase_a_trades,
)
from src.engine.portfolio_manager import PortfolioManager, load_merged_market_day_frames
from src.v5_config import load_v5_config, v5_to_v4_config

EQUITY_CSV = os.path.join(project_root, "outputs", "v5_equity_curve.csv")
TRADES_CSV = os.path.join(project_root, "outputs", "v5_trades.csv")


def run_v5_portfolio_backtest():
    v5 = load_v5_config()
    v4 = v5_to_v4_config(v5)
    s = v5.strategy
    k = v5.kosdaq
    p = v5.portfolio
    bet = s.field_test_invest_amount if v5.environment_mode == "field_test" else s.fixed_invest_amount

    print("🚀 v5.0 코스닥 스나이퍼 백테스트 (settings.yaml v5_0 SSOT) — 벌크 로딩 중...")
    print(
        f"   field_test={v5.environment_mode == 'field_test'} · 초기 {p.initial_cash:,.0f}원 · "
        f"베팅 {bet:,.0f}원/슬롯 · SL -{s.stop_loss_ratio:.0%} · TP +{s.target_profit_ratio:.0%} · "
        f"주가 {s.stock_price_floor:,.0f}~{s.stock_price_ceiling:,.0f}원"
    )
    print(
        f"   유니버스 시총 {k.min_mcap_krw/1e8:.0f}억~{k.max_mcap_krw/1e8:.0f}억 · "
        f"기준봉 거래대금 ≥{k.min_anchor_trade_krw/1e8:.0f}억 Top{k.anchor_top_n} · "
        f"실종 {k.volume_dry_ratio:.0%}"
    )

    day_frames, bdays = load_merged_market_day_frames(START_DATE, END_DATE, force_bulk=True)
    print(f"📊 벌크 로드 완료: {len(day_frames)} 영업일 × KOSPI+KOSDAQ")

    manager = PortfolioManager(
        day_frames,
        bdays,
        start_date=START_DATE,
        end_date=END_DATE,
        phase_i_mode=True,
        phase_h_sl_ratio=s.stop_loss_ratio,
        phase_h_tp_ratio=s.target_profit_ratio,
        phase_i_volume_dry_ratio=k.volume_dry_ratio,
        phase_i_min_anchor_trade_krw=k.min_anchor_trade_krw,
        phase_i_anchor_top_n=k.anchor_top_n,
        v4_config=v4,
    )
    result = manager.run()

    out_dir = os.path.dirname(EQUITY_CSV)
    os.makedirs(out_dir, exist_ok=True)
    result.equity_curve.to_csv(EQUITY_CSV, index=False, encoding="utf-8-sig")
    result.trades_detail.to_csv(TRADES_CSV, index=False, encoding="utf-8-sig")
    pass_path = PASS_LOG_TXT.replace("v4_", "v5_")
    if result.pass_logs:
        with open(pass_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(result.pass_logs))

    phase_a = validate_phase_a_trades(result.trades_detail, result.trades)
    buy_count = int((result.trades_detail["side"] == "BUY").sum()) if not result.trades_detail.empty else 0
    sell_count = int((result.trades_detail["side"] == "SELL").sum()) if not result.trades_detail.empty else 0
    m = result.metrics

    print("\n========================================================")
    print("📈 v5.0 코스닥 스나이퍼 최종 성적표")
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
    print("--- Phase A 검증 (v4 공통) ---")
    if result.trades_detail.empty:
        print("⚠️ 거래 0건 — 유니버스·진입 필터·히스토리 점검 필요")
    elif phase_a["ok"]:
        print("✅ Phase A DoD 통과")
    else:
        for msg in phase_a.get("issues", []):
            print(f"   - {msg}")
    print("========================================================")


if __name__ == "__main__":
    run_v5_portfolio_backtest()
