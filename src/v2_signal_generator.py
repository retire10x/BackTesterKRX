from __future__ import annotations

import pandas as pd


def generate_v2_gap_scalper_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    v2.0 인트라데이 갭 스캘퍼 전략의 진입 신호를 생성합니다.

    입력 df는 `src.data_loader.load_v2_0_intraday_gap_scalper_data()`가 만든
    Look-ahead-safe 전처리 컬럼(`gap_pct`, `vol_ratio`)을 포함해야 합니다.

    [진입 조건]
    1) 2.0% <= gap_pct < 5.0%
    2) vol_ratio >= 150.0%

    [진입 시점]
    - 조건 만족 당일의 Open(시가) 진입

    주의: 이 함수는 당일 High/Low/Close 등 미래 정보를 참조하지 않습니다.
    """
    if df is None:
        raise ValueError("df is None")

    required = {"Open", "High", "Low", "Close", "Volume", "gap_pct", "vol_ratio"}
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"필수 컬럼 누락: {missing}")

    signal_df = df.copy()
    signal_df["buy_signal"] = 0

    gap_condition = (signal_df["gap_pct"] >= 2.0) & (signal_df["gap_pct"] < 5.0)
    vol_condition = signal_df["vol_ratio"] >= 150.0

    signal_df.loc[gap_condition & vol_condition, "buy_signal"] = 1

    signal_count = int(pd.to_numeric(signal_df["buy_signal"], errors="coerce").fillna(0).sum())
    print(
        f"[Signal] 시그널 생성 완료: 총 {len(signal_df):,}개 행 중 {signal_count:,}개의 진입 신호 발생"
    )

    return signal_df

