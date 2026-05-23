# BackTesterKRX

**문서화가 가장 중요합니다.**  
→ [docs/작업지시서-BackTesterKRX.md](docs/작업지시서-BackTesterKRX.md) · [docs/백테스트-규칙.md](docs/백테스트-규칙.md) · [docs/문서화-원칙.md](docs/문서화-원칙.md)

**싱글 종목**(500만 원 전액), **일봉/주봉**, 익 **다음 봉 시가** 체결.

**시작점:** 프로젝트 루트의 **`main.py`** — 인자 없으면 **GUI**, `--list` 등이 있으면 **CLI**. 개발 시 GUI 자동 재시작은 **`python main.py --watch`** (또는 `-w`).

## 폴더 구조 (요약)

```
BackTesterKRX/
├── src/
│   ├── __init__.py
│   ├── gui.py          # 반응형 그리드·차트 패널(v4.9 실측·PNG mpl 동기), 기간 패닝 등
│   ├── gui_helpers.py  # YAML 반영·설정 dict·툴팁 (엔진과 분리)
│   ├── backtest_constants.py  # 추세 이평 상수·마커 색 (순환 참조 방지)
│   ├── backtest_chart.py # mplfinance 정적 PNG (`make_backtest_figure`)
│   ├── data_loader.py  # 종목·OHLCV·주봉 집계·YAML 로드
│   ├── strategy.py     # 골든/데드크로스 시그널
│   ├── simulator.py    # 익봉 시가 체결·수수료 시뮬
│   ├── metrics.py      # 성과 지표·PNG 보고서·run_backtest_detailed
│   ├── slope_ablation_batch.py  # `--slope-ablation-batch` 유니버스 통계 배치(TSV)
│   └── stock_screener.py  # 스크리너(MA120 역배열 제외·ATR14·거래대금·낙폭·거래량건조 순위 융합)
├── output/             # backtest_report.png
├── config/             # settings.yaml (기본 설정)
├── main.py             # GUI/CLI 공통 진입
└── requirements.txt
```

## 가상환경 (필수)

프로젝트 루트 `D:\develop\BackTesterKRX` 에서 **`venv`** 를 쓴다. 전역 `pip` 에 설치하지 않는다.

**PowerShell (최초 1회):**

```powershell
cd D:\develop\BackTesterKRX
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

**매번 실행 전:** `.\venv\Scripts\Activate.ps1` (프롬프트에 `(venv)` 가 보이면 OK)

**실행 스크립트 정책 오류 시 (관리자/사용자 설정에 따라):**  
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` 후 다시 활성화.

## 실행 (GUI — 권장)

```powershell
python main.py
```

(화면: **시장·종목·검색** 및 **스크리너 모드**(전체 / 스크리너 / 시총 상위 / 돌파 에너지)·검색 결과(티커|종목명|시총), 기간 기본 **6개월 전~오늘**. 우측 차트는 창에 맞춰 **가변 확장**(최소 크기 약 **960×540**, 폴백 **1280×840**)·실측 `chart_overlay_host` 기준 PNG 리사이즈. 매매 기준 이평 **5·10·20**, 추세선 **20·120** 기본, 캘린더 선택.)  
검색 → 리스트에서 **종목 1개 선택** 또는 **더블클릭 즉시 실행**(동작은 동일)·기간·원금 등 설정 후 **백테스트 실행** 버튼을 누르면 **현재 선택(하이라이트)된 종목 하나만** 단일 실행됩니다.

### GUI 개발용 — 저장 시 창 자동 재시작 (`watchdog`, Node 불필요)

감시 범위는 **`src/` 이하의 모든 `.py`** 와 프로젝트 루트의 **`main.py`** 입니다. 둘 중 하나라도 저장되면 GUI 자식 프로세스를 종료한 뒤 곧바로 다시 띄웁니다(디바운스 약 **0.5초**). **일반 사용은 위의 `python main.py` 만 쓰면 됩니다.**

```powershell
pip install -r requirements.txt
python main.py --watch
```

- 터미널에는 `[watch] 코드 변경 감지 ...` 가 찍힙니다.
- 에디터가 임시 파일로 저장 후 이름을 바꾸는 방식이어도 감지되도록 이동(`on_moved`) 이벤트까지 처리합니다.
- **CLI**(`--list` 등)와 `--watch` 는 **동시에 쓸 수 없습니다.**
- 종료: GUI 창을 닫거나, 터미널에서 **Ctrl+C**.

## 실행 (CLI)

```powershell
python main.py --list
```

**후보만 출력:** 위와 동일.

**CLI로 YAML 덮어쓰기 (예):**

```powershell
python main.py --interval daily --start 2022-01-01 --end 2025-12-31 --keyword 삼성 --code 005930 --ma 20 --ma120 --ma200
```

(`--ma120` / `--ma200` 은 YAML 의 `show_trend_ma120` / `show_trend_ma200` 을 켭니다.)

**스크리너 배치 CLI:** 종료일(`--end` 또는 YAML `period.end_date`)까지의 **일봉만** 사용. 후보별 데이터는 **캘린더 약 400일**(MA120·ATR 워밍업)까지 당김. **종가\<120일 단순이평(역배열)** 인 종목은 **랭킹 산출 전 제외**. 변동성 지표는 **ATR14 고정**. 스코어는 최근 lookback 내 **거래대금·고점 대비 낙폭·거래량 건조·ATR%** 를 각각 순위분위화한 뒤 평균(낙폭은 `pullback_rank_cap_pct` 클램프 후 순위화)합니다. 상위 `top_n` 종목만 순차 백테스트합니다.

