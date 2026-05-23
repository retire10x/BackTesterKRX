# 📈 프로젝트 진행 일지 (Progress Log)

## 1. 핵심 기능 체크리스트 (Feature Checklist)
- [x] 기본 이평선(MA{N}) 골든/데드크로스 신호 생성 로직 (`strategy.py`)
- [x] 다음 봉 시가 체결 및 거래 비용(설정 가능·기본 매수 0.015% / 매도·세금 0.18%) 반영 시뮬레이터 (`simulator.py`, GUI·YAML 연동)
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
- [x] **v4.9** 디스플레이·창 크기 반응형 차트 패널(우패널 `grid_propagate(False)`·실측 추정·지연 리페인트 + `metrics.chart_render_px` → mpl `figsize`/DPI·`gui_target` 여백) (`gui.py`, `metrics.py`, `backtest_chart.py`)

## 2. 최신 변경 이력 (Changelog)

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
