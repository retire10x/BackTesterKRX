# v11.2 라이브 ORB 모의투자 — 내일 아침 운영 SOP

## 사전 준비 (오늘 밤)

### 1. 텔레그램 봇 설정
프로젝트 루트 `.env` 파일에 추가:

```
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

또는 `config/config.json`:

```json
{
  "telegram": {
    "TELEGRAM_BOT_TOKEN": "your_bot_token",
    "TELEGRAM_CHAT_ID": "your_chat_id"
  }
}
```

### 2. 패키지 설치
```powershell
cd D:\develop\BackTesterKRX
.\venv\Scripts\activate
pip install streamlit streamlit-autorefresh
```

### 3. Mock 검증 (장외 시간)
```powershell
python run_v11_live_paper_trading.py --mock --speed 0
```
- 콘솔에 `PASS ✅` 확인
- 스마트폰 텔레그램에 기동·매수·청산·EOD 알림 도착 확인

---

## 내일 아침 (08:50 이전)

### 탭 1 — 라이브 트레이딩 봇
```powershell
cd D:\develop\BackTesterKRX
.\venv\Scripts\python run_v11_live_paper_trading.py
```
- 장외 시간이면 **09:00 KST**까지 자동 대기
- 09:00~15:30 분봉 수집 · ORB 매매 · 15:30 EOD 정산
- 로그: `logs/v11_live_paper.log`

### 탭 2 — Streamlit 대시보드
```powershell
cd D:\develop\BackTesterKRX
.\venv\Scripts\streamlit run dashboard/live_dashboard.py
```
- 브라우저: http://localhost:8501
- 1분마다 자동 새로고침 (총자산 · 손익 · 거래 내역)

### 탭 3 (선택) — 로그 실시간 모니터
```powershell
Get-Content logs\v11_live_paper.log -Wait -Tail 50 -Encoding utf8
```

---

## 장중 타임라인 (KST)

| 시각 | 동작 |
|------|------|
| 09:00~09:15 | ORB 기준선(15분 고점) 확정 |
| 09:16~10:30 | 돌파 시 가상 매수 → 텔레그램 🟢 |
| 09:00~15:20 | TP/SL 분 단위 감시 → 텔레그램 🔴 |
| 15:20 | 잔여 포지션 타임스탑 |
| 15:30 | Safe Vault 정산 · EOD CSV/MD · 텔레그램 🏦 |

---

## 생성 파일

| 경로 | 내용 |
|------|------|
| `data/live_trading.db` | live_equity · live_trades (대시보드 SSOT) |
| `outputs/v11_live_trades_YYYYMMDD.csv` | EOD 거래 덤프 |
| `outputs/v11_live_report_YYYYMMDD.md` | EOD 일일 리포트 |
| `config/v11_paper_broker.json` | 가상 계좌 상태 |

---

## 문제 해결

- **텔레그램 미수신**: `.env` 토큰/채팅ID 확인, Mock 재실행
- **대시보드 빈 화면**: 봇(탭1)이 먼저 기동되어 DB에 데이터 적재됐는지 확인
- **유니버스 조회 실패**: pykrx/KRX 로그인 또는 Mock 종목 폴백 (로그 확인)
