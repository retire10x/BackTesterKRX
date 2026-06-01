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


# .env → os.environ 주입 (KRX_ID/KRX_PW 등)
_load_env_file(os.path.join(project_root, ".env"))

from src.engine.portfolio_manager import (
    INITIAL_EQUITY,
    PortfolioManager,
    load_merged_market_day_frames,
)

START_DATE = "2023-01-01"
END_DATE = "2026-05-31"
EQUITY_CSV = os.path.join(project_root, "outputs", "v4_equity_curve.csv")


def run_v4_portfolio_backtest():
    print("🚀 v4.0 포트폴리오 백테스트 — 벌크 시장 데이터 로딩 중...")
    day_frames, bdays = load_merged_market_day_frames(START_DATE, END_DATE, force_bulk=True)
    print(f"📊 벌크 로드 완료: {len(day_frames)} 영업일 × KOSPI+KOSDAQ")

    manager = PortfolioManager(
        day_frames,
        bdays,
        start_date=START_DATE,
        end_date=END_DATE,
        initial_equity=INITIAL_EQUITY,
    )
    result = manager.run()

    os.makedirs(os.path.dirname(EQUITY_CSV), exist_ok=True)
    result.equity_curve.to_csv(EQUITY_CSV, index=False, encoding="utf-8-sig")

    m = result.metrics
    print("\n========================================================")
    print("📈 v4.0 스마트머니 포트폴리오 최종 성적표")
    print("========================================================")
    print(f"기간              : {START_DATE} ~ {END_DATE}")
    print(f"초기 자산         : {INITIAL_EQUITY:,.0f} 원")
    print(f"최종 자산         : {m['final_equity']:,.0f} 원")
    print(f"누적 수익률       : {m['cumulative_return_pct']:.2f} %")
    print(f"총 거래 횟수      : {m['total_trades']} 회")
    print(f"승률              : {m['win_rate_pct']:.2f} %")
    pf = m["profit_factor"]
    pf_text = "∞ (손실 없음)" if pf == float("inf") else f"{pf:.2f}"
    print(f"프로핏 팩터       : {pf_text}")
    print(f"포트폴리오 MDD    : {m['mdd_pct']:.2f} %")
    print(f"Equity Curve CSV  : {EQUITY_CSV}")
    if result.pass_logs:
        print(f"진입 Pass 로그    : {len(result.pass_logs)} 건 (현금/슬롯 부족)")
    print("========================================================")


if __name__ == "__main__":
    run_v4_portfolio_backtest()
