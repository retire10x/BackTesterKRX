# 📈 프로젝트 진행 일지 (Progress Log)

## 1. 현재 진행 중인 핵심 태스크
- [ ] **v8.0 ORB:** 분봉 데이터 파이프라인 구축 후 `PortfolioManagerV800.run()` 활성화
- [ ] **v8.0 라이브:** `live_master_v800.py`에 KIS 분봉 수집·프리마켓 rows 연동
- [ ] **검증:** `python run_v8_00_orb_research.py` smoke test · `pytest tests/test_orb_strategy.py`

## 2. 핵심 기능 체크리스트
- [x] v7.2.0 Final Master 지수 인터록 (`portfolio_manager_v720.py`)
- [x] v8.0 ORB 순수 로직 (`src/engine/orb_strategy.py`)
- [x] v8.0 설계도 (`docs/v8_00_orb_design.md`)
- [x] v8.0 라이브 ORB 엔진 스켈레톤 (`src/live/live_orb.py`, `live_master_v800.py`)
- [x] v8.0 SSOT (`config/live_settings_v800.yaml`)
- [ ] v8.0 분봉 백테스트 엔진 (`portfolio_manager_v800.py` — TODO)

## 3. 최신 변경 이력

### 2026-06-16 (**v8.0**) ORB 시가 돌파 모멘텀 설계·기반 모듈
- **패러다임:** v7 15:20 일봉 역추세 폐기 → 09:00~09:30 Opening Range Breakout + 당일 14:50 강제청산.
- **순수 로직:** 500억+ 전일 수급, 갭 +2~7%, 첫 5분 고가 돌파, +5%/-2%/14:50 청산.
- **인프라:** `live_settings_v800.yaml`(08:50~15:00), `live_master_v800.py` 스케줄러, 분봉 백테스트는 후속.
- **브랜치:** `v8.0` (v6.0 v7.x 커밋 `e459672` 이후 분기)

### 2026-06-15 (**v7.2.0**) Final Master 지수 인터록 탑재
- **백테스트:** PF `0.69` (목표 1.5 미달). `[MARKET INTERCEPT]` 2023 폭락 구간 정상 격발.

## 4. 최신 아키텍처 상태
- **V7.2:** `portfolio_manager_v720.py`, `run_v7_20_alpha_research.py` (일봉 relay)
- **V8.0:** `orb_strategy.py`, `live_orb.py`, `live_master_v800.py`, `config/live_settings_v800.yaml`
- **청소 기록:** v7 이전 changelog → `docs/progress_archive.md`

---

변경 반영 후 **§3 맨 위**에 새 블록을 누적.
