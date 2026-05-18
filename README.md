# BackTesterKRX

**문서화가 가장 중요합니다.**  
→ [docs/작업지시서-BackTesterKRX.md](docs/작업지시서-BackTesterKRX.md) · [docs/백테스트-규칙.md](docs/백테스트-규칙.md) · [docs/문서화-원칙.md](docs/문서화-원칙.md)

**싱글 종목**(500만 원 전액), **일봉/주봉**, 익 **다음 봉 시가** 체결.

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

## 실행

활성화된 `venv` 안에서:

```powershell
python main.py
```

**주봉 예:** `python main.py --interval weekly`

**후보 목록만:**

```powershell
python main.py --list
```

**CLI로 YAML 덮어쓰기 (예):**

```powershell
python main.py --interval daily --start 2022-01-01 --end 2025-12-31 --keyword 삼성 --code 005930 --ma 20
```

**그래프:** `output/backtest_report.png` — **종가 + 매매 타점(빨간 ▲ / 파란 ▼)** + 누적 수익률(2패널).

## 설정

`config/settings.yaml` — `period`, `universe`, `strategy.interval` (`daily` / `weekly`), `strategy.ma_period`, 비용, `portfolio.initial_cash`.