```powershell
python main.py --screener-batch --market KOSPI --keyword 삼성 --end 2025-12-31
```

스크리너 선정 순위는 `output/screener_last.tsv` 에 저장합니다. 검색 키워드를 비우면 해당 시장 **전 후보**를 돌려 **네트워크 부하와 시간이 큽니다.**

**곡선 가속도 ablation(전 유니버스 통계 비교):** 같은 YAML 전략·기간으로 `universe.slope_ablation_batch` 시총 한도 종목 각각 **`use_slope_acceleration` False**(baseline)·**True**(slope_accel; 엔진: 최근 MA20 OLS 기울기>0) 경량 시뮬 2회, 결과를 **`output/slope_ablation.tsv`** 에 기록합니다(차트/PNG 생략). `--batch-max-workers N` 로 병렬도 조정.

```powershell
python main.py --slope-ablation-batch --market KOSPI --batch-max-workers 4
```

**그래프:** `output/backtest_report.png` — **`mplfinance`** 로 **가격 패널**(캔들 또는 종가선·좌측 가격축 눈금) + **(선택) 거래량 패널**(`panel_ratios` **6:2**; 거래량 끔 시 **단일 패널**) + **(선택·GUI 체크 시) 누적 수익률(%) twinx**(하늘색 계열 배경 음영 + 우측 % 축 레이블) + **(선택) 추세 이평**(PNG 좌상단 `ax.legend` v4.5) + **매매 타점**(v3.4~v3.5). 매매 기준 N일 이평은 **선으로 그리지 않음**.

## 설정

`config/settings.yaml` — `period`(시작·종료 둘 다 비우면 GUI·CLI 모두 **실행 시점 기준 6개월 전~오늘**), `universe`, `strategy.interval`, `strategy.ma_period`(매매 5·10·20 권장), **v4.6** `golden_buy_enabled` · `dead_cross_sell_enabled`(기본 `true`; 끄면 골든/데크 시그널·데크 신호 매도 각각 비활성), **`strategy.show_trend_ma5` … `show_trend_ma200`** (차트 추세 오버레이, 기본 예시는 20·120 켜짐), **v4.0 매수 진입 필터**(대세·돌파·시간 버퍼, **골든 후보에 AND 결합**) · **v4.4 가변 낙폭 매도**(데크 신호 매도와 **OR**), 차트 패널 토글(`show_chart_candle`·`show_chart_volume`·**v4.10** `show_return_overlay` 기본 OFF — 켜면 가격 패널 누적 수익률 우측 Y축 포함), 비용, `portfolio.initial_cash`. **v4.10 GUI:** 백테스트 요약은 먼저 표시 후 PNG 차트가 별도 단계에서 생성됩니다(`defer_chart_render`/`materialize_backtest_chart_png`). **v4.1:** GUI에서 기간을 버튼으로 ±30일(달력) 이동·차트 **이미지 위** 좌우 투명 버튼으로 **7영업일**(BDay, 주말 제외) 단위 이동할 수 있으며, 엔진은 차트 구간은 유지한 채 OHLCV만 시작일보다 앞에서 추가 로드해 MA120·기울기 계산이 첫 봉부터 나오게 합니다.

## 소스 역할 (파일별)

| 경로 | 역할 |
|------|------|
| `main.py` | 인자 없음 → GUI; `--watch` → GUI+코드 변경 시 재시작; 그 외 → CLI |
| `src/gui.py` | CustomTkinter 창·**반응형 grid(weight)**·차트 패널 **v4.9** 우패널 `grid_propagate(False)` 및 `chart_render_px` 로 mpl PNG 해상도 동기 실측 리페인트 |
| `src/gui_helpers.py` | YAML→위젯·`try_build_config`·툴팁 |
| `src/backtest_constants.py` | `TREND_MA_PERIODS` 등 차트·GUI 공유 상수 |
| `src/backtest_chart.py` | mplfinance 멀티패널·타점·PNG 저장; **v4.9** 선택 `figsize`·`layout_preset`(CLI `report`/GUI `gui_target`) |
| `src/data_loader.py` | 종목 필터·OHLCV·주봉·`load_config`(기간 미설정 시 6개월~오늘)·`ohlcv_warm_start_date` · **v4.10** FDR 상장표 TTL·OHLCV LRU |
| `src/strategy.py` | 이평 돌파 시그널 |
| `src/simulator.py` | 익봉 시가 체결 시뮬 · **v4.10** 시뮬 루프 ndarray 최적화 |
| `src/metrics.py` | 누적·CAGR·MDD, `run_backtest_detailed`(차트는 `backtest_chart` 호출) · **v4.10** `defer_chart_render`·`materialize_backtest_chart_png` |
| `src/stock_screener.py` | 일봉 스크리너(ATR14 고정·MA120 역배열 하드 필터·거래대금·낙폭·거래량건조 순위 융합·시총 상위·돌파 에너지·GUI·CLI 공용) |
