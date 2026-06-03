# 릴레이 유니버스 산출물 (Git 미추적)

`univ_phase_*.json` · `relay_manifest.json` 은 **재생성 가능**하므로 저장소에 올리지 않습니다.

```powershell
python run_v5_relay_portfolio.py --scan-only
```

SSOT: `config/settings.yaml` `v5_5.screener` · 구간별 lock_date는 `src/v5_relay_screener.py` `RELAY_PHASES`.
