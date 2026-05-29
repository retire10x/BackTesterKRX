"""
CLI vs GUI 오버나이트 스캐너 명단 정합성 검증(네트워크·KRX 인증 필요 시 exit 2 가능).
`scripts/compare_overnight_cli_gui.py` 및 유닛 테스트에서 공통 진입점으로 사용한다.
"""

from __future__ import annotations

import os
from pathlib import Path


def prime_project_dotenv_from_root(root: Path | None = None) -> None:
    """KRX_ID/KRX_PW 미설정 시 프로젝트 루트 `.env`를 최소 파싱하여 주입한다."""
    if str(os.getenv("KRX_ID") or "").strip() and str(os.getenv("KRX_PW") or "").strip():
        return
    base = root or Path(__file__).resolve().parents[1]
    p = base / ".env"
    if not p.is_file():
        return
    try:
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            key = str(k).strip()
            val = str(v).strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except OSError:
        pass


def run_overnight_parity_check(
    *,
    requested_end: str,
    market: str,
    universe_limit: int,
) -> tuple[int, list[str]]:
    """
    :return: (exit_code, 로그 줄) — 0=집합·정렬 일치, 1=불일치, 2=벌크/환경 실패
    """
    import pandas as pd
    from pandas.tseries.offsets import BDay

    from src.data_loader import (
        load_v3_0_overnight_scalper_data,
        scan_leader_pullback_candidates_bulk,
    )
    from src.utils.date_helper import resolve_overnight_scan_anchor
    from src.v3_signal_generator import generate_v3_overnight_signals

    lines: list[str] = []
    end_eff = str(requested_end).strip()[:10]
    lim = max(20, min(300, int(universe_limit)))

    info = resolve_overnight_scan_anchor(end_eff)
    lines.append(
        f"[v3.13] requested={info.requested_calendar_date} t0={info.anchor_date} "
        f"policy={info.anchor_policy_reason}"
    )

    v3_cfg = {}
    try:
        from src.data_loader import load_config

        v3_cfg = load_config().get("v3_0") or {}
    except Exception:
        pass
    burst = float(v3_cfg.get("volume_burst_multiple", 3.0))
    shrink = float(v3_cfg.get("vol_shrink_limit", 0.5))

    bulk = scan_leader_pullback_candidates_bulk(
        end_eff,
        market=market,
        universe_limit=lim,
        cancel_event=None,
        volume_burst_multiple=burst,
        vol_shrink_limit=shrink,
    )
    if not bulk.get("ok"):
        lines.append(f"[bulk] FAILED: {bulk.get('reason')}")
        return 2, lines

    st = bulk.get("stats") or {}
    effective = str(st.get("effective_anchor_date") or info.anchor_date.strftime("%Y-%m-%d")).strip()[
        :10
    ]
    warm_start = (pd.Timestamp(effective) - BDay(15)).strftime("%Y-%m-%d")

    rows = bulk.get("rows") or []
    gui_codes = [str(t[0]).zfill(6) for t in rows]
    gui_set = set(gui_codes)
    rise_from_bulk = {str(t[0]).zfill(6): float(t[1]) for t in rows}

    items = load_v3_0_overnight_scalper_data(
        start_date=warm_start,
        end_date=effective,
        market=market,
        universe_limit=lim,
    )
    eff_ts = pd.Timestamp(effective).normalize()

    cli_pick: list[str] = []
    cli_set: set[str] = set()
    for code, df in items:
        c6 = str(code).zfill(6)
        sig = generate_v3_overnight_signals(df)
        if sig.empty or "buy_signal" not in sig.columns:
            continue
        idx_n = pd.DatetimeIndex(sig.index).normalize()
        mask = idx_n == eff_ts
        if not bool(mask.any()):
            continue
        row = sig.loc[mask].iloc[-1]
        if int(row.get("buy_signal", 0) or 0) != 1:
            continue
        cli_pick.append(c6)
        cli_set.add(c6)

    cli_sorted_expect = sorted(
        cli_set,
        key=lambda c: (-float(rise_from_bulk.get(c, -1e18)), c),
    )

    lines.append(f"effective_anchor={effective} universe_limit_applied={st.get('universe_limit_applied', lim)}")
    lines.append(
        f"bulk_rows={len(gui_codes)} cli_buy_at_t0={len(cli_pick)} items_loaded={len(items)}"
    )

    only_cli = sorted(cli_set - gui_set)
    only_bulk = sorted(gui_set - cli_set)

    if only_cli:
        lines.append(f"only_cli({len(only_cli)}): {only_cli}")
    if only_bulk:
        lines.append(f"only_bulk({len(only_bulk)}): {only_bulk}")

    if only_cli or only_bulk:
        lines.append("RESULT: SET_MISMATCH")
        return 1, lines

    if gui_codes != cli_sorted_expect:
        lines.append(f"RESULT: ORDER_MISMATCH gui={gui_codes} expect_sort={cli_sorted_expect}")
        return 1, lines

    lines.append("RESULT: PASS (set + return_pct-desc order)")
    return 0, lines
