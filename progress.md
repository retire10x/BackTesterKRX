# 📈 프로젝트 진행 일지 (Progress Log)

## 1. 현재 진행 중인 핵심 태스크
- [x] **v10.2.1-Rebuild:** `portfolio_manager_v1021.py` · `run_v10_2_rebuild_research.py`
- [x] **평가손익 휩쏘 방지 + Safety Buffer:** 확정청산일만 EOD 정산 · 200~210만 완충구간
- [x] **Cut-off 재검증 (3.5년):** PF 1.32 ❌ · MDD -37.97% · 수확/수혈 59회 (기존 859회)
- [x] **v10.2 Safe Vault:** `capital_buffer_manager.py` · 수확/수혈 EOD
- [x] **v9.0 Fib Swing:** PF 3.17 벤치마크 (`portfolio_manager_v900.py`)

## 2. 핵심 기능 체크리스트
- [x] v10.2.1 Safe Vault 휩쏘 방지 — `has_realized_pnl_today` · 5% 버퍼(210만 수확)
- [x] v10.2.1 리서치 러너 (`run_v10_2_rebuild_research.py`) — 2023~2026 통합
- [x] v10.1 통합 PM (`portfolio_manager_v101.py`) — 일별 regime + 슬롯 공유
- [x] v10.1 백테스트 러너 (`run_v10_1_integrated_research.py`)
- [x] v10.1 장세 스케줄 (`build_regime_schedule`)
- [x] v10.0 Momentum / Swing / Cash 엔진
- [x] v10.0 스윙 2·3차 추격 분할매수 (1:1:2)
- [x] v10.1 Blackout (cash 장세 → 15:20 신규매수 차단)
- [x] v10.1 장중 -4% intraday stop (`INTRADAY_STOP_RATIO`)
- [x] v9.0 Fib Swing 백테스트 (`portfolio_manager_v900.py`)

## 3. 최신 변경 이력

### 2026-06-17 (**v10.2.1**) 평가손익 휩쏘 방지 · Safety Buffer
- **문제:** 평가손익 변동만으로 매일 수확/수혈 859회(402+457) → PF 1.32 왜곡.
- **CapitalBufferManager:** `has_realized_pnl` 게이트 · `harvest_threshold` 210만(5% 버퍼) · 200~210만 완충.
- **PM v1021:** SELL 체결 시 `has_realized_pnl_today=True` · EOD 정산 후 플래그 리셋.
- **결과(2023~2026):** PF 1.32(변동없음) · MDD -37.97% · 수확 51 / 수혈 8회 · 금고 629,328원 · 진입 15건.

### 2026-06-16 (**v10.2.1-Rebuild**) Fib 순수 스윙 + Safe Vault
- **구조:** v9.0 Fib 3단 그리드 + `CapitalBufferManager` · MarketClassifier·Momentum 완전 제거.
- **PM:** `portfolio_manager_v1021.py` — V900 상속 · 15:30 EOD 수확/수혈 · equity `safe_vault`/`rebalance_event`.
- **러너:** `run_v10_2_rebuild_research.py --prewarm 260 --yes` · **결과(2023~2026):** PF 1.32 · MDD -4.01% · 진입 15건 · 금고 678,484원 · 수확 402 / 수혈 457회.

### 2026-06-16 (**v10.2.0**) Safe Vault 자본 수확·수혈
- **CapitalBufferManager:** 15:30 EOD · 수익→금고 · 손실→금고에서 원금 200만 복구.
- **연동:** `portfolio_manager_v101` · `V10MasterRunner` · 백테스트 리포트.

### 2026-06-16 (**v10.1.0**) 통합 백테스트 엔진
- **타임머신:** `build_regime_schedule` → 일별 momentum/swing/cash → `PortfolioManagerV101`.
- **슬롯 공유:** 스윙 보유는 스윙 룰 유지 · 빈 슬롯만 당일 regime 신규 진입.
- **비용:** 매수 0.015% · 매도 0.20% · Cut-off MDD -7% / PF 2.0.

### 2026-06-16 (**v10.1.0**) 자동 장세 판정 · 오버나이트 Blackout
- **판정:** 15:15 KOSPI/KOSDAQ 지수 vs MA5·MA20 → momentum / swing / cash (양 지수 병합).
- **런처:** `--capital`만 입력 · `--preset`은 수동 오버라이드·긴급 cash용.
- **Blackout:** cash 장세 → 종가 신규매수 전면 취소 (기존 보유는 장중 -4% 감시).
- **손절:** `evaluate_intraday_stop_loss` — 평단 -4% 터치 시 즉시 시장가.

### 2026-06-16 (**v10.0.0**) 프리셋 마스터 · 스윙 추격매수
- **아키텍처:** Momentum/Swing/Cash 독립 엔진 · 200만 원 · 4슬롯 · 15:20 일봉.
- **스윙:** 1:1:2 분할매수 · Risk-Free 본전 스탑.

## 4. 최신 아키텍처 상태
- **V10.2.1-Rebuild (백테):** `portfolio_manager_v1021.py` · `run_v10_2_rebuild_research.py` (Fib+Vault only)
- **V10.1 (실전+백테):** `market_classifier` · `portfolio_manager_v101` · `run_v10_1_integrated_research.py`
- **V10.0:** `v10_live_core.py` · `high_tight_flag_strategy.py` · `fib_swing_strategy.py`
- **V9.0 (백테스트):** `portfolio_manager_v900.py`, `run_v9_00_alpha_research.py`
- **청소 기록:** v7 이전 → `docs/progress_archive.md`

---

변경 반영 후 **§3 맨 위**에 새 블록을 누적.
