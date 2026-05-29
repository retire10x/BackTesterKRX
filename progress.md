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

## 2. 최신 변경 이력 (Changelog)

### 2026-05-29 (**v3.16** 차트 X축·패널 구분·휠 줌)
- **`backtest_chart.py`:** `_apply_chart_xaxis_price_panel_dates`(가격 패널 하단만 MM.DD·거래량 무 라벨·패널 간격 확대) · `_draw_price_volume_panel_divider` · `slice_chart_viewport` · `FIG_ATTR_PRICE_PANEL_XDATE`
- **`gui.py`:** 캔버스 `<MouseWheel>`/Linux Button-4·5 줌 · **줌 리셋** 버튼 · `_chart_canvas_state` 캐시 후 `render_backtest_chart_png_bytes` 재호출(output/ 미기록)
- **`gui.py` (v3.16 팬):** 확대 시 왼쪽 드래그 → 이동 중 `canvas.coords` 이미지만 이동 · 릴리스 시 `bar_shift` 반영·Y축 재렌더(드래그 중 PNG 생성 없음)

### 2026-05-29 (**v3.15** GUI 차트 이평선 토글 · 수익률 버튼 제거)
- **`gui.py`:** 차트 상단 **수익률** 체크박스 삭제 → **5·10·20·60·120일** 이평 토글(기본 ON) 가로 배치 · `stateChanged`→`_on_rules_refresh_chart` · `render_backtest_chart_png_bytes` 메모리 경로 유지
- **`backtest_chart.py`:** 기간별 MA 색·두께(5/10=0.8, 20=1.5, 60=1.0, 120=2.0) · `trend_ma_visible`+`line.set_visible` · 범례는 표시 중인 선만 · `_draw_cumulative_return_overlay` 제거
- **`metrics.py`:** `prepare_chart_trend_ma` · `show_return_overlay` 파이프라인 제거 · 5중 이평 기본 ON
- **`gui_helpers.py`·`config/settings.yaml`:** `show_return_overlay` 폐기 · `show_trend_ma5~120` 기본 true

### 2026-05-27 (**v3.1** 최종 배포 — 리스트 시총·대금·PNG 억제)
- **리스트:** 스캔 결과에 시가총액·당일 거래대금 컬럼 추가(억 단위 반올림, 극대 시총은 `N천억` 표기)
- **I/O:** GUI 백테스트 연기 차트는 `materialize_backtest_chart_png_bytes` 로 캔버스만 갱신 · 차트 전용 경로는 기존처럼 메모리 PNG(`render_backtest_chart_png_bytes`)
- **머지 준비:** `main` 통합 시 상기 항목과 DoD(스캔·내비 반복 시 `output/` 신규 차트 PNG 없음)로 검증 권장

### 2026-05-27 (**v3.1** 스캔 0건 긴급 검증/보정)
- **원인 보정:** `gui._run_v3_overnight_scan` 의 `universe_limit=200` 하드컷 제거 → KOSPI 전수 스캔(`0=unlimited`)으로 변경
- **디버그 출력:** 스캔 시점마다 `Target/Prev_1/Prev_2` 날짜, 임계값(Vol/Return/Tail), 단계별 생존 수를 터미널 출력 + `output/v31_scanner_debug_log.txt` 저장
- **검증 결과(2026-05-27):** Total 948 / Pass1 212 / Pass2 11 / Pass3 5 (최종)

### 2026-05-27 (**v3.1** 더블클릭 차트 전용 모드)
- **동작 전환:** `list_codes`·`list_history` 더블클릭 라우팅을 백테스트 실행에서 `차트 전용 렌더(_run_chart_only)`로 전환
- **패닝 일관성:** `[⏪][◀][▶][⏩]`·단축키 `1/2/7/8` 기간 이동 시에도 동일 차트 전용 경로만 호출(리스트 재스캔 없음)
- **렌더 방식:** `load_ohlcv` 기간 데이터 + **`render_backtest_chart_png_bytes`(디스크 저장 없음)** 로 우측 차트 갱신

