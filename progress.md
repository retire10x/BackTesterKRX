# 📈 프로젝트 진행 일지 (Progress Log)

## 1. 핵심 기능 체크리스트 (Feature Checklist)
- [x] 기본 이평선(MA{N}) 골든/데드크로스 신호 생성 로직 (`strategy.py`)
- [x] 다음 봉 시가 체결 및 거래 비용(설정 가능·기본 매수 0.015% / 매도·세금 0.20%) 반영 시뮬레이터 (`simulator.py`, GUI·YAML 연동)
- [x] 가변 낙폭(Trailing Stop) 상/하단 분리 청산 기능 (`settings.yaml` 연동)
- [x] 매수 진입 3대 필터(대세 Slope, 돌파 강도, 시간 버퍼) AND 결합 구현
- [x] 차트 상단 4단 미디어 플레이어 내비게이션 버튼 (⏪, ◀, ▶, ⏩) UI 이동
- [x] 매매 규칙 v4.6 설정 바 매수/매도 영역 시각적 그룹화 (UI 분리)
- [x] 차트 매수/매도 화살표 마킹 시점 및 Y축 앵커 위치 보정 (T일 종가 캔들)
- [x] 차트 마킹 시점 검증 데이터 파일 (`backtest_signal_debug.txt`) 자동 생성 기능
- [x] 차트 내비게이션 이동 영업일 수 단축 (일/주) 및 툴팁 문구 동기화
- [x] 시장 선택 ETF(KR) 상장 종목 검색 및 백테스트 연동 (`data_loader.fdr_stock_listing`)
- [x] 좌측 패널 수수료 입력·종목 실행 이력(FIFO 최대 30)·검색/이력 리스트 더블클릭 즉시 백테스트 (`gui.py`, `gui_helpers.try_build_config`)
- [x] 백테스트 종목 이력 `output/backtest_history.json` 저장·재실행 시 자동 불러오기 (`gui.py`)
- [x] 일봉 기준 종목 스크리너(종료일 이전 확정 분만 이용, 최근 N거래일 변동성·거래대금·고점대비 낙폭·거래량건조 순위분위 융합 상위 M개) 목록 출력·연동 (**GUI는 일괄 루프 없이 단일 백테스트**; 검색 결과·이력 더블클릭 포함) 및 CLI 등 (`stock_screener.py`, GUI·CLI·`settings.yaml` `universe.screener`)
- [x] 스크리너 하드 필터: 종료일 종가 기준 120일선 역배열 종목 제외 (`stock_screener.py`)
- [x] 스크리너 시총(상장표 Marcap)·MA20/120 추세 하드 게이트, 차트 패닝 일봉 캐시, Harness 매수 세 조건 동시 적용 YAML 옵션
- [x] 메인 GUI 그리드 가변(weight)·차트 패널 실측 리사이즈·검색/이력/날짜 컴팩트 배치·매수·매도 **동일 행 2열** 카드 내 필터 **2단 행**·**규칙 헤더 Refresh로 조건 반영 차트 재계산** (`gui.py`; PNG 저장 시 `autofmt_xdate` 등 `backtest_chart.py`)
- [x] **v4.7** 하단 독립 누적수익률 패널 제거(캔들+거래량 2단)·좌패널 **최고/최저 누적 수익률**·GUI **주가 패널 수익률 음영** 토글(`show_return_overlay`)·저장 후 `plt.close(fig)` (`backtest_chart.py`, `metrics.py`, `gui.py`, `gui_helpers.py`)
- [x] **v4.10** 백테스트 고속 경로: `data_loader` 상장표 TTL·OHLCV LRU, `metrics.defer_chart_render` + `materialize_backtest_chart_png` 로 통계/차트 분리(GUI), `simulator` ndarray 루프, 수익률 오버레이 기본 OFF (`gui.py`·`metrics.py`·`data_loader.py`·`simulator.py`)
- [x] **v4.11** 차트 교체 시 캔버스 `PhotoImage` 스왑(`itemconfig`)·PNG 생성 대기 중 이전 이미지 유지·차트 툴바 Braille 로딩·연타 시 materialize 티켓 필터 (`gui.py`)
- [x] **v4.9** 디스플레이·창 크기 반응형 차트 패널(우패널 `grid_propagate(False)`·실측 추정·지연 리페인트 + `metrics.chart_render_px` → mpl `figsize`/DPI·`gui_target` 여백) (`gui.py`, `metrics.py`, `backtest_chart.py`)
- [x] **v4.12_Beta** 스크리너 모드 「당일 타점(Event) 추적」: 골든+`simulate_single` 진입 필터(`_buy_filters_pass`) 기준 최근 3영업일 전환·이격도 컬럼·정렬(`stock_screener.py`·`gui.py`·`gui_helpers.py`)
- [x] **v4.13** 스크리너 「김직선 1봉 캔들 추적」: 장대양봉+20일 최대 거래량 기준봉 후 당일 고가돌파/중심선지지(`stock_screener.py`·`gui.py`·`gui_helpers.py`)
- [x] **v4.14** GUI 라디오 폐지·**순차 AND 파이프라인** 체크(시총 Top100·매수 규칙 종봉·김직선 1봉); `execute_pipelined_screening`·`PipelineScreenerPick`·통합 목록 포맷; YAML `universe.screener_pipeline`
- [x] **v4.14_Fix** 파이프라인: 시총 상위 게이트와 별개의 **3000억 하한 미적용**·Top-N 표시 **`disp_cap`** 하한
- [x] **v4.15** `merge_live_trade_panel_into_strategy` 단일 헬퍼로 백테·검색 `strategy` 동기화 · 2단계 스크린은 **골든 OFF 에도 진입 필터 종봉 AND** 적용(`stock_screener`·`gui_helpers`)
- [x] **v4.16_Patch** 김직선 1봉: 기준봉 거래량 **300% 또는 20일 TOP3**, 고가돌파 허용 `τ ∈ [T-3,T]`, 패턴 문자열 **`고가돌파 (경과일: N일)`**·파이프라인·GUI 동일 정렬 키

