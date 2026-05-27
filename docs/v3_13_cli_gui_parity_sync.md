# v3.13 CLI/GUI 오버나이트 Parity 동기화

## 변경 요약

1. **`v3_0.universe_limit`(기본 100)**  
   - `scan_v3_overnight_candidates_bulk(..., universe_limit=…)`에서 시장 조인 후 **앵커일 시총**으로 소팅·상단 N종목만 적용하여 CLI 로더와 유니버스 체계를 통일했다.

2. **기준일 단일 모듈**  
   - `src/utils/date_helper.py` 의 `resolve_overnight_scan_anchor()` 가  
     종료일 문자열 **+ 현재 서울 시각**(장전/장중≤15:30/장후/주말/미래클램프) 규칙으로 **t0·prev·prev2** 를 한 군데서만 계산한다.  
   - 벌크 경로 및 CLI 로드 종료일(`end_load`) 연동되며, 과거 과도한 거래량-휴리스틱 앵커 시프트는 제거했다.

3. **CLI `--mode cli`**  
   - `period.end_date` 에 대해 앵커를 해석한 `end_load` 까지만 시계열을 로드하고, 종료 블록에 **동일 벌크**로 만든 패리티 리스트를 출력한다.

4. **GUI 스캐너**  
   - `universe_limit` 를 YAML과 동일 값으로 벌크에 전달하고, 디버그에 `anchor_policy_reason` 노출한다.  
   - 질적 순서 안정화: 종목별 상승률 동순위 시 **종목 코드 기준 안정 소트**.

5. **검증**  
   - `src/overnight_parity.py` → `scripts/compare_overnight_cli_gui.py` 재사용  
   - `tests/test_overnight_parity.py` 에 앵커 단위 테스트(기본)·`RUN_KRX_INTEGRATION=1` 선택적 라이브 통합

```powershell
python -m unittest discover -s tests -p "test*.py" -v
$env:RUN_KRX_INTEGRATION=1   # 선택
python -m unittest discover -s tests -p "test*.py" -v
.\venv\Scripts\python scripts\compare_overnight_cli_gui.py --end YYYY-MM-DD
```

## 한계 메모

- **공식 KRX 교체 휴장 목록** 미반영 pandas `BDay` 기반이므로 명절 교체 거래일은 추후 확장 여지 있다.
