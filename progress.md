# 📈 프로젝트 진행 일지 (Progress Log)

## 1. 현재 진행 중인 핵심 태스크
- [ ] **v9.0 백테스트:** `python run_v9_00_alpha_research.py --prewarm 220 --yes`
- [ ] **검증:** `python run_v9_00_alpha_research.py --smoke` · PF 목표 검토
- [x] **v8.0 폐기:** ORB 분봉 전략 중단, `v9.0` 브랜치 신설

## 2. 핵심 기능 체크리스트
- [x] v9.0 Fib Swing 순수 로직 (`src/engine/fib_swing_strategy.py`)
- [x] v9.0 포트폴리오 매니저 (`src/engine/portfolio_manager_v900.py`)
- [x] v9.0 리서치 러너 (`run_v9_00_alpha_research.py`)
- [x] v9.0 설계도 (`docs/v9_00_fib_swing_design.md`)
- [x] v9.0 SSOT (`config/settings.yaml` `v9_0`)
- [x] v7.2.0 Final Master 지수 인터록 (`portfolio_manager_v720.py`)

## 3. 최신 변경 이력

### 2026-06-16 (**v9.0.0**) 대형주 피보나치 스윙 (Risk-Free Swing)
- **패러다임:** v8 ORB 폐기 → 15:20 일봉 종가 · 200만 원 · 4슬롯 · 1:1:2 분할매수.
- **진입:** MA60×MA200 GC(3~6개월) + 피보나치 0.382/0.500/0.618.
- **청산:** 스윙고점 50% 익절 → 본전 손절 → 0라인/1:2 전량손절.
- **유니버스:** KOSPI200/KOSDAQ150 또는 시총 1조+, 5천억 미만 제외.
- **브랜치:** `v9.0` (v6.0 기준 분기, v8 미포함)

### 2026-06-15 (**v7.2.0**) Final Master 지수 인터록
- **백테스트:** PF `0.69` (목표 1.5 미달). `[MARKET INTERCEPT]` 2023~2026 폭락 구간 정상 격발.

## 4. 최신 아키텍처 상태
- **V9.0:** `fib_swing_strategy.py`, `portfolio_manager_v900.py`, `run_v9_00_alpha_research.py`
- **V7.2:** `portfolio_manager_v720.py`, `run_v7_20_alpha_research.py` (일봉 relay, v6.0/v9.0 공존)
- **폐기:** v8.0 ORB (분봉·`live_master_v800` — v9.0 브랜치에 미포함)
- **청소 기록:** v7 이전 changelog → `docs/progress_archive.md`

---

변경 반영 후 **§3 맨 위**에 새 블록을 누적.