- [x] **v3.0** [1단계] Clean-up: v2.0 SRS·`src/v2_*` 모듈 폐기, `load_v3_0_overnight_scalper_data` 로더
- [x] **v3.0** [2단계] Signal Generator: 거래량 150%·장대양봉 4%·위꼬리≤20% 종가 진입 (`src/v3_signal_generator.py`)
- [x] **v3.0** [3단계] Execution Engine: 종가 매수·익일 시가 청산·BUY 0.015% / SELL 0.20% 고정 (`src/v3_execution_engine.py`)
- [x] **v3.0** [4단계] Analytics: OVERNIGHT PERFORMANCE REPORT 단일 출력 (`src/v3_metrics.py`)
- [x] **v3.0** 인수검증: `main.py --mode cli` 대시보드만·`SELL_COST=0.0020` 고정
- [x] **v3.0** Code Freeze: `v3_signal_generator`·`v3_execution_engine` 진입/청산 로직 고정
- [x] **v3.0** 다중 기간 CLI 검증: 세션 A/B/C (`--start`/`--end`만 변경)·`output/v3_multi_period_report.md`
- [x] **v3.1** Overnight Scanner GUI: 좌측 검색=스캔 트리거·결과 리스트 `코드|종목명|당일상승률|시총|거래대금`(억 반올림·극대 시총 `천억` 축약) 표시 · `fetch_pykrx_marcap_trade_krw_by_code`/상장 시총·종가×거래량 폴백
- [x] **v3.1** 레이아웃 다이어트: 우측 상단 매매 규칙/요약 로그 제거·차트 뷰어 확장
- [x] **v3.1** 레거시 네비/단축키 유지: `[⏪][◀][▶][⏩]` + 키 `1/2/7/8` 기간 이동 시 리스트 비갱신
- [x] **v3.1** 차트 전용 전환: 주도주/이력 더블클릭 시 백테스트 미실행·기간 OHLC 차트만 렌더
- [x] **v3.1** 스캐너 디버그 보강: KOSPI 전수(`universe_limit=0`)·단계별 생존 카운트 로그 파일 출력
- [x] **v3.1** 프로덕션 I/O: GUI 차트(`차트 전용`·연기 백테스트 후처리)는 `output/` `.png` 미생성(`render_backtest_chart_png_bytes`·`materialize_backtest_chart_png_bytes`)
- [x] **v3.15** 차트 상단 수익률 버튼·`show_return_overlay` 레거시 제거 · 5중 이평(5·10·20·60·120일) 토글 체크박스·기간별 두께/색·`line.set_visible`·범례 연동 (`gui.py`, `backtest_chart.py`, `metrics.py`, `gui_helpers.py`)
- [x] **v3.16** 차트 X축 날짜 제거·가격/거래량 패널 구분선 · 휠 줌+줌 리셋(메모리 PNG 재렌더) (`backtest_chart.py`, `gui.py`)
- [x] **v3.70** 스캔 파라미터 SSOT: `v3_scan_config` 엄격 YAML(`KeyError`)·`resolve_effective_pullback_scan_params`(세션 오버레이)·엔진 기본 인자 없음·GUI `bootstrap_gui_pullback_scan_ssot` 초기화 순서
- [x] **v3.75** 해상도별 폰트 가변 차단 (v3.76에서 철회·아래 참고)
- [x] **v3.76** OS DPI System-Aware: CTk `set_*_scaling` 미호출(기본 OS DPI) · Tk 폰트 양수 pt(11/10/9)
- [x] **v3.80** 눌림목 실전 필터: t-1 양봉(종가>시가)·t 종가≥전일 중심선 — 벌크·단일·백테스트 AND
- [x] **v3.85** 유니버스 Top 1000·ALL(0) · Pass4 종가>MA60 (`src/filters.py`)
- [x] **v3.86** Top ALL 시 KOSPI+KOSDAQ OHLCV·시총 병합 (`pullback_bulk_markets_for_scan`)
- [x] **v3.70** pykrx 일별 전종목 벌크 OHLCV 로컬 캐시(`data/cache/ohlcv_by_ticker/*.pkl`)·앵커일만 재조회·폴백 스캔도 캐시 조립
- [x] **v3.30** 주도주 눌림목 스캔: `scan_leader_pullback_candidates_bulk`(t-1 세력·MA20 지지·거래량 급감)·GUI 명칭·세력 배수/눌림 비율 입력
- [x] **v3.40** 스캔·백테스트 UI 분리: 파라미터 상단·이력 하단·수수료 UI 제거·단일 종목 눌림목 타임라인 백테스트(`pullback_backtest.py`)
- [x] **v3.45** UI 폴리싱: 차트 패널 구분선 제거·캔버스 중앙 정렬·맑은 고딕 통일·리스트 10pt·날짜 폭 확대·원금/매도 1행·이력 높이 확장
- [x] **v3.50** 김직선 정배열 추세 필터: 종가>MA120 · MA5≥MA10 — 역배열·우하향 종목 스캔·백테스트 전면 제외
- [x] **v3.60** 유니버스 콤보(100/300/500)·`last_session.json` 세션 복원·스캔/백테스트 밀리초 타이머·버튼 컴팩트·연한 레드 중단색
- [x] **v3.65** 좌측 패널 200px 슬림화·파라미터 2단 행·원금|매도 1행·안내 1줄 압축
- [x] **v3.66** 모멘텀 필터(MA5≥MA10) GUI 토글·YAML `use_momentum_filter`·스캔/백테스트/parity 동기화 · 유니버스 콤보 `Top 100` / `Top` / `Top 500` 라벨
- [x] **v3.88** 차트 단일 렌더러 `render_stock_chart`·`update_chart_canvas`·`ticker_to_name` SSOT — 더블클릭·⏪◀▶⏩ 내비·이평 토글 공통 경로·상단 종목명 동기화 (`gui.py`)
- [x] **v3.89** 입력 패널 날짜 행 1개월 단위 기간 이동(◀▶)·종목 선택 시 차트 자동 갱신 (`gui.py`, `data_loader.months_before`)
- [x] **v3.90** OHLCV pykrx 단일화(벌크 캐시·by_date)·Pass4 MA60·MA120 듀얼 AND·MA5≥MA10 부트스트랩 기본 ON (`filters.py`, `data_loader.py`, `gui.py`, `gui_helpers.py`)
- [x] **v3.95** Pass4 Perfect Trend Lock: 종가>MA60·MA120 AND **MA60>MA120** 배열성 (`filters.py`, `data_loader.py`, `gui.py`)
- [x] **v4.00** Pass0 유동성: 시총·당일 거래대금 하한 SSOT(`settings.yaml` v3_0) · 벌크·폴백·단일 판정 (`filters.py`, `v3_scan_config.py`, `data_loader.py`, `gui.py`)
- [x] **v4.10** 시장/유니버스 분리: 시장=ALL(KOSPI+KOSDAQ)·Top=ALL(선택 시장 전종목) · 리스트 [주]/[닥] · 스캔 스냅샷 고정 (`filters.py`, `data_loader.py`, `gui.py`, `gui_helpers.py`)
- [x] **v4.15** Pass2 중심선 OR: MA20 터치 회복 **또는** (MA20 위 + t-1 중심선) — 벌크·단일·백테스트 SSOT (`filters.py`, `data_loader.py`, `pullback_backtest.py`, `gui.py`)
- [x] **v4.20** 스캔 검출 근거 Excel 스냅샷: `src/engine/exporter.py` · Pass0~4 정량 필드 · Disparity5/20 · GUI 📥 근거 버튼 · `outputs/evidences/`
- [x] **v4.25** OHLC 4대 가격 스냅샷·이격도 락: t0 Open/High/Low/Close Excel 강제 · Pass2 105%/110% 스캔 게이트 SSOT
- [x] **v4.0** 스마트머니 연쇄 청산 엔진: `scan_smart_money_universe`·`calculate_cascade_backtest` (`src/engine/smart_money_cascade.py`)
- [x] **v4.0** LG전자(066570) 3개년 연쇄 청산 실행 스크립트 (`run_v4_test.py` · sys.path 방어 · `_load_ohlcv_pykrx_by_date`)
- [x] **v4.0** 포트폴리오 매니저·전 종목 백테스트 (`portfolio_manager.py`·`run_v4_portfolio.py` · Phase A~D 검증·`outputs/v4_trades.csv`)
- [x] **v4.0** Phase G 심폐소생 (눌림 -3%·단리 1천만·손절 -5%·익절 +3.5%) — `portfolio_manager` Phase G 진입/청산
- [x] **v4.0** Phase G 14시나리오 전체 스윕·눌림목 타점 보고 (`run_v4_tune.py` → `outputs/v4_tune_report.md` §눌림목)
- [x] **v4.0** Phase H 계단식 박스권 쌍바닥(Double Bottom) 타점 엔진 (`portfolio_manager` Phase H 진입/청산 + `run_v4_tune` `combo_phase_h_double_bottom`)
- [x] **v4.0** Phase H-2 미세 그리드 스윕 CLI (`run_v4_tune.py --phase-h2-grid`) 및 H 파라미터 주입형 엔진 실행
- [x] **v4.0** Phase H-3 소액 필드 테스트 모드(10만 원·2슬롯·주가 1천~2만 캡) 및 진입 선필터
- [x] **v4.0** Phase E SSOT — `config/settings.yaml` `v4_0.*` · `src/v4_config.py` · `src/utils/config.py`