### 2026-05-27 (**v3.1** Overnight Scanner GUI 전면 개편)
- **코드 프리즈 준수:** `src/v3_signal_generator.py`·`src/v3_execution_engine.py` 수학 로직/비용 체계 미변경(`SELL_COST=0.0020` 유지)
- **스캐너 연동:** `gui.py` 검색/하단 실행 버튼을 v3.1 오버나이트 스캐너로 통합(종료일 기준 유니버스 스캔 후 `코드 | 종목명 | 당일상승률` 리스트 표시)
- **화면 리팩토링:** 우측 상단 매수/매도 규칙 패널 완전 제거, 우측 상단에 현재 선택 종목 라벨 추가, 차트/내비 공간 상단 확장
- **레거시 자산 유지:** 차트 내비 버튼([⏪][◀][▶][⏩])과 키보드 단축키(`1`,`2`,`7`,`8`) 유지; 기간 이동은 우측 차트만 갱신하고 좌측 리스트는 고정
- **로그 정리:** 좌측 성과 텍스트 박스 비활성화 및 실행 버튼명을 `오버나이트 주도주 스캔`으로 교체

### 2026-05-27 (**v3.0** Code Freeze · 다중 기간 백테스트 · 배포 준비)
- **Freeze:** `src/v3_signal_generator.py`, `src/v3_execution_engine.py` 로직 변경 금지 확정 · `SELL_COST=0.0020` 재확인
- **CLI:** `merge_v3_cli_into_config` — `--start`/`--end`로 기간만 덮어쓰기 · 실행 시 BUY/SELL 비용 로그 출력
- **검증:** 세션 A(2025-05~2026-05) PF 2.51 / B(2022 하락) PF 1.10 / C(2023~2025 3년) PF 1.28 · 보고서 `output/v3_multi_period_report.md`

### 2026-05-27 (**v3.0** Overnight Scalper — 브랜치 공식 수립)
- **레거시 폐기:** `docs/작업지시서-v2.0-Intraday-Gap-Scalper.md`, `src/v2_*.py` 삭제
- **신규:** `src/v3_signal_generator.py`, `v3_execution_engine.py`, `v3_metrics.py`, `load_v3_0_overnight_scalper_data`
- **CLI:** `python main.py --mode cli` → v3.0 OVERNIGHT 대시보드만 출력
- **문서:** `docs/작업지시서-v3.0-Overnight-Scalper.md`

### 2026-05-27 (**v2.0** Sign-off — quiet CLI·SRS 동기화·인수 마감) [v3.0에서 폐기]
- **`v2_signal_generator.py`:** `verbose=False` 기본·종목별 로그 옵션화
- **`main.py`·`config/settings.yaml`:** `v2_0.quiet_signal_log: true` — 파이프라인 요약 1줄 + PERFORMANCE REPORT
- **`docs/작업지시서-v2.0-Intraday-Gap-Scalper.md`:** [3단계] Open→Close Bias-Free 플랫 엔진 명세로 동기화
- **레거시:** `기본 도구.txt`·`백테스팅 핵심 지식 베이스 리스트.txt`·`backtest_smart_money`/`backtest_trend_following` — 저장소 내 미존재(이미 정리됨)

### 2026-05-27 (**v2.0** Intraday Gap Scalper 요구사항 문서화)
- **문서:** `docs/작업지시서-v2.0-Intraday-Gap-Scalper.md` 신규 작성(4단계 로드맵 + 인수 조건 + 정합성 주의사항 포함)
- **진행:** `progress.md` Feature Checklist에 v2.0 구현 태스크 5개 추가
- **보정:** `vol_ratio(=전일/전전일 거래량)`로 정합 고정 및 `SELL_COST=0.20%`로 통일
- **구현 착수:** `main.py`에 v2.0 CLI(데이터로더 검증) 분기 추가 + `src/data_loader.py`에 pykrx 기반 v2.0 Data Loader 구현
- **구현:** `src/v2_signal_generator.py` 신규 생성 및 `main.py --mode cli` 파이프라인에 Signal Generator 연동
- **구현:** `src/v2_execution_engine.py` 당일 Open→Close 플랫 청산·`trade_return` 계산 및 CLI 파이프라인 연동
- **구현:** `src/v2_metrics.py` 성과 집계·`print_v2_dashboard` 및 CLI 파이프라인 최종 연동

