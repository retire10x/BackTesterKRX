# v5.5.2 코스닥 스나이퍼 — 라이브 봇 일일 운영 SOP

**문서 버전:** 1.0  
**적용 전략:** v5.5.2 FROZEN (Hit & Run +8% / -3% / 4일 · 듀얼 MA · 변곡 진입)  
**산출물 경로:** `docs/live_SOP_guide.md`

---

## 1. 문서 개요 및 목적

| 항목 | 내용 |
|------|------|
| **목적** | 장중 0.5초 실시간 감시를 위해 운용 PC를 안전하게 가동·종료하고, **자동 마스터**와 **대표님 수동 개입**을 결합한 하이브리드 운영을 표준화 |
| **운영 모델** | 오전~장중: 마스터 런처 상시 가동 · 15:10~15:16: 사령탑 수동 검증 · 15:20: 마스터 자동 종가 주문 |
| **관련 설정** | `config/live_settings.yaml` · `.env` · `config/live_positions.json` |

**관련 문서**

- 기술 요약: `docs/live_daily_manual.md`
- 백테스트 규칙: `harness.md` · `progress.md`

---

## 2. 사전 고정 설정 (최초 1회 · 변경 시 재확인)

### 2.1 Windows 전원 (필수)

| 설정 | 값 |
|------|-----|
| 화면 끄기 | 허용 가능 |
| **시스템 절전(Sleep)** | **안 함 (Never)** — 장중 봇 동면 원천 차단 |
| 절전 모드 | 사용 안 함 (가능 시) |

`설정 → 시스템 → 전원 및 배터리 → 화면 및 절전`

### 2.2 `.env` 체크리스트

| 변수 | 용도 |
|------|------|
| `KRX_ID` / `KRX_PW` | pykrx 스캔 (15:10·15:15) |
| `KIS_PAPER_APP_KEY` / `KIS_PAPER_APP_SECRET` | 모의 API |
| `KIS_PAPER_ACCOUNT_NUMBER` | 모의 계좌 8자리 |
| `KIS_ACCOUNT_NUMBER` | 실전 계좌 (실전 전환 시) |
| `LIVE_DRY_RUN` | `1`=주문 시뮬 · `0`=실제 KIS 주문 |

### 2.3 `config/live_settings.yaml`

| 키 | 기본값 | 의미 |
|----|--------|------|
| `account.mode` | `paper` | 모의 검증 후 `real_money` |
| `account.max_slots_limit` | 5 | 동적 슬롯 상한 (총자산÷5만, 1~5) |
| `account.minimum_operational_capital` | 50000 | 미만 시 매수 셧다운 |
| `account.bet_amount_per_slot` | 50000 | 종목당 베팅 |
| `screener.screener_time` | 15:15 | 마스터 자동 스캔 |
| `strategy.entry_time` | 15:20 | 마스터 자동 진입 |
| `watch.poll_interval_sec` | 0.5 | 장중 감시 주기 |

### 2.4 Python 실행 경로 (권장)

프로젝트 가상환경이 있으면 아래 형식을 SOP 전 구간에 사용합니다.

```powershell
cd D:\develop\BackTesterKRX
$PY = ".\venv\Scripts\python.exe"   # venv 없으면: python
```

이하 예시는 `python`으로 표기합니다. 실제 환경에 맞게 `$PY`로 치환하세요.

---

## 3. 일일 표준 운영 절차 (SOP)

### 🌅 오전 루틴 — 시스템 점검 및 장중 감시 점화 (08:30 ~ 08:50)

**목표:** 장 개시 전 API 세션·어제 이월 포지션 동기화 후, 0.5초 무한 감시 대기.

#### Step A. PC · 네트워크

1. 운용 PC 전원 ON
2. 유선/ Wi-Fi 연결 확인
3. **절전 모드 OFF** (§2.1)

#### Step B. 마스터 런처 구동 (메인 터미널 — 종일 유지)

```powershell
cd D:\develop\BackTesterKRX
.\venv\Scripts\python.exe run_live_bot.py
```

또는 DRY-RUN 강제:

```powershell
.\venv\Scripts\python.exe run_live_bot.py --dry-run
```

#### Step C. 오전 안정성 확인 (로그 체크)