## 2. 최신 변경 이력 (Changelog)

### 2026-06-02 (**v4.0** Phase H-3 — 10만 원 소액 필드 테스트 인프라)
- **`config/settings.yaml`:** `v4_0.environment.mode=field_test`·`initial_cash=100000` · `portfolio.max_slots=2` · `strategy.field_test_invest_amount=50000` · `stock_price_floor=1000` · `stock_price_ceiling=20000`
- **`src/v4_config.py`:** `environment` 섹션 파싱 및 `V4Config` 확장(`environment_mode`, `environment_initial_cash`) · field_test 시 `portfolio.initial_cash`를 환경값으로 대체
- **`src/engine/portfolio_manager.py`:** `_phase_h_entry_allowed` 신설 — **주가 floor/ceiling 선검사 후** `_phase_h_tactical_filter` 수행(연산량 절감) · field_test 모드 기본 H 베팅금=5만 적용

### 2026-06-02 (**v4.0** Phase H-2 — 손절·익절·황제주컷 미세 그리드)
- **`run_v4_tune.py`:** `--phase-h2-grid` 추가 (`sl` 3/4/5%, `tp` 6/8/10%, `emperor_cap` 30/20/15%) · `--quick` 시 축소 그리드
- **`run_v4_tune.py`:** Phase H 결과 열 `phase_h_sl_ratio`·`phase_h_tp_ratio`·`phase_h_emperor_cap_ratio`·`phase_h_fixed_amount` CSV/리포트 반영
- **`src/engine/portfolio_manager.py`:** Phase H 하이퍼파라미터를 생성자 주입값으로 수용(시나리오별 미세 튜닝 실행 가능)