### 2026-05-25 (**v4.16_Patch** 김직선 3단계 거래량·고가돌파 창 완화)
- **`stock_screener.py`:** 기준봉 포함 20영업일 거래량이 **평균 대비 ≥300%** 이거나 순위 **TOP3** 일 때 세력 기준봉 인정. 고가 돌파(종가 확인) 허용 `τ ∈ [T-3,T]`, 종가 기준 고가 유지 **`kim_breakout_age_trading_days`**, 패턴 레이블 `고가돌파 (경과일: N일)`. **`pipeline_screener_pick_sort_tuple`** 로 시총 정렬 후에도 고가·경과 우선 표시 일치.
- **`gui.py`:** `_screener_list_sort_key`·창 제목 v4.16_Patch.

### 2026-05-24 (**v4.15** 매매 패널 추출 헬퍼 단일화 · 골든 OFF 시에도 2단계 필터 종봉 AND)
- **`gui_helpers.py`:** `merge_live_trade_panel_into_strategy` 로 interval·이평(백테: 검증된 값)·골든/데드·진입 필터·가변 낙폭까지 한 경로 반영; `extract_live_strategy_config`(검색: 이평 클램프 + 실패 시 `RuntimeError`).
- **`try_build_config`:** 차트 플래그 전에 동일 헬퍼 호출하여 중복 제거.
- **`stock_screener.py`:** `run_buy_stage_screen = stage_buy_rules` 만으로 OHLC·`_pipeline_buy_rules_terminal_qualifies` 실행. `_pipeline_buy_rules_terminal_qualifies` 는 골든 ON→`Signal==1` 필요, 골든 OFF→Signal 생략·`_buy_filters_pass` 만 엄격 적용.
- **`gui.py`:** 검색 스냅샷은 `extract_live_strategy_config`; 창 제목 v4.15.

### 2026-05-24 (**v4.14_Patch** 스크리너 2단계 strategy 누수 수정)
- **원인:** `execute_pipelined_screening(..., strategy_st=load_config()['strategy'])` 만 사용해 **디스크 YAML** 기준으로 종봉·골든·진입 필터를 평가함. 사용자가 우측 패널에서 체크를 바꿔도 검색 결과(특히 1단계+2단계)가 거의 안 바뀌는 **GUI↔백엔드 단절**이었음(.py 내 `_buy_filters_pass` 무조건 True 버그 아님).
- **`gui_helpers.py`:** 패치 당시 `live_strategy_blob_for_pipeline_search`; v4.15에서 `extract_live_strategy_config`(코어 `merge_live_trade_panel_into_strategy`)로 통합.
- **`gui.py`:** 검색 시작 직전 스냅샷 후 워커에 `strategy_st` 전달. 창 제목 v4.14_Patch.
- **`stock_screener.py`:** `strategy_cross_flags_from_cfg` 결과에 대해 `golden_buy_enabled` 를 **`.get(..., True)` 없이 명시 불리언**으로 사용(폴백 혼선 제거).

### 2026-05-24 (GUI: 검색 결과 100건 재잘림 제거 · 파이프라인 체크 인터락 해제)
- **원인:** `execute_pipelined_screening` 은 시총 1단계만 켠 경우 `disp_cap`≥100으로 반환했으나, `update_gui_with_screener_results` 가 YAML `top_n`(예: 30)으로 `_screener_display_cap` 을 두고 `packed[:limit]` 재슬라이스해 목록을 30으로 맞춤.
- **`gui.py`:** 엔진이 돌려준 픽 리스트 전량 표시(**GUI 재상한 폐기**), `_screener_display_cap` 속성 및 `_search_screen_universe_params` 대입 삭제. 파이프 체크 3개 `trace` 에서 검색 결과 지우던 `_on_pipeline_filter_changed` **제거**(체크만 바꿔도 기존 리스트 유지).
- **`config/settings.yaml` · `stock_screener.default_screener_config`:** `universe.screener.top_n` 기본 **100**(1단계 Top 100 레이블·`PIPELINE_MC_TOP_N_DEFAULT` 와 정합).
- **`main.py`:** CLI 스크리너 배치 시 `top_n` 폴백 리터럴 100.

