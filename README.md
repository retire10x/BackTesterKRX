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
│   ├── gui.py          # 창·입력·차트(v2.9: 높이730·이평 라디오·추세 6종)
│   ├── data_loader.py  # 종목·OHLCV·주봉 집계·YAML 로드
│   ├── strategy.py     # 골든/데드크로스 시그널
│   ├── simulator.py    # 익봉 시가 체결·수수료 시뮬
│   └── metrics.py      # 성과 지표·PNG 보고서·run_backtest_detailed
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

(화면: **시장·종목·검색** 한 줄, 기간 기본 **6개월 전~오늘**, 추세선 기본 **20·120일**, **가상 원금**은 **시작일·종료일과 같은 줄**(3열). 우측 차트 **730px**, 매매 이평 **5·10·20**, 캘린더로 기간 선택.)  
검색 → 리스트에서 **종목 1개 선택** → 일/주봉·기간·원금·**매매 이평(5/10/20)**·추세선·차트 지표 → **백테스트 실행**.

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

**그래프:** `output/backtest_report.png` — **`mplfinance`** 로 가격(**캔들** 또는 **종가선**)·**(선택) 거래량**·**(선택) 누적 수익률** 패널을 조합(비율: 전체 **5:2:3**, 거래량 끔 **6:4**, 수익률 끔 **7:3**, 가격만 **단일 패널**) + **(선택) 추세 이평 오버레이(6종·범례 색 고정)** + **매매 타점(v3.3: ▲ 녹색·저가 앵커 아래 / ▼ 노랑·고가 앵커 위, 각 15pt 고정 오프셋)**. 매매 기준 이평(N일)은 **차트에 실선으로 그리지 않으며** 시그널·체결 시뮬에만 사용합니다.

## 설정

`config/settings.yaml` — `period`(시작·종료 비우면 로드 시 **6개월 전~오늘** 자동), `universe`, `strategy.interval`, `strategy.ma_period`(매매 5·10·20 권장), **`strategy.show_trend_ma5` … `show_trend_ma200`** (차트 추세 오버레이, 기본 예시는 20·120 켜짐), 차트 패널 토글, 비용, `portfolio.initial_cash`.

## 소스 역할 (파일별)

| 경로 | 역할 |
|------|------|
| `main.py` | 인자 없음 → GUI; `--watch` → GUI+코드 변경 시 재시작; 그 외 → CLI |
| `src/gui.py` | CustomTkinter UI (`tkcalendar` 날짜 선택 포함) |
| `src/data_loader.py` | 종목 필터·OHLCV·주봉·`load_config`(기간 미설정 시 6개월~오늘) |
| `src/strategy.py` | 이평 돌파 시그널 |
| `src/simulator.py` | 익봉 시가 체결 시뮬 |
| `src/metrics.py` | 누적·CAGR·MDD, PNG, `run_backtest_detailed` |