### 2026-06-02 (**v4.0** Phase H-2 — 시간·황제주·저점 윈도우 정합성 패치)
- **`_phase_h_tactical_filter`:** 달력일 `.days` 제거 → **영업일 5일** 관망 · 황제주(종가 > 베팅금 30%) 진입 차단 · 로컬 저점 **20영업일** · 정수 주수 기준 `invest_amount` 캡(수량 누수 방지)
- **`run_v4_tune.py`:** `--only SCENARIO ...` (부분 스윕)

### 2026-06-02 (**v4.0** Phase H — 계단식 박스권 쌍바닥 타점 엔진)
- **`src/engine/portfolio_manager.py`:** `phase_h_mode` 추가 · 5일선 하회+MA10/20 수렴+쌍바닥 지지 진입 · SL -3% / TP +10% / 5일 타임스탑 청산(`STOP_LOSS_H`/`TAKE_PROFIT_H`/`TIME_STOP_H`)
- **운용:** Phase H는 1회차 단일 진입만 허용(연쇄 stage 진입 비활성) · 슬롯당 300만 단리·당일 배분 상한 유지
- **`run_v4_tune.py`:** 시나리오 구조 `(name, overrides, phase_mode)`로 확장 · `combo_phase_h_double_bottom` 추가 · 결과 CSV에 `phase_mode`/`stop_loss_h_count` 기록
- **DoD 보고:** 튜닝 리포트에 `## Phase H DoD 체크` 자동 생성(거래수 감소율, `STOP_LOSS_H` 건수, PF 1.0 돌파 여부)