| 확인 항목 | 정상 로그 예시 |
|-----------|----------------|
| 시스템 기동 | `🚀 [시스템 가동] v5.5.2 코스닥 스나이퍼 완전 자동 마스터 엔진` |
| OAuth (실 API) | `✅ OAuth2 토큰 발급 성공` (`LIVE_DRY_RUN=0` 일 때) |
| 감시 (보유 있음) | `[실시간 실전 감시 시작] 현재 추적 중인 종목: ['005930', ...]` |
| 감시 (보유 없음) | `[실시간 감시 대기] 보유 종목 없음 — 15:15 스캔 · 15:20 진입 예정` |

**이후 동작**

- 보유 종목이 있으면 **09:00~15:30** 동안 0.5초 주기로 고가/저가 추적
- +8% 익절 · -3% 손절 · 4일 타임스탑 충족 시 KIS 청산 주문 (`LIVE_DRY_RUN`에 따름)
- **이 터미널은 퇴근 전까지 `Ctrl+C` 하지 않음** (§3.3 참고)

> 모니터는 꺼도 됩니다. **PC 절전만 금지**합니다.

---

### 🕒 오후 루틴 — 사령탑 개입 및 동시호가 검증 (15:10 ~ 15:21)

**목표:** 주도주 40종 선확인 → Dry-Run 타점 검증 → 15:20 마스터 자동 실주문(또는 모의).

> 아래 수동 명령은 **별도 PowerShell 창**에서 실행합니다.  
> 오전에 켠 **마스터 런처 터미널은 그대로 둡니다.**

#### [15:10] ROUTINE 0 — 주도주 선제 스캔 (수동 권장)

```powershell
cd D:\develop\BackTesterKRX
.\venv\Scripts\python.exe run_live_bot.py screener --force
```

**확인**

1. `config/live_today_universe.json` — 코드 40개 이하 배열
2. `config/live_today_universe.meta.json` — `scanned_items_report` 에 시총·거래대금(억 원) 랭킹

**판단:** 거래대금 상위 종목이 전략 의도(900억~4,000억·50억 컷)와 맞는지 눈으로 슥 확인.

> 15:15에 마스터가 자동 스캔을 한 번 더 실행할 수 있습니다(당일 state 기준 1회).  
> 15:10 선제 스캔으로 리포트를 먼저 확보하는 것이 SOP 의도입니다.

#### [15:16] ROUTINE 0b — 황금 필터 · 가상 타점 (Dry-Run)

```powershell
.\venv\Scripts\python.exe run_live_bot.py entry --dry-run --force
```

> **주의:** `entry` 명령은 **재스캔하지 않습니다.** 반드시 15:10 `screener`로 만든 `live_today_universe.json`만 로드합니다.

**확인 로그**

| 로그 | 의미 |
|------|------|
| `📂 유니버스 로드 — ... (재스캔 없음)` | 저장된 JSON 사용 |
| `[3/40] 432430 (와이랩) 진입 조건 연산 중…` | 종목별 연산 진행 |
| `[탈락] 432430 (와이랩) — 120일선 아래 위치` | 탈락 사유 |
| `🔥 [진입 시그널 포착] …` | 60/120 듀얼 우상향 + 변곡 통과 |
| `[탈락] … — 슬롯 가득` | 예수금·슬롯 초과 |

**판단:** 오늘 15:20에 실제 탑승 후보로 적합한지 최종 확인.  
(Dry-Run이므로 KIS 주문·`live_positions.json` 실매수 반영은 `LIVE_DRY_RUN=0` 마스터 진입 시에만)

#### [15:20 ~ 15:21] ROUTINE 1·2 — 자동 집행 (마스터)

**조작 없음.** 오전부터 켠 마스터가 자동 처리합니다.

| 시각 | 마스터 동작 |
|------|-------------|
| 15:15 | `LiveScreener` — Top 40 갱신 (당일 미실행 시) |
| 15:20 | `calculate_entry_signals()` — KIS 종가(MOC) 주문 |

**마스터 로그**

- `🔔 [ROUTINE 2] 15:20 변곡·듀얼 MA 진입 및 종가 주문`
- `🎯 [주문 시그널]` / `✅ 진입` (실주문 모드)

#### [15:21] 포지션 이월 확인

`config/live_positions.json` 을 엽니다.

```json
{
  "updated_at": "2026-06-04T15:20:05+09:00",
  "positions": [
    {
      "code": "005930",
      "qty": 6,
      "entry_price": 72000,
      "entry_date": "2026-06-04",
      "hold_days": 0
    }
  ]
}
```

