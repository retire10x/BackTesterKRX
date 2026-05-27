# CLI vs GUI 오버나이트 스캔 결과 동일성 분석

## 요약

- **의도적으로 기본값만 놓고 보면 “항상 같은 결과”가 나오지 않을 수 있습니다.** 이유는 (1)**유니버스 범위**, (2)**평가 기준일(앵커)**, (3)**선택적인 OHLCV 소스/스냅샷 차이**입니다.
- **동일하게 맞추려면**: 같은 시장(`KOSPI`/`KOSDAQ`), **`v3_0.universe_limit`으로 잘린 동일 종목 집합**, 그리고 **같은 `effective_anchor`(벌크 통계의 기준 영업일)**에 대해 CLI 쪽 시계열의 **그 날짜 행**을 보면 됩니다.

## 실행 경로 정리

| 항목 | CLI (`python main.py --mode cli`) | GUI 오버나이트 스캔 (벌크 성공 시) |
|------|-----------------------------------|-------------------------------------|
| 진입 규칙 | `generate_v3_overnight_signals` — 거래량 1.5배·시가 대비 종가 ≥4%·꼬리 비율 ≤0.2 | `scan_v3_overnight_candidates_bulk` 내 동일 수식을 **3일분 벌크 OHLC 조인** 후 벡터화 |
| 종목 범위 | `load_v3_0_overnight_scalper_data(..., universe_limit=N)` (**기본 100, 시총 순 상위**) | 조인 결과 **시장 단위 거의 전종목**(로그 상 `total_loaded`가 큼) |
| 기준일 | YAML `period.end_date`부터 시계열을 잘라 **마지막 봉**에 시그널 생성 | `end_date`에 대응하는 **t0 스냅샷**; 로그에 `effective_anchor_date` 표기(장 전·미집계 시 **영업일 1일 자동 후퇴** 가능) |
| 폴백 | 없음(항상 시계열 로더) | 벌크 실패 시 `load_v3_0_overnight_scalper_data` + `universe_limit` 제한 폴백 |

## “둘이 같아야 한다”를 만족시키는 조건

1. **같은 시장** (`universe.market` vs GUI 드롭다운).
2. **같은 유니버스**: GUI가 벌크로 **전체 시장**을 볼 때는 CLI **기본 100**과 **절대 같을 수 없음**. CLI의 `v3_0.universe_limit`과 **동일 N**으로 제한 한 뒤 비교해야 함.
3. **같은 평가일**: 벌크 결과 `stats.effective_anchor_date`가 사용자가 고른 종료일과 다르면(자동 후퇴), CLI도 그날까지 잘린 시계열로 **해당 영업일 행만** 검사해야 일치 검증 가능.
4. **수치 소스**: 이론상 `get_market_ohlcv_by_ticker(일자)`로 만든 행과 `get_market_ohlcv_by_date(구간)`의 **해당 일자 행**이 완전 동일이라는 보장은 KRX/pykrx 쪽에 의존(반올림·스냅샷 차이 가능). 그런 차이가 있으면 **경계 선상** 종목 한두 개 차이만 날 수 있음.

## 자동 검증 스크립트

프로젝트 루트에서(`.env`/KRX 사용 가능 환경):

```powershell
.\venv\Scripts\python scripts\compare_overnight_cli_gui.py --end YYYY-MM-DD
```

- 벌크가 성공하면 `effective_anchor`를 읽고, **`universe_limit`만큼의 동일 유니버스**에서  
  시계열 `buy_signal == 1`(해당 앵커일) 과 벌크 `rows` 코드 집합을 비교합니다.
- **종료 코드 0**: 교집합 유니버스 기준 코드 집합 일치  
- **종료 코드 1**: 불일치(어느 쪽에만 있는 코드 출력)  
- **종료 코드 2**: 벌크 실패(인증·네트워크·pykrx 오류 등)

## 이번 세션 도구 실행 메모

CI/샌드박스에서 스크립트를 돌린 경우 **`ohlcv_bulk_failed`** 또는 KRX 로그인 실패가 나오면 결과 비교 자체가 불가합니다. 사용자 PC에서 로그인·네트워크가 정상일 때 위 명령으로 재실행하면 됩니다.
