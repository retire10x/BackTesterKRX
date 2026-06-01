import os
import sys

# [v4.0 경로 방어선] 스크립트 위치 기준 프로젝트 루트를 sys.path 최우선 주입
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.data_loader import _load_ohlcv_pykrx_by_date
from src.engine.smart_money_cascade import calculate_cascade_backtest, _normalize_ohlcv_columns

def run_lg_electronics_test():
    print("🚀 데이터 로딩 파이프라인 가동 중... 잠시만 기다려주세요.")
    
    # 1. LG전자(066570) 3개년 데이터 수집
    ticker = "066570"
    start_date = "2023-01-01"
    end_date = "2026-05-31"
    
    # 위에서 임포트한 함수명과 100% 일치하도록 보정 완료
    df_lg = _load_ohlcv_pykrx_by_date(ticker, start_date, end_date)
    
    if df_lg is None or df_lg.empty:
        print("❌ [ERROR] 데이터 로드 실패. pykrx 수집망을 확인하세요.")
        return

    df_lg = _normalize_ohlcv_columns(df_lg)

    print(f"📊 데이터 로드 완료: 총 {len(df_lg)} 영업일 데이터 확보.")

    # 2. 거래대금 계산 및 스마트머니 기준봉(1,500억 이상) 매칭
    df_lg['trading_value'] = df_lg['close'] * df_lg['volume']
    
    # 거래대금 1,500억 이상 터진 날 검색
    smart_money_days = df_lg[df_lg['trading_value'] >= 150_000_000_000].index
    
    if len(smart_money_days) == 0:
        print("⚪ 조사 기간 내에 거래대금 1,500억 이상 터진 스마트머니 기준봉이 없습니다.")
        return
        
    print(f"🎯 스마트머니 감지된 기준봉 날짜들: {list(smart_money_days.strftime('%Y-%m-%d'))}")

    # 3. 첫 번째 스마트머니 기준봉 기점으로 v4.0 연쇄 청산 시뮬레이션 돌리기
    first_signal_date = smart_money_days[0]
    start_idx = df_lg.index.get_loc(first_signal_date)
    
    print(f"\n▶ 첫 기준봉 위치 [{first_signal_date.strftime('%Y-%m-%d')}] 기점으로 v4.0 연쇄 시뮬레이션 가동")
    result_df = calculate_cascade_backtest(df_lg, start_idx)
    
    # 4. 결과 리포트 출력
    print("\n========================================================")
    print("📈 v4.0 스마트머니 연쇄 청산 최종 결과 증명 리포트")
    print("========================================================")
    if result_df is None or result_df.empty:
        print("⚪ 조건 만족 후 진입한 내역이 없습니다. (조정 시 3일선 미도달 등)")
    else:
        print(result_df.to_string(index=False))
    print("========================================================")

if __name__ == "__main__":
    run_lg_electronics_test()