### 2026-05-24 (GUI·YAML: 매매 규칙 체크박스 전부 기본 OFF)
- **`gui.py`:** 우측 매매 규칙 패널 — 골든/데드·대세·돌파·시간버퍼 BooleanVar 초기값 `false`. 초기화 말미 `var_filter_trend.set(True)` 제거로 `apply_yaml_to_widgets` 가 설정한 `filter_trend_slope` 가 더 이상 덮어쓰이지 않음; 인터락은 `_sync_buy_filters_interlock()` 만 호출.
- **`config/settings.yaml`:** `golden_buy_enabled`·`dead_cross_sell_enabled`·`filter_breakout_strength`·`filter_time_buffer` 기본 `false` (트레일·곡선 가속도는 기존대로 `false`).
- **`metrics.py`:** `strategy_cross_flags_from_cfg` 에서 위 스위치 키 생략 시 폴백을 `False` 로 통일.

### 2026-05-23 (퀀트 파이프라인 패치 v4.14_Fix)
- **`stock_screener.py` (`execute_pipelined_screening`):** 레거시 **시총 3000억 하한**으로 OHLC 단계 후보 탈락 제거. `stage_mcap_top100`일 때 YAML `top_n`(기본 30)만으로 결과가 상단 슬라이스 되던 버그 수정. 2단계 체크+**골든 매수 OFF** 시 빈 결과·무의미 조기종료 제거 — **골든 ON일 때만** 종봉 매수 규칙 게이트(`effective_buy_rules`) 적용·그 외 바이패스.
- **`gui.py`:** 창 제목 v4.14_Fix.

### 2026-05-23 (퀀트 필터 파이프라인 통합 v4.14)
- **`stock_screener.py`:** `execute_pipelined_screening` — 유니버스 후 (선택)`_narrow_universe_by_mcap_top`(Marcap 순위와 교집합)→(선택) 스레드 풀 OHLC·터미널 매수 규칙(`_pipeline_buy_rules_terminal_qualifies`)→(선택)`_evaluate_kim_line_one_bar_pattern`; 결과는 `PipelineScreenerPick` 로 정규화.
- **`gui.py`:** 스크리너 라디오 제거·3단계 체크박스; 검색 데몬 스레드는 위 함수 단일 호출·`format_gui_list_pipeline`; 창 제목 v4.14.
- **`gui_helpers.py`:** `universe.screener_pipeline` 저장/로드·레거시 `screener_mode` 마이그레이션.
- **`config/settings.yaml`:** `screener_pipeline` 블록·`screener_mode` 주석 갱신.

### 2026-05-23 (김직선 1봉 캔들 스크리너 v4.13)
- **`stock_screener.py`:** `screen_universe_kim_line_one_bar`·`_evaluate_kim_line_one_bar_pattern`·`KimLineOneBarPick`(고가돌파/중심선지지·기준봉 거래대금·기준선 대비 이격도).
- **`gui_helpers.py`:** `GUI_SCREENER_MODE_KIM_LINE_1BAR`·`format_gui_list_kim_candle`.
- **`gui.py`:** 라디오·검색 워커·확장 컬럼·정렬. 창 제목 v4.13.
- **`config/settings.yaml`:** `screener_mode` 주석에 `kim_line_1bar`.

