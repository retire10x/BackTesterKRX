# Progress changelog archive

`progress.md`(Harness §4) 비대 방지용 백업. 최근·v4.0 Portfolio Validation changelog는 루트 `progress.md`를 참고하세요.

| 이관일 | 구간 | 비고 |
|--------|------|------|
| 2026-05-23 | 2026-05-21 ~ 05-20 | 아래 첫 블록 |
| 2026-06-01 | 2026-05-22 ~ 05-31 | [이관 구간: 2026-05-22 ~ 2026-05-31](#이관-구간-2026-05-22--2026-05-31) |
| 2026-06-09 | 2026-06-01 ~ 06-06 | [이관 구간: 2026-06-01 ~ 2026-06-06](#이관-구간-2026-06-01--2026-06-06) |
| 2026-06-14 | 2026-06-08 ~ 06-09 | [이관 구간: 2026-06-08 ~ 2026-06-09](#이관-구간-2026-06-08--2026-06-09) |

---

## 이관 구간: 2026-06-08 ~ 2026-06-09

### 2026-06-09 (**v6.19**) 대시보드 자산 성장 곡선 Y축 라벨 정밀도 보정
- **Y축 라벨:** `Math.round` 절사 방식에서 `toLocaleString()` 소수점 정밀 표기 방식으로 변경
- **효과:** 1,000만 원 단위 이하의 미세한 자산 변동(예: 1,004.8만)을 차트에서 즉시 식별 가능
- **변경 파일:** `src/web/static/index.html`

### 2026-06-08 (**v6.18**) 대시보드 유니버스 테이블 네이버증권 핫링크 탑재
- **유니버스 후보 종목 테이블:** `관제` 컬럼 신설 — `📊 N네이버증권` 버튼 (새 탭 `target="_blank"`)
- **URL 바인딩:** `https://finance.naver.com/item/main.naver?code={code}` 동적 생성 (6자리 패딩)
- **CSS:** `.btn-naver-link` (네이버 그린 `#02c75a`) + `.naver-n` (백색 N 뱃지) 컴포넌트 추가
- **변경 파일:** `src/web/static/index.html`

### 2026-06-08 (**v6.17**) 스캐너 5대 추세 철벽 필터 복원 (MA20 우상향 긴급 복원)
- **`_verify_strict_pre_filter`:** MA20 우상향(`ma20[-1] > ma20[-2]`) 조건 추가 — 3대 이평선(MA20·MA60·MA120) 동시 우상향 릴레이 검증 완성
- **`min_history_bars` 기본값:** 120 → 122 (신규 상장주 방어 강화, `.iloc[:-1]` 확정 기준)
- **5대 조건 AND 체인:** ①`len >= 122` ②MA20↑ ③MA60↑ ④MA120↑ ⑤`종가 > MA60`
- **변경 파일:** `src/live/live_screener.py`

---

### 2026-05-21 (긴급: 스크리너 MA120 역배열 검증 순서 고정·차트 저장 여백/표시 레이아웃)
- **스크리너 (`stock_screener.py`):** 정제·오름차순 정렬 후 `rolling(120)` 로 `MA120` 계산하고, 종료일 캘린더 마스크로 자른 다음 `dropna(["MA120","Close"])` 의 **마지막 행**으로 `종가 < MA120` 이면 `None`/콘솔 `[SCREENER INTERCEPT]` 로 탈락. 동일 종료 구간 프레임만 `_daily_metrics_slice` 재사용.
- **차트 (`backtest_chart.py`, `gui.py`):** `save_figure_as_png` 에서 `tight_layout` 후 `subplots_adjust(left=0.05, right=0.92, top=0.93, bottom=0.12, hspace=0.34)` 및 무인자 `autofmt_xdate`, `bbox_inches='tight'` 제거로 가장자리 재잘림 방지; mplfinance 호출 시 `scale_padding↑`, `tight_layout=False`; GUI는 `ImageOps.contain` 으로 비율 유지 피팅.

### 2026-05-21 (긴급: YAML 검색어로 인한 초기 검색창 ‘삼성’ 잔존 수정·스크리너 역배열 필터 재강화)
- **`config/settings.yaml`:** 기본 `universe.search_keyword` 를 `""` 로 변경. `apply_yaml_to_widgets()` 가 시작 시 검색 입력을 채워 기존 `삼성` 문구가 Entry에 되살아나던 근본 원인을 제거했습니다 (`gui_helpers.py`).
- **`src/stock_screener.py`:** 종료일 슬라이스 후 OHLCV 수치 강제·동일 타임스탬프 중복 인덱스 정리 (`keep='last'`). MA120 차단은 `rolling` 의존보다 **마지막 120일 종가의 산술평균 vs 종가 `<` 단일 비교**로 고정했고, ATR·거래대금은 동일하게 잘린 `z_prefetched_end` 프레임만 사용해 재슬라이스 불일치를 제거했습니다.

### 2026-05-21 (스크리너 미선택 시 종목명 입력 필수 조건화 및 초기 검색어 공백 처리 완료)
- **내용:** 프로그램 최초 실행 시 종목 검색 입력창 기본값을 비웠고, 「종목 스크리너」가 해제된 상태에서는 검색·백테스트 시 검색어가 비면 상태줄 안내 및 경고 후 진행을 막도록 했습니다. 이미 실행 종목 코드가 명확할 때(YAML selected_code·검색 결과 목록 선택·이력 오버라이드 등)는 검색어 없이도 단일 백테스트 가능합니다 (`gui.py`, `gui_helpers.py`).

### 2026-05-21 (스크리너 역배열 120선 차단 및 GUI 가변 레이아웃·PNG 축 레이블)
- **내용:**
  - **스크리너:** 종가 확정 분 기준 종가\<MA120(일봉 120 거래일 롤링) 종목을 변동성·거래대금 랭킹 전 제외하고, 필요 시 차단을 위해 스크린용 일봉 fetch 카렌더 구간 ±400일로 확대했습니다 (`stock_screener.py`).
  - **GUI:** 메인 창 열(weight)으로 우측 차트 영역이 창 크기에 맞춰 확장되도록 하고 고정 크기 패널·과도 패딩을 줄였으며, 차트 이미지 크기를 `chart_overlay_host` 실측 기준으로 리사이즈합니다 (`gui.py`).
  - **PNG:** `save_figure_as_png` 에서 `subplots_adjust` 및 `tight_layout` 처리 후 무인자 `fig.autofmt_xdate()` 로 날짜 라벨 자동 회전. `_apply_hts_style_xaxis` 에서 x축 0도 강제·라벨 루프를 제거해 저장 단계 회전과 충돌하지 않도록 조정했습니다 (`backtest_chart.py`).
  - **문서:** `README.md` 에 스크리너 역배열·400일 로드·GUI 최소 크기 및 차트 플로트 레이아웃을 요약했습니다(epic 반영).

### 2026-05-21 (종목 스크리너: 변동성·거래대금 상위 N 자동 필터링 후 배치 백테스트)
- **목표:** 전체 후보 종목마다 순회하기보다 종료일 기준 과거 확정 일봉만으로 최근 거래일 구간의 변동성(ATR 또는 일간 수익률 표준편차)·거래대금(Σ 거래량×종가)이 모두 상대적으로 큰 순으로 상위 M개만 선별해 순차 백테스트함(시점 왜곡 회피).
- **내용:**
  - **엔진:** `src/stock_screener.py` — 키워드·시장 후보별 미래 데이터 미사용, 변동성·거래대금 각각 순위 분위합으로 결합 순위 계산 후 `universe.screener` 의 `lookback_trading_days`·`top_n` 적용.
  - **파일 출력:** 매 실행 후 `output/screener_last.tsv` 에 선정 순위·스크린 수치 저장.
  - **GUI:** 「종목 스크리너(상위 일봉 N거래일)」체크 및 `atr14 | std_return` 선택; 활성 시 백테스트 버튼이 스크린→선정 종목 루프 실행. 차트 기간 패닝에 의한 자동 재실행은 스크립트 부하 방지 위해 스크리너 플래그를 무시한 단일 백테스트만 수행(`gui.py`).
  - **CLI:** `main.py --screener-batch` 로 테이블형 배치 결과 출력 및 동일 YAML 파라미터 사용.
  - **설정:** `config/settings.yaml` 의 `universe.screener` 기본 블록 추가.

### 2026-05-21 (좌측 입력창 재배치, ETF 시장·수수료 UI, 실행 이력 FIFO·더블클릭 실행)
- **목표:** 좌패널을 상단부터 실행 버튼까지 재정렬하고, 검색 결과·종목 이력에서 빠르게 재실행할 수 있도록 하며 거래 비용을 GUI에서 직접 조정 가능하게 한다.
- **수정 내용:**
  - **UI 제거:** 조회 주기(일·주)·시간축 이동(±30일) 라벨·버튼 및 연동 분기 코드 제거 (`gui.py`; 내부 변수 `var_interval=daily`는 YAML·차트 간격 호환 목적으로 유지).
  - **UI 비표시(엔진 기본값):** 매매 기준 이평(20)·추세 오버레이(20·120)·차트 패널(캔들·거래량·수익률)은 위젯 없이 변수만 초기화해 동일 규칙으로 동작하게 함.
  - **ETF:** 시장 선택 `KOSPI/KOSDAQ/ETF` 및 `FinanceDataReader.StockListing("ETF/KR")` 매핑 (`data_loader.py` `fdr_stock_listing`).
  - **수수료:** 시작·종료·원금 아래 매수/매도(세금 포함) % 입력 필드 추가, `try_build_config`가 `trading_costs.buy_cost/sell_cost`에 반영 (`gui_helpers.py`); 기본 YAML 매도 비용 `0.0018`(0.18%).
  - **이력:** 최근 실행 종목 deque(FIFO 최대 30)·맨 아래 줄부터 과다 시 제거·`삭제` 버튼으로 선택 행 영구 삭제·검색/이력 리스트 더블클릭 시 코드 오버라이드로 즉시 백테스트.
  - **`metrics.run_backtest_detailed`:** 종목 이름은 키워드 검색 결과에 없어도 동일 시장 전체 장전에서 조회해 검증 가능하도록 변경(키워드와 무관 재실행).
  - 후속 미세 조정: 좌패널 **가상 원금**을 매수·매도 수수료 입력과 동일 행 맨 앞에 배치, 라벨을 `가상 원금`, `매수 수수료(%)`, `매도 수수료(%)`로 통일함.
  - **이력 디스크 지속화:** 프로그램 정상 종료 시 `output/backtest_history.json` 에 최대 FIFO 30개 저장, 시작 시 같은 파일에서 복원 (`WM_DELETE_WINDOW` → 저장 후 `destroy`).

### 2026-05-21 (차트 마킹 시점 보정, 검증 로그 생성 및 내비게이션 이동 범위 축소)
- **이슈:**
  1. 차트 플로팅 시 매수/매도 화살표 마킹 시점이 체결 집행일(T+1일 시가) 캔들에 표시되어 전략 판단 신호가 발생한 날(T일 종가)과 불일치함. Y축 위치 또한 체결일 캔들 기준으로 표시됨.
  2. 차트 마킹 시점이 정상 보정되었는지 신호 발생일과 실제 플로팅 인덱스 날짜를 대조하여 개발자가 손쉽게 검증할 수 있는 디버그용 파일 추출 기능이 부재함.
  3. 차트 상단 내비게이션 버튼의 이동 단위가 주/월(30d, 7d)로 되어 있어 일일 탐색이 중요한 주식 백테스트에서 너무 넓게 이동함.
- **수정 내용:**
  - **차트 마커 위치 및 앵커 보정:** `backtest_chart.py`의 `_draw_trade_markers_matplotlib` 함수에서 체결일 인덱스(`bi_exec`)를 신호 발생 당일(`bi = bi_exec - 1`)로 변경하여 X축 및 Y축 앵커가 당일 캔들(최저가 Low 밑, 최고가 High 위)을 향하도록 보정 완료. 개별 거래 객체에 `marked_date`를 임시 저장함.
  - **마킹 시점 검증 데이터 자동 생성:** `metrics.py` 내에 백테스트 완료 시점에 `backtest_signal_debug.txt`를 프로젝트 루트에 UTF-8 인코딩으로 자동 저장하는 로직 구현. 종목 정보, 거래 종류/번호, T일 캔들의 특성(장대음봉/양봉/도지 등) 동적 분석 및 차트 마킹일 불일치 시 `[오류: 인덱스 1칸 밀림 발생]` 경고 문구를 덧붙여 출력함.
  - **차트 내비게이션 범위 축소:** `gui.py`에서 ⏪, ◀, ▶, ⏩ 버튼의 이동 영업일 단위를 각각 `(-7, -1, +1, +7)` 영업일로 변경하고, 이에 맞춰 커스텀 툴팁 안내 문구도 동기화하여 갱신함.

### 2026-05-20 (하단 누적 수익률 차트 잘림 해결 및 3단 영역 비율 6:2:2 강제 지정)
- **이슈:** Matplotlib의 `_expand_mpf_vertical_panel_gaps` 함수에서 subplot 세로 간격을 넓힐 때 각 패널의 높이를 반복 차감하면서 중간 패널(거래량)의 배율이 왜곡되고, 하단 마진이 부족하여 세 번째 축(누적 수익률)의 날짜 텍스트 등이 차트 프레임 밖으로 잘리는 현상이 발생함.
- **수정 내용:**
  - **비율(6:2:2) 및 간격 정밀 등분:** 하단 마진(0.15)과 상단 마진(0.95)을 강제 확보하고, 각 패널의 세로 영역을 원래 배율인 `6:2:2` (또는 활성화된 패널 수에 맞춘 원래 비율)에 따라 수학적으로 완벽히 등분하여 재배치하도록 간격 확장 로직을 재설계함.
  - **잘림 방지 패딩 확보:** `save_figure_as_png` 내부에서 저장 직전 `subplots_adjust(bottom=0.15, hspace=0.3)`를 주입하고, `pad_inches=0.08`로 여백을 다듬어 날짜 라벨 및 최하단 그래프가 안전하게 포함되도록 조치함.

- **이슈:** 우측 차트 이미지 높이(490px)가 좌측 입력 섹션(780px)에 비해 다소 낮게 고정되어 레이아웃 균형이 맞지 않고, 차트 내부 플롯(캔들, 거래량, 수익률)의 위아래 비율이 찌그러지거나 텍스트가 짤리는 문제점이 존재함.
- **수정 내용:**
  - **기본 높이 상향:** 좌/우 패널 전체 높이를 `780px`에서 `850px`로 확장하고, fallback 차트 높이도 `560px`로 상향 정의.
  - **동적 세로 높이 동기화:** 우측 프레임의 그리드 row 2(차트 프레임)에 `weight=1`을 설정하고 `sticky="nsew"` 배치하여, 늘어난 컨테이너 높이에 맞춰 차트 프레임이 세로로 꽉 차게 자동 동기화되도록 처리.
  - **6:2:2 비율 적용:** 세 요소(캔들 차트, 거래량 바, 누적 수익률 그래프)의 panel_ratios 값을 정확히 `(6, 2, 2)` 배율로 지정함.
  - **차트 종횡비 최적화:** 찌그러짐을 없애기 위해 Matplotlib의 `figsize` 세로 크기를 10에서 8.2인치(`figsize=(12, 8.2)`)로 재조정하여 GUI 프레임 종횡비에 정밀하게 매칭함.

### 2026-05-20 (차트 네비게이션 툴팁 문구 변경, 추세 필터 기본값 활성화 및 툴팁 텍스트 수정)
- **이슈:** 차트 내비게이션의 ⏪, ◀, ▶, ⏩ 버튼들의 기존 툴팁 문구가 주식 백테스터 성격에 부합하지 않고 직관성이 떨어짐. 또한 초기 실행 시 `추세` 체크박스가 기본 해제(Unchecked)되어 있어 매번 수동으로 활성화해야 하는 번거로움이 있음.
- **수정 내용:**
  - **내비게이션 버튼 툴팁 최적화:** 30영업일/7영업일 전후 이동 정보와 증감량(`-30d`, `-7d`, `+7d`, `+30d`)이 직접적으로 표현되도록 툴팁 문구를 변경.
  - **추세 기본값 활성화:** `self.var_filter_trend` 변수의 기본값을 `True`(Checked)로 설정하여, 프로그램 최초 로드 시 매수 하위 필터들이 활성화(enabled)된 상태로 자연스럽게 시작되도록 변경.
  - **툴팁 문구 세부 튜닝:** `추세` 및 `골든 매수`에 노출되는 말풍선 문구를 보다 명확하고 직관적인 전용 문구로 다듬어 반영.

### 2026-05-20 (매수 조건 레이아웃 변경, 필터 인터락 로직 및 모던 커스텀 툴팁 구현)
- **이슈:** 매수 진입 3대 필터 및 골든 매수 조건의 흐름이 다소 혼란스럽고, 장기 가이드 필터인 '추세(구 대세)'의 해제 여부와 다른 하위 필터들 간의 종속 연계(Interlock)가 UI상에 연동되지 않아 오작동 여지가 있음. 또한 기존 노란색 시스템 툴팁 디자인이 일관성을 해침.
- **수정 내용:**
  - **레이아웃 및 명칭 정리:** `대세(Slope)`를 `추세`로 명칭 변경하고, 매수 영역의 최좌측(첫 번째)에 위치시킴. 전체 순서: `추세 [0.01]` ➜ `골든 매수` ➜ `돌파 강도` ➜ `시간 버퍼`.
  - **인터락(Interlock) 연동:** `추세` 체크박스가 비활성화될 때, 우측의 `골든 매수`, `돌파 강도`, `시간 버퍼` 3종 필터 체크박스 및 추세 기울기 입력란(스핀 버튼 포함) 전체가 일괄 `disabled`(반투명화 및 조작 차단)되도록 GUI 변수 트레이싱을 연계함.
  - **Canvas 기반 커스텀 툴팁:** `HoverTooltip`을 전면 리팩토링하여 둥근 모서리(8px) 다크 네이비(#1e293b) 배경, 하단 중앙 말풍선 화살표, 흰색 폰트(#f8fafc) 및 0.2초 Fade-in 연출이 적용된 Toplevel 창을 그리는 현대적 디자인으로 전면 교체함. `추세`와 `골든 매수`에 각각 지정된 전용 툴팁 문구를 바인딩함.

### 2026-05-20 (차트 제목 내 기간 표기 제거)
- **이슈:** 플레이어 제어 버튼 우측 행에 상세 조회 기간 정보가 상시 출력되므로, 차트 메인 이미지 제목 영역에 기간 대괄호가 중복 표시되어 다소 지저분함.
- **수정 내용:**
  - `backtest_chart.py` 의 `make_backtest_figure` 내부 차트 제목 변수에서 기간 브라켓을 걷어내고, 오직 `종목명 (매매기준 MA##)` 포맷만 깔끔하게 출력하도록 환원.

### 2026-05-20 (차트 이미지 영역 세로 높이 조절로 잘림 현상 해결)
- **이슈:** 우측 차트 프레임 높이가 너무 커서(`FIXED_CHART_H = 650`), 매매 규칙 패널 및 제어 버튼 행 등과 함께 배치될 때 우측 프레임 전체 고정 세로 높이인 780px를 초과하여 차트 맨 하단의 누적 수익률 그래프가 잘리는 현상 발생.
- **수정 내용:**
  - `gui.py` 의 `FIXED_CHART_H` 고정 높이 상수를 `650`에서 `490`으로 조정.
  - 이를 통해 차트가 축소 렌더링되면서 하단 누적 수익률 그래프까지 잘림 없이 좌측 입력 섹션 하단 라인 이내에 완벽히 들어오도록 보정.

### 2026-05-20 (네비게이션 컨트롤 영역 기간 노출 기능 추가)
- **이슈:** 미디어 플레이어형 제어 버튼으로 차트를 좌우로 이동할 때, 현재 조회 중인 구체적인 기간 범위를 직관적으로 파악하기 어려움.
- **수정 내용:**
  - `gui.py` 의 4단 미디어 제어 버튼 우측 동일 행에 `self.lbl_current_period` (CTkLabel)를 추가 배치.
  - 날짜 평행이동(`_shift_period_calendar_days`, `_shift_period_trading_days`) 및 백테스트 실행/완료 시점에 DateEntry로부터 날짜를 동적 취합하여 `조회 기간: YYYY-MM-DD ~ YYYY-MM-DD` 형식으로 갱신하는 `_update_period_label()` 적용.

### 2026-05-20 (차트 제목 및 조회 기간 표시 개선)
- **이슈:** 차트 제목에 불필요한 정보가 섞여 길고, 백테스트 조회 기간이 직접 표시되지 않아 기간 확인이 모호함.
- **수정 내용:**
  - `backtest_chart.py`의 `make_backtest_figure` 내부 제목 형식을 `종목명 (매매기준 MA##) [YYYY-MM-DD ~ YYYY-MM-DD]` 형태로 수정.
  - 시뮬레이션 데이터(`odata`)의 날짜 인덱스로부터 시작일과 종료일을 동적으로 추출하여 제목에 정확히 출력되도록 구현.

### 2026-05-20 (매수 3대 필터 AND 결합 구현)
- **이슈:** 시간 버퍼(`filter_time_buffer`) 조건 검증이 시뮬레이터 루프 내부에 분산되어 있어, 대세 Slope 및 돌파 강도 필터와의 AND 결합 처리를 직관적으로 통제하기 어려움.
- **수정 내용:**
  - `_pass_time_buffer(d, sig_bar)` 헬퍼 함수를 신설하여 골든크로스 발생일 이후 +1, +2 영업일 종가가 MA20 위에 연속 안착했는지 판별.
  - `_buy_filters_pass` 함수 내부에 시간 버퍼 검증을 완전히 통합하여, 활성화된 모든 매수 필터가 완전한 AND 결합으로 체크되도록 구조화.
  - `simulate_single` 내부의 `tb_anchor` 관련 순차 추적 코드를 제거하고, 매수 예약 바(`buf_exec_bar = i + 3`) 방식으로 제어 흐름 단순화.
  - 모든 안착 판정은 확정된 종가 데이터를 기준으로 수행하며, 주문 체결은 반드시 **다음 영업일 시가(Open)**에 실행하여 시점 왜곡(Look-ahead Bias) 금지 규칙 준수.

### 2026-05-20 (UI 개선 작업)
- **이슈:** 매매 규칙 설정 바의 모든 옵션(매수/매도/필터 등)이 일렬로 나열되어 초보자가 보기에 가독성이 떨어짐.
- **수정 내용:** 
  - `rules_panel` 내부의 단일 한 줄(`strip`) 레이아웃을 폐지하고, `buy_frame`과 `sell_frame` 두 개의 카드형 서브 프레임으로 분리.
  - 1행 2열 grid 레이아웃을 도입하여 매매 규칙 영역 내에 균등 분배(weight=1).
  - 매수 조건 영역(`🟢 매수 진입 조건 (AND 결합)`)과 매도 조건 영역(`🔴 매도 청산 조건 (OR 결합)`)으로 시각적 경계 획정.
  - 기존 툴팁, 스핀버튼 리스너, 입력 데이터 바인딩 정상 유지.
- **다음 작업 예정:** `simulator.py`에 매수 진입 3대 필터(대세 Slope, 돌파 강도, 시간 버퍼) AND 결합 구현.

### 2026-05-20 (에이전트 작업)
- **이슈:** 차트 내 좌우 투명 버튼이 데이터 캔들을 가리는 문제 발생.
- **수정 내용:** - 차트 내부 투명 버튼을 완전히 제거함.
  - 매매 규칙 설정 바 아래에 FontAwesome 6 Solid 기반의 4단 미디어 플레이어형 컨트롤러 배치.
  - `-30, -7, +7, +30` 영업일 이동 로직 및 인덱스 초과 방지 예외 처리 완료.
- **다음 작업 예정:** 초보자를 위해 매매 규칙 설정 바를 '매수 조건'과 '매도 조건' 섹션으로 시각적 분리(CSS 격자 구조 적용).

---

## 이관 구간: 2026-05-22 ~ 2026-05-31

progress.md(Harness §4) **Phase F** — **2026-06-01**에 본 저장소 progress.md §2에서 잘라 옮긴 **2026-05-22 ~ 2026-05-31** 구간 원문입니다. v4.0 Portfolio Validation(Phase A~G) changelog는 루트 progress.md에 유지됩니다.

### 2026-05-31 (**v4.25** OHLC Evidence Snapshot)
- **`src/engine/exporter.py`:** 메타 직후 `당일 가격 (OHLC) ★` 4행 · 종목명/코드 통합 · MA20 교차검증 행
- **`src/filters.py`:** `pass_disparity_lock` · `PULLBACK_DISPARITY5/20_LOCK_PCT`
- **`src/data_loader.py`:** `High_t0` · 벌크 `cond_disparity` · 단일 스캔 이격도 락
- **`src/gui.py`:** 창 제목 v4.25

### 2026-05-31 (**v4.20** Scan Evidence Snapshot Export)
- **`src/engine/exporter.py`:** `ScanEvidenceSnapshot`·`generate_evidence_snapshot`·네이비 헤더/Zebra/PASS·FAIL 조건부 서식
- **`src/data_loader.py`:** 벌크 스캔 `evidence` dict (종목별 고정 문자열)
- **`src/gui.py`:** 📥 근거 버튼 · `_scan_evidence_by_code` · 창 제목 v4.20

### 2026-05-31 (**v4.15** Pass2 Pipeline Logic Optimization)
- **`src/filters.py`:** `leader_pullback_pass2_ma20_or_center` · `leader_pullback_center_defense` SSOT
- **`src/data_loader.py`:** 벌크 `cond_pass2` OR 벡터 · `qualifies_leader_pullback_from_ohlcv` 동기화
- **`src/pullback_backtest.py`·`src/gui.py`:** 타임라인·디버그 Pass2 문구 · 창 제목 v4.15

### 2026-05-31 (**v4.10** Market Unified & Snapshot)
- **`src/filters.py`:** `normalize_pullback_scan_market` · `pullback_bulk_markets_for_scan` — dual은 **시장=ALL** 전용, Top=ALL은 선택 시장 전종목
- **`src/data_loader.py`:** 벌크 `code_to_market` · rows 5튜플(상장시장) · stats `scan_market`
- **`src/gui_helpers.py`:** `format_krx_market_badge`·`format_gui_list_leader_pullback`·`parse_gui_list_row_code` [주]/[닥] · ALL 세션·try_build 종목별 시장
- **`src/gui.py`:** 시장 콤보 ALL · 스냅샷 `_scan_result_snapshot` · 시장 변경 시 리스트 재조회 제거 · 창 제목 v4.10

### 2026-05-30 (**v4.00** Liquidity Filter — Pass 0)
- **`src/filters.py`:** `pass_liquidity_gate` — 시총·거래대금 AND
- **`config/settings.yaml`·`v3_scan_config.py`:** `min_liquidity_market_cap_krw`(500억)·`min_liquidity_trade_amount_krw`(10억) SSOT
- **`src/data_loader.py`:** 벌크 Top-N 직후 Pass0 · `qualifies_leader_pullback_from_ohlcv` 유동성 선행 검사
- **`src/gui.py`:** Pass0 디버그·창 제목 v4.00 · 폴백 스캔 동일 게이트

### 2026-05-30 (**v3.95** Perfect Trend Lock — MA60>MA120 배열성)
- **`src/filters.py`:** `pass_dual_long_trend_ma60_and_ma120` — 종가·이평 위치 + **MA60>MA120** AND
- **`src/data_loader.py`:** 벌크 `cond_kim_long` 동일 3중 AND
- **`src/gui.py`·`pullback_backtest.py`:** Pass4 디버그·보고 문구 · 창 제목 v3.95
- **인수:** 052300(2026-03-20) 역배열 수렴 슈팅주 Pass4·최종 0건 컷오프

### 2026-05-30 (**v3.90** Data Sync & Filter Lock)
- **`src/data_loader.py`:** `load_ohlcv`/`load_ohlcv_for_chart` → pykrx 전용(벌크 pkl stitch 우선·`get_market_ohlcv_by_date` 폴백) · FDR OHLCV 폐기 · 벌크 스캔 `MA120`·`cond_kim_long` 듀얼 AND
- **`src/filters.py`:** `pass_dual_long_trend_ma60_and_ma120` · `PULLBACK_VERY_LONG_MA_DAYS=120` · 스캔 히스토리 120영업일
- **`src/gui.py`·`gui_helpers.py`:** `use_momentum_filter` 부트스트랩 강제 ON · Pass4 디버그 문구 · 창 제목 v3.90
- **인수:** 002030(2026-04-30) FDR/pykrx 기준 Pass4·최종 후보 탈락 · 차트·필터 MA60/MA120 수치 일치

### 2026-05-30 (**v3.89** 입력 패널 날짜 1개월 이동)
- **`src/gui.py`:** 시작·종료일 행 우측에 ◀▶ 버튼(22×22)·`_shift_period_months`/`_on_date_shift_months` — `months_before`로 기간 평행 이동·종료일 오늘 클램프 · 종목 선택 시 `_schedule_auto_run_after_shift` 차트 갱신 · 창 제목 v3.89

### 2026-05-30 (**v3.88** 차트 렌더러 단일화·종목명 동기화)
- **`src/gui.py`:** `ticker_to_name` SSOT·`render_stock_chart`/`update_chart_canvas` 단일 렌더 경로 — `_run_chart_only`·`_pending_display_name` 폐기
- **진입점 통일:** 리스트·이력 더블클릭·기간 내비(⏪◀▶⏩)·이평 토글 Refresh → `render_stock_chart`
- **라벨:** `현재 선택 종목 : {티커} | {한글명}` · 차트 타이틀 **종목명만** · 창 제목 v3.88
- **핫픽스:** `update_chart_canvas` 내부 `work()` 에서 `chart_title` 재할당 → `title_resolved` 로 분리(UnboundLocalError)
- **핫픽스:** GUI·`backtest_chart` 이중 `(매매기준 MA{N})` 접미사 제거 → 타이틀 종목명 단독

### 2026-05-29 (**v3.86** 듀얼 시장 ALL 통합 풀)
- **`src/filters.py`:** `pullback_bulk_markets_for_scan` — ALL(0) 시 KOSPI+KOSDAQ
- **`src/data_loader.py`:** 벌크 일별 OHLCV·시총 pykrx 이중 로드 후 concat
- **`src/gui.py`:** 디버그 `Markets pipeline`·Total loaded 통합 표기 · 창 제목 v3.86

### 2026-05-29 (**v3.85** 유니버스 확장·MA60 완화)
- **`src/filters.py`:** `pass_long_trend_close_above_ma`(MA60)·`resolve_pullback_universe_head`(ALL=0)
- **`src/gui_helpers.py`:** 콤보 `100`/`300`/`500`/`1000`/`ALL`
- **`src/data_loader.py`:** 벌크 MA60·60영업일 OHLCV·ALL 시 head 미적용
- **`src/gui.py`:** 디버그 Pass4 `종가>MA60` · 창 제목 v3.85

### 2026-05-29 (**v3.80** 눌림목 전일 양봉·중심선 수호)
- **`src/data_loader.py`:** `leader_pullback_prev_day_yang`·`leader_pullback_center_defense` — Pass1·Pass2 AND · 벌크 `high_m`
- **`src/pullback_backtest.py`:** 타임라인 신호 동일 조건
- **`src/gui.py`:** 디버그 규칙·Pass 라벨 갱신 · 창 제목 v3.80

### 2026-05-29 (**v3.76** OS 배율 System-Aware·pt 폰트)
- **`main.py`:** Qt 환경 변수·`gui_display` 호출 제거
- **`src/gui_display.py`:** 삭제(v3.75 DPI 강제 차단 폐기)
- **`src/gui.py`:** `set_*_scaling` 호출 제거(CTk 기본 OS DPI 연동; `None` 은 API 오류) · 창 제목 v3.76
- **`src/gui_helpers.py`:** `gui_tk_font_pt`/`gui_ctk_font_pt` — Tk 양수 pt 11/10/9(음수 px 제거)

### 2026-05-29 (**v3.75** DPI·폰트 절대 px 고정) [v3.76에서 철회]
- **`main.py`:** `QT_AUTO_SCREEN_SCALE_FACTOR`·`QT_ENABLE_HIGHDPI_SCALING` 등 0/1 강제 · `_launch_gui_main()` · watch 자식 프로세스 동일 정책
- **`src/gui_display.py`:** `apply_gui_display_policy`·`lock_tk_scaling_to_one` · Windows Per-monitor DPI aware
- **`src/gui_helpers.py`:** `GUI_*_PX`(12/10/9/12) · `gui_tk_font_px`(음수=픽셀) · `gui_ctk_font_px`
- **`src/gui.py`:** DateEntry·Canvas·내비 버튼 px 튜플 · CTkFont px 통일 · 창 제목 v3.75

### 2026-05-29 (**v3.70** SSOT 마이그레이션 — 하드코딩 폴백 박멸)
- **`src/v3_scan_config.py`:** `pullback_scan_params_from_yaml_section`·`resolve_effective_pullback_scan_params` — `v3_0` 필수 키 누락 시 `KeyError` · `.get(..., 1.5/0.8/300)` 제거
- **`main.py`·`scripts/compare_overnight_cli_gui.py`·`tests/test_overnight_parity.py`:** `pullback_scan_params_from_mapping` / `default_pullback_scan_params` 경유
- **`src/gui_helpers.py`:** `bootstrap_gui_pullback_scan_ssot` — YAML → `last_session.json` → StringVar/콤보 · `apply_yaml_to_widgets`에서 v3 중복 제거
- **`src/gui.py`:** 초기 StringVar 빈 값 · 부트스트랩 후 주입 · `load_last_gui_session` 단일 호출 제거

### 2026-05-29 (**v3.66** 모멘텀 필터 선택·유니버스 라벨)
- **`config/settings.yaml`:** `v3_0.use_momentum_filter` — false 시 Pass5(MA5≥MA10) 스킵·Pass4(종가>MA120)까지만 적용
- **`src/v3_scan_config.py`:** `PullbackScanParams.use_momentum_filter` · 세션 JSON 복원
- **`src/data_loader.py`:** 벌크·단일 `qualifies_leader_pullback_from_ohlcv` — `kim_straight_trend_pass` 장기/단기 분리
- **`src/pullback_backtest.py`:** 타임라인 백테스트 동일 분기
- **`src/gui.py`:** `MA5 >= MA10` 체크박스 · 디버그 로그 Pass5 스킵 문구 · 유니버스 `Top 100` / `Top` / `Top 500`
- **`src/gui_helpers.py`:** 유니버스 라벨↔값 매핑 · `last_session.json`·`apply_yaml_to_widgets` 연동
- **`main.py`·`overnight_parity.py`:** parity·CLI에 `use_momentum_filter` 전달

### 2026-05-29 (**v3.65** 좌측 패널 가로 압축)
- **`gui.py`:** `FIXED_LEFT_W` 200 · 파라미터 2행(일자/유니버스·세력·눌림) · 라벨 축소 · 마진 2px · 백테스트 1행 필드 · 하단 안내 1줄

### 2026-05-29 (**v3.60** UI 성능·세션 보존)
- **`gui.py`:** 유니버스 콤보 · 종료 시 `config/last_session.json` · 스캔/백테스트 버튼 경과 타이머 · 버튼 높이 1/3 축소 · 차트 가이드 중앙 정렬
- **`gui_helpers.py`:** `dump_last_gui_session` / `load_last_gui_session`
- **`data_loader.py`:** `universe_limit` 상한 500

### 2026-05-29 (**v3.50** 김직선 정배열 추세 필터)
- **`data_loader.py`:** `kim_straight_trend_pass` · 벌크 OHLCV 120영업일 확장 · Pass4(종가>MA120)·Pass5(MA5≥MA10)
- **`pullback_backtest.py`:** 타임라인 백테스트 진입에 동일 추세 필터 적용
- **`gui.py`:** 폴백 워밍업 135영업일 · 디버그 Pass 4/5 카운트

### 2026-05-29 (**v3.45** GUI 시각 정합성·타이포그래피)
- **`backtest_chart.py`:** 가격·거래량 패널 구분선·spine 제거 · 패널 간격 축소
- **`gui.py`:** 차트 PNG 캔버스 정중앙(CENTER) 배치 · `Malgun Gothic` · 리스트 10pt · DateEntry width 11 · 원금|매도 1행 · 이력 9행 · 안내 9pt
- **`gui_helpers.py`:** `GUI_FONT_FAMILY`·`GUI_LIST_FONT_SIZE`·`gui_hint_font()` 상수

### 2026-05-29 (**v3.40** 스캔·백테스트 UI 기능 분리)
- **`gui.py`:** 스캔 파라미터(시장·기간·세력/눌림) 리스트 상단 가로 배치 · 종목 검색창·수수료 입력 제거(0.015%/0.20% 고정) · `🔵 스캔`/`🔴 스캔 중단` · 이력 리스트 하단 · `⚙️ 단일 종목 백테스트` 패널(가상 원금 쉼표 마스킹·매도 시점 콤보·결과 CTkTextbox)
- **`pullback_backtest.py`:** 차트 활성 종목 기간 내 눌림목 3중 조건 전수·종가 매수→익일 시가 매도 복리 시뮬레이션

### 2026-05-29 (**v3.30** 주도주 눌림목 스캐너)
- **`data_loader.py`:** `scan_leader_pullback_candidates_bulk` — 22영업일 OHLCV·`vol_ma20_strictly_prior`(t-2~t-21)·`volume_burst_multiple`·`vol_shrink_limit` · `qualifies_leader_pullback_from_ohlcv` 폴백
- **`gui.py`:** 「🔥 주도주 눌림목 리스트」·「🔵 주도주 눌림목 스캔」·좌하단 파라미터 2종 · `LeaderPullbackScanWorker`
- **`config/settings.yaml`:** `v3_0.volume_burst_multiple`·`vol_shrink_limit`

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