- `entry_date`가 **오늘**이면 당일 매수 이월 성공
- 내일 09:00부터 마스터가 해당 종목을 0.5초 감시

**실주문 전환 체크**

- 모의 검증: `LIVE_DRY_RUN=0` + `account.mode: paper`
- 실전: `real_money` + 잔고·리스크 최종 승인 후만

---

### 🌆 장마감 루틴 — 당일 정산 및 안전 종료 (15:40 이후)

#### Step A. 포지션 스냅샷 (별도 터미널 · 1회)

```powershell
cd D:\develop\BackTesterKRX
.\venv\Scripts\python.exe run_live_bot.py watch --once --dry-run
```

**출력 예**

```
📊 당일 포지션 스냅샷 (2종)
   005930 qty=6 진입=72,000 현재≈73,500 PnL=+2.08% hold=0d entry_date=2026-06-04
```

> `--dry-run`: 청산 주문 없이 상태만 조회. 실청산 테스트는 `--dry-run` 제거 (주의).

#### Step B. 마스터 엔진 종료

1. **오전에 켠 마스터 터미널**로 이동
2. `Ctrl + C` → `⏹️ 사용자 중단 — 마스터 엔진 종료` 확인
3. PC 전원 OFF (또는 내일 08:30 전까지 유지 정책에 따름)

**내일 이월**

- `live_positions.json`은 디스크에 유지 → 내일 08:30 마스터 재기동 시 자동 추적 재개
- `live_master_state.json`의 `screener_date` / `entry_date`는 **날짜가 바뀌면** 자동 리셋

---

## 4. 하이브리드 명령어 요약표

| 시각 | 구분 | 명령 | 터미널 |
|------|------|------|--------|
| 08:30~08:50 | 자동 | `run_live_bot.py` | **메인 (상시)** |
| 15:10 | 수동 | `run_live_bot.py screener --force` | 보조 |
| 15:16 | 수동 | `run_live_bot.py entry --dry-run --force` | 보조 |
| 15:20~15:21 | 자동 | (마스터) | 메인 |
| 15:40+ | 수동 | `run_live_bot.py watch --once --dry-run` | 보조 |
| 15:40+ | 종료 | `Ctrl+C` on master | 메인 |

**보조 점검**

```powershell
.\venv\Scripts\python.exe scripts\test_live_gateway.py --live
.\venv\Scripts\python.exe scripts\run_live_screener.py --show
```

---

## 5. 파일·상태 맵

| 파일 | 역할 |
|------|------|
| `config/live_today_universe.json` | 당일 후보 종목 코드 |
| `config/live_today_universe.meta.json` | 15:10·15:15 스캔 리포트 |
| `config/live_positions.json` | 보유·진입가·이월 |
| `config/live_master_state.json` | 마스터 당일 스캔/진입 1회 플래그 |
| `config/live_settings.yaml` | SSOT 파라미터 |

---

## 6. 장애 대응 (Quick Reference)

| 증상 | 조치 |
|------|------|
| 15:10 스캔 0종 | 15:12~15:18 재실행 · `scripts/run_live_screener.py` |
| OAuth `EGW00133` | 1분 대기 후 재시도 (토큰 1분 1회) |
| `EGW00201` 초당 한도 | 자동 0.5s 간격·재시도 · 진입 후 `live_positions.json` 즉시 저장(잔고 재조회 생략) |
| `CHECK_ACNO` | `KIS_PAPER_ACCOUNT_NUMBER` 모의 8자리 확인 |
| 마스터 멈춤 | 로그 예외 확인 · `Ctrl+C` 후 재기동 |
| 절전 후 봇 정지 | Windows 절전 OFF 재확인 |

---

## 7. 변경 이력

| 날짜 | 버전 | 내용 |
|------|------|------|
| 2026-06-04 | 1.0 | 최초 SOP — 오전/오후/장마감 하이브리드 절차 박제 |

---

## 8. 간편 명령

# 주도주 스캔 시작
PS D:\develop\BackTesterKRX> .\venv\Scripts\python.exe run_live_bot.py screener --force

# 보유 종목이 있을때 0.5초 마다 감시
PS D:\develop\BackTesterKRX> .\venv\Scripts\python.exe run_live_bot.py

# 매수
PS D:\develop\BackTesterKRX> .\venv\Scripts\python.exe run_live_bot.py entry --force