### 2026-06-02 (**v4.0** Phase G — 14시나리오 전체 스윕·최적 눌림목 도출)
- **실행:** `python run_v4_tune.py` (벌크 1회 · 920영업일 · ~162분) → `outputs/v4_tune_results.csv` · `v4_tune_report.md`
- **결과:** PF≥1 **0/14** · 흑자 **0/14** — 현 그리드로 Ruin 해소 불가
- **PF 1위:** `rr_asym_sl3_tp5` (손절 3%·익절 5%) PF **0.69**, 최종 261만(-91.3%)
- **최종 자산 1위:** `combo_def3m_rr_wide` (300만·deploy 20%·눌림 4%·SL5/TP5) **872만**(-70.9%), PF **0.68**
- **눌림목 단독(1천만·deploy 45%·SL/TP 동일):** 2% PF 0.57 ≈ 3% baseline 0.57 > 5% PF 0.50 — **얕은 2%** PF 우세, 자산은 baseline(63만)이 2%(60만)보다 근소 우세
- **코드:** `run_v4_tune.py` 보고서에 `## 눌림목 타점` 섹션 추가 · `docs/v4_ruin_analysis.md` §7 갱신
- **YAML:** 자동 반영 없음(main 병합 없음) — 채택 시 `nuliim_ratio`·사이징·손익비 수동 검토

### 2026-06-01 (**v4.0** Phase G — Ruin 서면·튜닝 러너, main 병합 없음)
- **`docs/v4_ruin_analysis.md`:** Ruin 메커니즘·Phase B/C 인용·튜닝 레버 표
- **`run_v4_tune.py`:** `v4_0.strategy` 시나리오 스윕 · `outputs/v4_tune_results.csv`·`v4_tune_report.md` · `--quick`
- **`src/v4_config.py`:** `v4_config_with_strategy_overrides()` — YAML 덮어쓰기 헬퍼
- **정책:** 현재 브랜치 유지 · main/PR 보류
- **`--quick` 7시나리오 (~86분):** PF≥1 **0/7** · 흑자 **0/7** · 최선 `defense_3m_deploy15` 최종 **813만**(-72.9%, PF 0.56) · PF 1위 `rr_wide_5pct` PF **0.64**(-96.4%)

