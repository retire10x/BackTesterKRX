"""
v3.13 Parity: `date_helper` 앵커 규칙 + 통합 정합성 헬퍼 import-only smoke.
네트워크가 필요한 검증은 환경변수 RUN_KRX_INTEGRATION=1 일 때만 수행한다.
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime

from src.utils.date_helper import resolve_overnight_scan_anchor


class TestOvernightAnchorKst(unittest.TestCase):
    """KST 기준 시각 × 요청 종료일 → t0 규격."""

    def test_pre_open_uses_prior_session(self) -> None:
        from datetime import timezone, timedelta

        kst = timezone(timedelta(hours=9))
        ref = datetime(2026, 5, 28, 8, 30, tzinfo=kst)
        a = resolve_overnight_scan_anchor("2026-05-28", reference_now=ref)
        self.assertEqual(str(a.anchor_date), "2026-05-27")

    def test_post_regular_close_keeps_calendar_day(self) -> None:
        from datetime import timezone, timedelta

        kst = timezone(timedelta(hours=9))
        ref = datetime(2026, 5, 28, 16, 0, tzinfo=kst)
        a = resolve_overnight_scan_anchor("2026-05-28", reference_now=ref)
        self.assertEqual(str(a.anchor_date), "2026-05-28")

    def test_intraday_before_final_close_uses_prior(self) -> None:
        """15:30 이하(종가 포함 일봉 미확정 가정): 직전 영업일."""

        from datetime import timezone, timedelta

        kst = timezone(timedelta(hours=9))
        ref = datetime(2026, 5, 28, 14, 0, tzinfo=kst)
        a = resolve_overnight_scan_anchor("2026-05-28", reference_now=ref)
        self.assertEqual(str(a.anchor_date), "2026-05-27")

    def test_weekend_recenters_to_prior_bd(self) -> None:
        from datetime import timezone, timedelta

        kst = timezone(timedelta(hours=9))
        ref = datetime(2026, 5, 30, 10, 0, tzinfo=kst)  # 토요일
        a = resolve_overnight_scan_anchor("2026-05-30", reference_now=ref)
        self.assertEqual(str(a.anchor_date), "2026-05-29")
        self.assertEqual(str(a.prev_1), "2026-05-28")
        self.assertEqual(str(a.prev_2), "2026-05-27")


class TestOvernightParityImport(unittest.TestCase):
    def test_parity_runner_importable(self) -> None:
        from src import overnight_parity as op

        self.assertTrue(callable(op.run_overnight_parity_check))


@unittest.skipUnless(os.getenv("RUN_KRX_INTEGRATION") == "1", "네트워크/KRX 필요")
class TestOvernightParityLive(unittest.TestCase):
    def test_run_parity_maybe_zero(self) -> None:
        from pathlib import Path

        from src.data_loader import default_backtest_period_range, load_config
        from src.overnight_parity import prime_project_dotenv_from_root, run_overnight_parity_check

        prime_project_dotenv_from_root(Path(__file__).resolve().parents[1])
        cfg = load_config()
        uni = cfg.get("universe") or {}
        market = str(uni.get("market") or "KOSPI").strip().upper()
        if market not in ("KOSPI", "KOSDAQ"):
            market = "KOSPI"
        v3_cfg = cfg.get("v3_0") or {}
        limit = max(20, min(300, int(v3_cfg.get("universe_limit", 300))))
        period = cfg.get("period") or {}
        end_eff = str(period.get("end_date") or "").strip()[:10]
        if not end_eff:
            _, end_d_fallback = default_backtest_period_range()
            end_eff = end_d_fallback.strftime("%Y-%m-%d")

        code, lines = run_overnight_parity_check(
            requested_end=end_eff,
            market=market,
            universe_limit=limit,
        )
        self.assertIn(code, (0, 1, 2))
        print("\n" + "\n".join(lines))
        self.assertEqual(code, 0, msg="통합 테스트는 exit 0이어야 최종 통과")
