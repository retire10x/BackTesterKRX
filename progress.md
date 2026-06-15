# 📈 프로젝트 진행 일지 (Progress Log)

## 1. 현재 진행 중인 핵심 태스크
- [x] **실행 완료:** `python run_v7_20_alpha_research.py --prewarm 120 --mode dynamic_warmup --universe all --yes > outputs/v7_20_final_run.log 2>&1`
- [x] **산출물:** `outputs/v7_20_final_trades.csv`, `outputs/v7_20_final_research_report.md`, `outputs/v7_20_final_run.log`
- [ ] **Sign-off 미달:** PF `0.69`로 목표 `>= 1.5` 미달. 2023년 폭락 구간 `[MARKET INTERCEPT]` 차단 로그는 정상 확인.

## 2. 핵심 기능 체크리스트
- [x] v7.1.0 Pivot 매니저 추가 (`src/engine/portfolio_manager_v710.py`)
- [x] v7.1.0 전용 리서치 러너 생성 (`run_v7_10_alpha_research.py`)
- [x] V710 임포트 및 `_run_relay` 호출부를 `alpha_eq/alpha_tr/alpha_td/alpha_m` 구조로 정리
- [x] v7.2.0 Final Master 매니저 추가 (`src/engine/portfolio_manager_v720.py`)
- [x] v7.2.0 전용 리서치 러너 생성 (`run_v7_20_alpha_research.py`)
- [x] v7.2.0 전체 백테스트 실행 및 `[MARKET INTERCEPT]` 로그 검토

## 3. 최신 변경 이력

### 2026-06-15 (**v7.2.0**) Final Master 지수 인터록 탑재
- **신규 엔진:** `PortfolioManagerV720`이 `PortfolioManagerV710`을 상속하고 `kosdaq_index_df` 주입을 필수화.
- **시장 필터:** KOSDAQ 종가가 3일 이동평균선 아래면 `_process_entries()` 초입에서 당일 신규 매수 전면 차단 및 `[MARKET INTERCEPT]` 로그 출력.
- **신규 러너:** `run_v7_20_alpha_research.py`에서 KOSDAQ 지수 일봉을 로드해 릴레이 매니저에 전달.
- **검증:** `python -B -m py_compile "src/engine/portfolio_manager_v720.py" "run_v7_20_alpha_research.py"` 통과.
- **백테스트:** 7구간 전체 실행 완료. PF `0.69`, 누적 수익률 `-75.41%`, MDD `-80.66%`, 목표 PF `1.5` 미달.
- **차단 확인:** 2023-09~10, 2024-08, 2026-03 등 지수 하락일에 `[MARKET INTERCEPT]` 로그 정상 격발.

### 2026-06-14 (**v7.1.0**) 다음 실행 작업 정리
- **다음 명령:** `python run_v7_10_alpha_research.py --prewarm 120 --mode dynamic_warmup --universe all --yes`
- **상태:** 사용자가 내일 다시 실행 예정. 오늘은 실행 보류.
- **목적:** V7.1.0 Pivot 전략의 7구간 릴레이 백테스트 실행 및 PF `1.5` 달성 여부 확인.
- **산출 예정:** `outputs/v7_10_pivot_trades.csv`, `outputs/v7_10_alpha_research_report.md`

### 2026-06-14 (**v7.1.0**) Pivot 러너 생성
- **신규 파일:** `run_v7_10_alpha_research.py`
- **핵심 연결:** `PortfolioManagerV710` 단독 실행, `_run_relay(... manager_cls=PortfolioManagerV710, manager_kwargs=alpha_kwargs)`
- **검증:** `python -m py_compile "run_v7_10_alpha_research.py"` 통과

### 2026-06-14 (**v7.1.0**) Pivot 엔진 추가
- **신규 파일:** `src/engine/portfolio_manager_v710.py`
- **진입 조건:** 최근 20영업일 최고 거래대금 `>= 200억`, 낙폭과대, 양봉 또는 긴 아랫꼬리 브레이크 캔들, 거래량 급감
- **청산:** `+8% / -3% / 4일`
- **검증:** `python -m py_compile "src/engine/portfolio_manager_v710.py"` 통과

## 4. 최신 아키텍처 상태
- **V7.0:** `src/engine/portfolio_manager_v700.py`, `run_v7_00_alpha_research.py`
- **V7.1:** `src/engine/portfolio_manager_v710.py`, `run_v7_10_alpha_research.py`
- **릴레이 기간/유니버스:** `src/v5_relay_screener.py`의 7구간, 코스닥 전종목 마스크
- **청소 기록:** 2026-06-08~06-09 이전 최신 changelog는 `docs/progress_archive.md`로 이관

---

변경 반영 후 **§3 맨 위**에 새 블록을 누적. 완료 항목이 5개 이상이거나 파일이 100줄을 넘으면 `docs/progress_archive.md`로 이관.