### 2026-05-23 (스크리너 당일 타점 추적 v4.12_Beta)
- **`stock_screener.py`:** 독립 함수 `screen_universe_entry_event`(기존 랭킹 루틴 비침범)·`EntryEventTrackPick`(신호 경과일·종가대비 이격도·시총). 엔진 `metrics.strategy_entry_filters_from_cfg` / `simulate._buy_filters_pass` 재사용.
- **`gui_helpers.py`:** `GUI_SCREENER_MODE_ENTRY_EVENT`(`entry_event_track`)·리스트 줄 `format_gui_list_entry_event(...)`.
- **`gui.py`:** 라디오「당일 타점(Event) 추적」·검색 워커 분기·스크린 결과 정렬·2열 표시 조건 분기·`parse_gui_list_row_code` 호환 유지.
- **`config/settings.yaml`:** `universe.screener_mode` 허용 값 주석 확장.

### 2026-05-23 (백테스트 속도·차트 분리 v4.10)
- **`data_loader.py`:** `fdr_stock_listing` 결과 메모리 캐시(TTL)·`load_ohlcv` LRU(최대 96건); `clear_ohlcv_cache()`.
- **`metrics.py`:** `defer_chart_render`·`write_signal_debug_log`·`BacktestResult.chart_render_pending`·`materialize_backtest_chart_png()` — 시뮬·성과와 mpl PNG·검증 로그 분리.
- **`simulator.py`:** 시뮬 본 루프에서 Open/High/Close/Signal ndarray 직접 인덱싱.
- **`gui.py`:** 창 제목 v4.10, 수익률 체크 기본 OFF, 백테스트 워커는 defer 후 메인에서 캡처한 `chart_render_px` 로 차트 전용 스레드 PNG 생성.
- **`config/settings.yaml`:** `show_return_overlay: false` 기본.
- **`scripts/benchmark_backtest_v410.py`:** 동기 vs defer+materialize 초 단위 비교 출력.

### 2026-05-23 (차트 더블버퍼·로딩 표시 v4.11)
- **`gui.py`:** 창 제목 v4.11. `_update_chart_image` 는 단일 `canvas` image item 에 `itemconfigure` 로 비트맵 교체해 선삭제 백색 플래시 제거·첫 회·오류만 `delete('all')`. defer PNG 생성 대기 때 캔버스 플레이스홀더 메시지 제거(기존 차트 유지). 기간 줄 `lbl_chart_loading` Braille 애니 + `_chart_materialize_ticket` 디스패치로 오래된 백그라운드 결과 무시.

### 2026-05-23 (반응형 차트 패널 v4.9)
- **`gui.py`:** 우패널 `grid_propagate(False)` 로 Row2(weight=1) 차트 높이가 규칙 패널의 요구 높이에만 종속되지 않도록 교정 `_raw_chart_overlay_measured_size` 로 픽셀 다단 추정 `_defer_chart_image_paint` 로 초기 한 번 재리페인트 백테스트 시작 직전 `chart_render_px` 를 `run_backtest_detailed(..., chart_render_px=...)`.
- **`metrics.py`:** `chart_render_px` / `chart_render_dpi`(기본 100) 시 mpl 인치=`px`/dpi 저장 `layout_preset=gui_target`. 미지정이면 기존 12×7 inch 및 300 DPI.
- **`backtest_chart.py`:** 선택 `figsize`·인치별 폰트 RC 스케일 `save_figure_as_png(..., layout_preset=report|gui_target)`.

### 2026-05-23 (차트 네비 v4.8·이력 독립)
- **`gui.py`:** 차트 ⏪~기간 줄 **우측**에 **[ ] 수익률** 체크(툴팁 유지)·별도 행 제거. 최근 실행 이력 **4컬럼**(시장 포함)·메인 시장 드롭다운 변경과 **무관한 시총 표시**(행별 상장 시장으로 FDR 표 조회)·`backtest_history.json` **version 3**. 이력 더블클릭/`silent_try_build`+`market_override`로 목록 검증 우회 후 **종목폼 동기화**·패닝/Refresh 시 **`_last_run_listing_market`** 사용。
- **`gui_helpers.try_build_config`:** `market_override`; `period_nav` 시 마지막 성공 실행 시장 우선(`_last_run_listing_market`).
- **`metrics`:** 교차 시장 **종목명 조회 폴백**·목록 불일치 시 **OHLCV 로드 허용** 시 코드를 표시명으로 사용·`normalize_krx_listing_market`로 `universe.market` 정규화。
- **`data_loader.normalize_krx_listing_market`**·**`load_ohlcv` 주석**(티커 로드 시장 무관)·**`simulator` 헤더** 보강。