### 2026-06-01 (**v4.0** Phase F — Harness §4 progress 아카이브)
- **`docs/progress_archive.md`:** §2 changelog **2026-05-22 ~ 2026-05-31** 원문 누적 이관 (~315행).
- **`progress.md`:** v4.0 Phase A~G·엔진 착수 이력만 §2 유지 · §3 아카이브 구간 갱신.

### 2026-06-01 (**v4.0** Phase E — settings.yaml SSOT 이관)
- **`config/settings.yaml`:** `v4_0.strategy`·`portfolio`·`costs` 블록 추가 (눌림·단리·손익·tracked 만료·수수료)
- **`src/v4_config.py`:** `V4Config` dataclass · `load_v4_config()` (v3_scan_config 패턴)
- **`portfolio_manager.py`:** `PHASE_G_*`·`INITIAL_EQUITY` 하드코딩 제거 → 생성자 YAML 바인딩
- **검증:** `run_v4_parity.py` **3/3 일치** (phase_g_mode=False) · `run_v4_portfolio.py` Phase A/G DoD 통과
- **벌크 재실행(Phase G+YAML):** 최종 633,906원 (-97.89%) · 828 SELL · PF 0.57 · MDD -97.98%
- **참고:** `outputs/backup_pre_phase_e`는 중단된 짧은 G 실행분(158행)이라 전체 diff 동치 기준 아님 — 상수값은 이관 전과 동일

### 2026-06-01 (**v4.0** Phase G — 심폐소생 패치)
- **G-1:** 기준봉 당일 매수 금지 · 종가 ≤ 앵커×(1-3%) 눌림목
- **G-2:** 슬롯당 1,000만 원 단리 (`fixed_invest_amount`)
- **G-3:** STOP_LOSS -5% · TAKE_PROFIT +3.5% · 3일 TIME_STOP (장중 고/저가)

### 2026-06-01 (**v4.0** Phase D — 로직 정합성 패치·전 포트 재검증 완료)
- **D-1:** 동일 `code` open position 시 추가 BUY 차단 (`portfolio_manager._process_entries`)
- **D-2:** `TRACKED_EXPIRE_BDAYS=30` — stage==1·미보유만 tracked 만료 (연쇄 2회차+ 유지)
- **D-3:** `compute_stage_invest_amount` + 당일 현금 45% 배분 상한 (`MAX_DAILY_CASH_DEPLOY_RATIO`)
- **Fix:** `candidate_codes`마다 매일 `_append_history_bar` — 선적재 후 진입 0건 버그 제거
- **재실행:** `run_v4_parity.py` **3/3 일치** · `run_v4_portfolio.py` BUY 1,189 / SELL 1,186 · Phase A DoD 통과
- **성과(패치 후):** 최종 57,701원 (-99.81%) · PF 0.35 · MDD -99.84% — 패리티 유지했으나 **Ruin 미해소**(전략·총자산 비례 사이징 한계)
- **보류:** D-4 엔진 통합·D-5 `hold_days` — Phase E/G 또는 별도 착수
- **다음:** Phase E — `settings.yaml` v4_0 SSOT

### 2026-06-01 (**v4.0** Phase C — LG전자 패리티 동치 검증 완료)
- **`run_v4_parity.py`:** Top20+1,500억 첫 앵커(2023-02-08) · 격리 포트(066570만·max_slots=1·OHLCV 선적재)
- **`portfolio_manager.py`:** `allowed_codes`·`anchor_first_smart_money_only`·`preload_ohlcv` · **`_ohlcv_history_as_of`**(미래봉 참조 차단)
- **결과:** 단일 vs 격리 포트 **3건 키 100% 일치** · `outputs/v4_parity_report.md`
- **시사:** 전 종목 포트 -99%는 엔진 로직 오류보다 **다종목 자금/슬롯 경쟁** 가능성 큼 → Phase D
- **다음:** Phase D 로직 패치

