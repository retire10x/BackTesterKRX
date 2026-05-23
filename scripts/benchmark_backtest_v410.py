#!/usr/bin/env python3
"""
v4.10 성능 비교: 동기 차트 PNG vs defer(시뮬·통계만) + 후속 materialize.

네트워크 캐시 히트 효과를 보려면 2회차 동기 시간을 참고합니다.
실행: 프로젝트 루트에서 `python scripts/benchmark_backtest_v410.py`
"""
from __future__ import annotations

import time
from copy import deepcopy


def main() -> None:
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from src.data_loader import clear_ohlcv_cache, load_config
    from src.metrics import materialize_backtest_chart_png, run_backtest_detailed

    cfg = load_config()
    cfg.setdefault("universe", {})["selected_code"] = "005930"

    clear_ohlcv_cache()

    print("[1] sync full chart (defer=False), cold-ish run")
    t0 = time.perf_counter()
    r_full = run_backtest_detailed(deepcopy(cfg), defer_chart_render=False)
    t_sync_cold = time.perf_counter() - t0
    print(f"    ok={r_full.ok} report={r_full.report_path} sec={t_sync_cold:.3f}")

    print("[2] sync full chart, rerun (listing/OHLCV cache)")
    t0 = time.perf_counter()
    r_full2 = run_backtest_detailed(deepcopy(cfg), defer_chart_render=False)
    t_sync_warm = time.perf_counter() - t0
    print(f"    sec={t_sync_warm:.3f}")

    print("[3] defer (sim + summary, skip PNG/debug log)")
    t0 = time.perf_counter()
    r_def = run_backtest_detailed(
        deepcopy(cfg),
        defer_chart_render=True,
        write_signal_debug_log=False,
    )
    t_defer = time.perf_counter() - t0
    print(
        f"    ok={r_def.ok} chart_render_pending={r_def.chart_render_pending} "
        f"sec={t_defer:.3f}"
    )

    t_mat = 0.0
    rp = r_def.replay_chart
    print("[4] materialize_backtest_chart_png from replay")
    if isinstance(rp, dict) and rp:
        t0 = time.perf_counter()
        outp, skipped = materialize_backtest_chart_png(
            rp,
            chart_render_px=None,
            write_signal_debug_log=False,
        )
        t_mat = time.perf_counter() - t0
        print(f"    path={outp} skipped={skipped} sec={t_mat:.3f}")

    tot_split = t_defer + t_mat
    print("\n=== summary (seconds) ===")
    print(f"  sync cold (1st)     : {t_sync_cold:.3f}")
    print(f"  sync warm (2nd)      : {t_sync_warm:.3f}")
    print(f"  defer (sim only)      : {t_defer:.3f}")
    print(f"  + materialize (PNG)  : {t_mat:.3f}")
    print(f"  defer+materialize sum: {tot_split:.3f}")
    print(
        "  GUI: summary appears after defer; chart paints after materialize "
        "(perceived latency reduced)."
    )


if __name__ == "__main__":
    main()