### 2026-05-23 (차트 v4.7 — 3층 패널 제거·텍스트 고저 수익률·음영 오버레이)
- **`backtest_chart.py`:** mplfinance 패널 **가격+거래량**만 유지(`6:2`·단일 패널). 하단 독립 **누적 수익률 패널** 삭제. `show_return_overlay` 시 **`twinx` + `fill_between`**(#56b4e9, alpha 0.1, zorder 후면).
- **`metrics.py`:** `summary_rows`에 **「최고/최저 수익률」** (`ret_series` min/max 부호 포함). 레거시 `show_chart_return` 차트 플래그는 **무시**. PNG 저장 후 **`plt.close(fig)`**.
- **`gui.py` / `gui_helpers.py`:** 차트 버튼 아래 **`주가 차트에 수익률 음영 표시`** 체크(기본 **OFF**)·YAML `strategy.show_return_overlay`. 토글 시 **`_on_rules_refresh_chart`** 디스패치.`try_build`에서 구 `show_chart_return` 키 **제거**(`pop`).
- **문서·`config/settings.yaml`:** `README`/`docs`/진행표 갱신.

### 2026-05-23 (GUI 종결: 차트 contain 제거 · 프레임 강제 resize · 초기 idletasks)
- **`gui.py`:** `ImageOps.contain` 폐기. `(fw,fh)`에 PIL **`resize`(LANCZOS)** 후 `CTkImage(size=(fw,fh))`. RGB 변환. 초기 레이아웃: **`_update_chart_image`** 최상단 `self`·`chart_frame`·`chart_overlay_host` 연속 **`update_idletasks`** 후 `winfo`/리사이즈.

### 2026-05-23 (GUI: 우측 패널 세로 가중차트·contain 여백·overlay pack)
- **`gui.py`:** 우측 `grid` Row0·1 `weight=0`·sticky `new`, Row2 `weight=1`·minsize 로 규칙·플레이어는 고정·세로 잉여 전부 차트행. `chart_frame` 안 `chart_overlay_host` **`pack(fill=both,expand,pad 5)`**, `lbl_chart` 동일 pack. 차트 타깃: 실측에서 **−20(px 가로)·−40(px 세로)** 후 `contain` 최소 300×200.

### 2026-05-23 (GUI: 메인 창 초기 크기 · 모니터 중앙 정렬)
- **`gui.py`:** 시작 시 `1400×850`(상수) 목표, `winfo_screen`/여백 클램프 후 `geometry(WxH+X+Y)` 중앙 배치·`minsize`는 목표 최소와 실제 초기 크기 중 작은 값으로 완충.

### 2026-05-23 (GUI: 백테스트 이력 리스트 = 검색 리스트 규격 + 시총)
- **`gui.py`:** 이력 `Listbox`를 검색 결과와 동일 `height=7`·폰트·스크롤 패턴으로 통일. 한 줄 형식 **`티커 | 종목명 | 시총`** (`format_gui_list_triple`). 이력 데이터는 `(코드, 이름, 저장 시점 시총|None)` 트리플; 디스크 `backtest_history.json` **version 2**·하위 호환 v1 2항목 로드.
- 시총: 백테스트 완료 시 **현재 GUI 시장**의 `fetch_listing_market_cap_krw_by_code`로 스냅샷 저장; 저장값이 없으면 목록 갱신 시 상장표로 보완(검색 리스트 시총과 동일 FDR 근거, ETF 포함).
- 이력 더블클릭 시 종목명이 있으면 검색 결과와 동일하게 키워드 입력에 반영 가능.

### 2026-05-23 (GUI: 매매 규칙 패널 Refresh — 조건 반영 차트 재생성)
- **`gui.py`:** `매매 규칙 · v4.6` 헤더 행 우측에 **Refresh** 버튼. 클릭 시 활성 종목(`current_code`)·현재 DateEntry 기간·좌패널 수수료 등과 **우패널 매수·매도 조건·곡선 가속도**로 `try_build_config` 후 `run_single_backtest` 재실행(차트 PNG 갱신). 백테스트 진행 중에는 버튼·실행 버튼 동일 비활성.

### 2026-05-23 (GUI: 규칙 패널 매수/매도 카드 동일 행 재배치)
- **`gui.py`:** `grid_container` 를 1열 세로 스택에서 **매수 좌 · 매도 우** 동일 행(`uniform` 2열)으로 복구. 카드 안쪽 매수/매도 각 **2단 행** 필터 레이아웃은 유지.

### 2026-05-23 (GUI 저해상도 반응형 레이아웃 재구성 — 시뮬·체결 규칙 무변)
- **`gui.py`:** 좌측 — 시장·종목 입력 1행, **검색** 버튼 다음 행 `columnspan`/전폭; 검색 결과 위 테스트 라벨 제거; 시작/종료일 컴팩트 격자+가상 원금 동행; 이력 **삭제**는 리스트 위 툴바 우측, 리스트 `sticky`/전폭. 우측 — 매수·매도 필터 각 **2단 행** 배치(규칙 카드는 이후 요청으로 동일 행 2열). 차트 — `chart_frame`+`chart_overlay_host` **`Configure`** 디바운스 리페인트, 실측·캐시 폴백으로 contain 타깃 보정; 창 `minsize` 완화.
- 시뮬레이터·다음 봉 시가 체결 로직 변경 없음 (Harness Zero Tolerance 유지).

### 2026-05-23 (문서: Harness §4 — `progress.md` 청소)
- **`docs/progress_archive.md`:** 2026-05-21 ~ 2026-05-20 changelog 원문 이관(백업·누적).
- **`progress.md`:** §3 보관 안내 및 아키텍처 명사형 요약 추가로 본문 경량화.

### 2026-05-22 (GUI: 검색 중 버튼 비활성·대기 커서)
- **`gui.py`:** 검색 워커 시작 전 `검색` 버튼 비활성 + 최상위 창 `cursor=wait`; 완료·실패 콜백에서 버튼·커서 원복.

### 2026-05-22 (곡선 가속도: MA20 단기 OLS>0 완화 · GUI 체크박스 · 백테스트 버튼 리스트 보존)
- **시뮬 (`simulator.py`):** `use_slope_acceleration` 판정을 MA20 vs MA120 비교에서 **최근 5봉 MA20 OLS 기울기 > 0** 단일 조건으로 변경.
- **GUI (`gui.py`):** 매수 진입 스트립에 **곡선 가속도** 체크박스(`check_slope_accel_var`), `run_single_backtest(..., use_slope_acceleration=…)`로 단일 종목만 실행·`screener.enabled` 강제 OFF. **「백테스트 실행」** 은 더 이상 스크리너 일괄을 호출하지 않아 `list_codes` 가 지워지지 않음(검색 전용 경로만 리스트 갱신).
- **헬퍼 (`gui_helpers.py`):** YAML `strategy.use_slope_acceleration` → 체크박스 초기값.
- **설정·로그:** `config/settings.yaml` 주석, `metrics.py` 로그 문구 정합.

### 2026-05-22 (곡선 가속도 검증: ablation 배치 TSV — 이후 엔진은 MA20 OLS>0 로 갱신)
- **메트릭 (`metrics.py`):** `omit_report_artifacts=True` 시 PNG·디버그 생략(유니버스 배치 경량화).
- **배치 (`slope_ablation_batch.py`):** `main.py --slope-ablation-batch` 로 종목별 `baseline`/`slope_accel` 2회 → `output/slope_ablation.tsv`·출력 요약. `--batch-max-workers N`.
- **설정:** `strategy.use_slope_acceleration`, `universe.slope_ablation_batch` (`config/settings.yaml`).

### 2026-05-22 (스크리너: 눌림목형 랭킹 — 고점 낙폭·거래량 건조 지표 융합)
- **스크리너 (`stock_screener.py`):** 후보별 최근 lookback 내 **고점 대비 종가 낙폭(%)**, 말단 대 직전 구간 **평균 거래량 비율**로부터 **거래량 건조(%)** 계산 후, 변동성·거래대금과 함께 **백분위 순위 4개 평균**으로 `combined_score`. 낙폭은 `pullback_rank_cap_pct`(기본 35) 클램프 후 순위화해 과도 낙폭에 상대 패널티.
- **설정·출력:** `universe.screener.pullback_rank_cap_pct` (`config/settings.yaml`). `output/screener_last.tsv`·CLI 표에 고점낙폭·거래량건조 열 추가 (`gui.py`, `main.py`).
- **데이터 (`data_loader.py`):** `ensure_datetime_index` 재도입 및 `fetch_listing_market_cap_krw_by_code` 에 잘못 붙어 있던 무효 분기 제거 — `stock_screener`/`metrics` 임포트 복구.

### 2026-05-22 (GUI: 검색·스크리너 비동기)
- **`gui.py`:** `screen_universe`·`fetch_filtered_universe` 를 `threading.Thread` 워커에서 실행하고, 완료 시 `after(0, …)` 로 리스트박스만 메인 스레드에서 채워 **탐색 시 창 응답 없음 현상 완화**. 진행 중 `self._busy` 로 이중 검색·백테스트 동시 시작 방지.

### 2026-05-22 (GUI·YAML: 005930 기본 종목 제거 및 차트 패닝 종목 고정)
- **`config/settings.yaml`:** 기본 `universe.selected_code` 를 비움 → 특정 종목 하드코딩 없음.
- **`gui.py`:** 성공 시 `_last_active_stock_code` 갱신·`current_code` 프로퍼티. 기간 패닝 재실행은 `period_nav=True` 로 YAML/빈 리스트에 의한 종목 전환 방지, 기존 OHLCV 캐시로 API 재호출 최소화.
- **`gui_helpers.try_build_config`:** `period_nav` 및 **「종목을 선택하세요」** 안내로 미선택 백테스트 차단.

### 2026-05-22 (통합 지시서: 스크리너 하드필터·차트 네비 캐시·Harness 매수 AND 옵션)
- **스크리너 (`stock_screener.py`, `data_loader.py`):** 스코어링 전 FDR 상장표 `Marcap` 기준 `min_market_cap_krw`(기본 3000억 원 근사) 필터 · 일봉 MA20 대 MA120·이평 OLS 우상향 OR 필터(`hard_ma_pair_trend_filter`). ETF 시장은 시총 필터 생략.
- **GUI (`gui.py`):** 일반 백테스트 시 종목·기간별 일봉 장기 버퍼 캐시 → 차트 패닝 시 `run_backtest_detailed(..., ohlcv_preloaded_daily=…)`로 재다운 최소화(시작 warmup·종료 포함 커버 검증).
- **메트릭·시뮬 (`metrics.py`, `simulator.py`):** `strategy.harness_buy_all_three_and`(기본 false) 시 골든 후 대세·돌파강도·시간버퍼 **무조건 AND** 진입 로그 명시 매도 OR 문구 동기화.
- **설정:** `universe.screener.min_market_cap_krw`, `hard_ma_pair_trend_filter` · `strategy.harness_buy_all_three_and` 추가.

## 3. 보관 및 아키텍처 요약 (Harness §4)

- **과거 changelog 원문:** `docs/progress_archive.md` (2026-05-21 ~ 05-20 이관)
- **상태 명사형:** 단일종목 다음봉 시가 체결 · 매수 필터 AND / 매도 OR · 파라미터 `settings.yaml` · GUI 단일 백테스트·스크리너 목록 · 차트 패닝 OHLCV 캐시 · 규칙 UI v4.6

---

변경 반영 후 **§2 맨 위**에 새 블록을 누적. 분량·Done 누적 시 위 아카이브로 이관.
