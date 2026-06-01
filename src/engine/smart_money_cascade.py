import pandas as pd
import numpy as np

def scan_smart_money_universe(df_market_today):
    """
    [Pass 0 ~ 1] 스마트머니 절대 자금 장벽 필터
    df_market_today: 당일 전 종목의 [code, name, volume_calc, close] 정보를 담은 데이터프레임
    """
    # 1. 당일 거래대금(종가 * 거래량) 계산 (단위: 원)
    df_market_today['trading_value'] = df_market_today['close'] * df_market_today['volume']
    
    # 2. [필터 A] 당일 거래대금 1,500억 원 이상만 필터링
    min_value_barrier = 150_000_000_000
    cond_value = df_market_today['trading_value'] >= min_value_barrier
    
    # 3. [필터 B] 통합 거래대금 순위 상위 20위 이내 락(Lock)
    df_market_today['value_rank'] = df_market_today['trading_value'].rank(ascending=False)
    cond_rank = df_market_today['value_rank'] <= 20
    
    universe = df_market_today[cond_value & cond_rank]
    return universe['code'].tolist()


def calculate_cascade_backtest(df_stock, start_idx):
    """
    [Pass 2 ~ 3] N회차 연쇄 종가 매수 및 24시간 타임스탑 청산 시뮬레이터
    df_stock: 단일 주도주의 일봉 데이터프레임 (index=Date, columns=[open, high, low, close, volume])
    start_idx: 스마트머니 유입 기준봉(장대양봉)이 터진 날의 인덱스
    """
    # 이동평균선 사전 연산
    df_stock['MA3'] = df_stock['close'].rolling(window=3).mean()
    df_stock['MA5'] = df_stock['close'].rolling(window=5).mean()
    df_stock['MA10'] = df_stock['close'].rolling(window=10).mean()
    df_stock['MA20'] = df_stock['close'].rolling(window=20).mean()
    
    trade_logs = []
    current_stage = 1  # 1회차(3일선)부터 순차 시작
    idx = start_idx + 1
    
    while idx < len(df_stock) and current_stage <= 4:
        row = df_stock.iloc[idx]
        prev_row = df_stock.iloc[idx - 1]
        
        # -------------------------------------------------------------------------
        # [진입 로직] 회차별 조건 만족 시 '당일 종가(close)'에 기계적 매수
        # -------------------------------------------------------------------------
        entry_triggered = False
        volume_ratio = row['volume'] / prev_row['volume']
        
        if current_stage == 1 and row['close'] <= row['MA3']:
            entry_triggered = True
            allocation_ratio = 0.50
        elif current_stage == 2 and row['close'] <= row['MA5'] and volume_ratio <= 0.70:
            entry_triggered = True
            allocation_ratio = 0.30
        elif current_stage == 3 and row['close'] <= row['MA10'] and volume_ratio <= 0.50:
            entry_triggered = True
            allocation_ratio = 0.20
        elif current_stage == 4 and row['close'] <= row['MA20'] and volume_ratio <= 0.30:
            entry_triggered = True
            allocation_ratio = 0.10
            
        if entry_triggered:
            entry_price = row['close']
            entry_date = df_stock.index[idx]
            
            # -------------------------------------------------------------------------
            # [청산 로직] 24시간~3영업일 타임스탑 레이어 추적 (왜곡 없는 고정 연산)
            # -------------------------------------------------------------------------
            hold_days = 0
            cleared = False
            target_profit_price = entry_price * 1.035  # 컴팩트 익절 목표가 (+3.5%)
            
            for t_idx in range(idx + 1, min(idx + 4, len(df_stock))):
                hold_days += 1
                t_row = df_stock.iloc[t_idx]
                
                # 트리거 A: 장중 고가가 +3.5% 목표가를 단 1원이라도 터치했는가?
                if t_row['high'] >= target_profit_price:
                    exit_price = target_profit_price
                    pnl = 0.035 - 0.00215  # 수수료 세금 공제
                    trade_logs.append({
                        "stage": f"{current_stage}회차", "entry_date": entry_date, 
                        "exit_date": df_stock.index[t_idx], "pnl": pnl, "type": "익절 🟢"
                    })
                    cleared = True
                    idx = t_idx  # 포인터를 청산일로 워프
                    break
                    
                # 트리거 B: 3영업일째 장 마감까지 목표가 도달 실패 시 Hard Time Stop
                if hold_days == 3:
                    exit_price = t_row['close']
                    pnl = (exit_price / entry_price) - 1 - 0.00215
                    trade_logs.append({
                        "stage": f"{current_stage}회차", "entry_date": entry_date, 
                        "exit_date": df_stock.index[t_idx], "pnl": pnl, "type": "타임스탑 ⚪" if pnl >= 0 else "손절 🚨"
                    })
                    cleared = True
                    idx = t_idx
                    break
            
            if cleared:
                current_stage += 1  # 청산 완료 시 다음 차수(지정 이평선) 레벨업
                continue
                
        idx += 1
        
    return pd.DataFrame(trade_logs)