### 2026-06-01 (**v4.0** Phase B — 자산 소멸 집중 감사 완료)
- **`scripts/audit_v4_phase_b.py`:** B-1 사이징·B-2 유령(기준봉 지연)·B-3 LG diff — `outputs/v4_audit_sizing.md`·`v4_audit_ghost.csv`·`v4_logic_diff_066570.md`
- **B-1:** 현금 초과 매수·음수 cash 없음 · slot_budget=equity/3 동적 반영 · 동시 3종목 1회차 시 현금 최대 50% 투입
- **B-2:** 30일 초과 진입 16.3% · 최대 지연 837일 · 실현손실 최대는 delay 0~5일 구간
- **B-3:** LG 1~2회차 일치·3회차 불일치(단일 04-24 vs 포트 11-03) — 다종목 경쟁으로 연쇄 시퀀스 어긋남
- **1차 원인:** PF 0.36+총자산 비례 재투자 Ruin · 유령 tracked · 포트/단일 엔진 불일치
- **다음:** Phase C 패리티 또는 Phase D 패치

### 2026-06-01 (**v4.0** Phase A — `v4_trades.csv` 익스포트 완료)
- **`portfolio_manager.py`:** BUY/SELL 각 1행 · `trade_id`·`cash_after`·`total_equity_after`·`slot_budget_at_entry`·`alloc_ratio` 등 `trades_detail` 누적
- **`run_v4_portfolio.py`:** `outputs/v4_trades.csv`·`v4_pass_log.txt`(있을 때) · Phase A DoD 자동 검증
- **인수:** SELL 1,042건 = metrics 일치 · `cash_after` 음수 0건 · PnL 샘플 10건 OK · BUY 1,045(기말 미청산 3건 추정)
- **다음 착수:** Phase B — 사이징·유령 tracked·로직 diff 감사

### 2026-06-01 (**v4.0** Portfolio Validation Roadmap — 문서화)
- **`docs/작업지시서-v4.0-Portfolio-Validation.md`:** Phase A~G 순차 작업지시 · main/GUI 병합 보류

### 2026-06-01 (**v4.0** Portfolio Manager Engine)
- **`src/engine/portfolio_manager.py`:** 일자별 시뮬레이션 루프 · Total Equity 3천만 · Max Slots 3 · 슬롯/현금 부족 시 진입 Pass · 보유 포지션 청산(+3.5%/3일 타임스탑) 우선 처리 후 진입 · Equity Curve/MDD 산출
- **`run_v4_portfolio.py`:** `.env` 로드(KRX ID/PW) · 벌크 로드 강제 · `outputs/v4_equity_curve.csv` 자동 저장 · 승률/프로핏팩터/수익률/MDD 출력
- **Fix:** 거래대금(종가×거래량) `int32` 오버플로우 방지(float64 캐스팅)로 유니버스 0건 문제 해결

### 2026-06-01 (**v4.0** Smart Money Cascade Engine)
- **`src/engine/smart_money_cascade.py`:** Pass0~1 유니버스(1,500억·Top20) · 1~4회차 종가 매수(MA3/5/10/20+거래량 건조) · 익일~3영업일 +3.5% 익절/타임스탑 · 비용 0.00215 · 미청산 중복 진입 차단
- **`run_v4_test.py`:** 프로젝트 루트 `sys.path` 주입 · `src.data_loader._load_ohlcv_pykrx_by_date` · LG전자 첫 스마트머니 기준봉 연쇄 리포트
- **인수:** `python run_v4_test.py` 단일 실행 · 1회차 익절 후 2~3회차 순차 레벨업(중복 진입 없음)

## 3. 보관 및 아키텍처 요약 (Harness §4)

- **과거 changelog 원문:** `docs/progress_archive.md` — 2026-05-21~05-20 · **2026-05-22~05-31**(Phase F, 2026-06-01)
- **main/GUI:** 단일종목 다음봉 시가 체결 · 매수 AND / 매도 OR · `settings.yaml` · 스크리너·차트 패닝 캐시
- **v4.0 브랜치:** `smart_money_cascade` + `portfolio_manager` · `v4_0.*` SSOT · `run_v4_tune.py` 튜닝 · Ruin 서면 `docs/v4_ruin_analysis.md` — **main 병합 없음**

---

변경 반영 후 **§2 맨 위**에 새 블록을 누적. 분량·Done 누적 시 위 아카이브로 이관.
