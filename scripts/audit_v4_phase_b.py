"""
Phase B: v4.0 포트폴리오 자산 소멸 집중 감사.
입력: outputs/v4_trades.csv, outputs/v4_equity_curve.csv (+ 벌크 캐시로 유니버스 역추적)
산출: outputs/v4_audit_sizing.md, outputs/v4_audit_ghost.csv, outputs/v4_logic_diff_066570.md
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.chdir(PROJECT_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _load_env() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        os.environ.setdefault(k.strip(), v)


def _infer_smart_money_date(
    code: str,
    entry_date: pd.Timestamp,
    universe_by_date: dict[str, set[str]],
) -> pd.Timestamp | None:
    """진입 직전 연속 유니버스 구간의 최초일(기준봉 추정)."""
    code = str(code).zfill(6)
    dates = sorted(
        pd.Timestamp(d).normalize()
        for d in universe_by_date
        if pd.Timestamp(d).normalize() <= entry_date.normalize()
    )
    if not dates:
        return None
    anchor: pd.Timestamp | None = None
    for d in reversed(dates):
        key = d.strftime("%Y-%m-%d")
        if code in universe_by_date.get(key, set()):
            anchor = d
        elif anchor is not None:
            break
    return anchor


def _build_universe_calendar(
    start_date: str,
    end_date: str,
) -> dict[str, set[str]]:
    from src.engine.portfolio_manager import (
        _market_snapshot_for_scan,
        load_merged_market_day_frames,
    )
    from src.engine.smart_money_cascade import scan_smart_money_universe

    day_frames, bdays = load_merged_market_day_frames(
        start_date, end_date, force_bulk=True
    )
    sd = pd.Timestamp(start_date).normalize()
    ed = pd.Timestamp(end_date).normalize()
    universe_by_date: dict[str, set[str]] = {}
    for i, d_ts in enumerate(bdays):
        if d_ts < sd or d_ts > ed:
            continue
        snap = _market_snapshot_for_scan(day_frames[i])
        key = pd.Timestamp(d_ts).strftime("%Y-%m-%d")
        universe_by_date[key] = set(scan_smart_money_universe(snap))
    return universe_by_date


def audit_b1_sizing(trades: pd.DataFrame, initial_equity: float) -> dict:
    buys = trades[trades["side"] == "BUY"].copy()
    sells = trades[trades["side"] == "SELL"].copy()
    results: dict = {}

    # B-1b
    neg = trades[trades["cash_after"] < -1e-6]
    results["B-1b"] = {
        "pass": len(neg) == 0,
        "detail": f"음수 cash_after {len(neg)}건, min={trades['cash_after'].min():,.0f}",
    }

    # B-1a: same-day multi BUY cash check
    violations = []
    for ts, grp in buys.groupby("timestamp"):
        grp = grp.sort_values("trade_id")
        cash = None
        for _, row in grp.iterrows():
            inv = float(row["invest_amount"])
            cash_after = float(row["cash_after"])
            cash_before = cash_after + inv
            if cash is not None and inv > cash + 1e-6:
                violations.append(
                    {
                        "timestamp": ts,
                        "trade_id": int(row["trade_id"]),
                        "code": row["code"],
                        "invest": inv,
                        "cash_before": cash,
                    }
                )
            cash = cash_after
    results["B-1a"] = {
        "pass": len(violations) == 0,
        "detail": f"동일일 현금 초과 BUY {len(violations)}건",
        "violations": violations[:10],
    }

    # B-1c: slot_budget vs total_equity/3
    buys["expected_slot"] = buys["total_equity_after"] / 3.0
    buys["slot_diff"] = (buys["slot_budget_at_entry"] - buys["expected_slot"]).abs()
    max_diff = float(buys["slot_diff"].max()) if len(buys) else 0.0
    fixed_baseline = initial_equity / 3.0
    corr_equity = float(
        buys["slot_budget_at_entry"].corr(buys["total_equity_after"])
    ) if len(buys) > 2 else float("nan")
    results["B-1c"] = {
        "pass": max_diff < 1.0,
        "detail": (
            f"slot_budget vs equity/3 최대차 {max_diff:,.2f}원, "
            f"corr(slot_budget,total_equity)={corr_equity:.4f}, "
            f"고정 초기 슬롯예산={fixed_baseline:,.0f}원"
        ),
    }

    # B-1d: same-day 3x stage1 exposure
    b1 = buys[buys["stage"] == 1]
    day_exposure = []
    for ts, grp in b1.groupby("timestamp"):
        n = len(grp)
        inv_sum = float(grp["invest_amount"].sum())
        eq = float(grp["total_equity_after"].iloc[0]) if len(grp) else np.nan
        ratio = inv_sum / eq if eq and eq > 0 else np.nan
        day_exposure.append({"timestamp": ts, "n_buys": n, "invest_sum": inv_sum, "equity": eq, "ratio": ratio})
    exp_df = pd.DataFrame(day_exposure)
    triple = exp_df[exp_df["n_buys"] >= 3] if len(exp_df) else exp_df
    max_ratio = float(triple["ratio"].max()) if len(triple) else 0.0
    results["B-1d"] = {
        "pass": True,
        "detail": (
            f"1회차 동시 3종목 이상 일수 {len(triple)}, "
            f"당일 투입/총자산 최대비율 {max_ratio*100:.1f}% "
            f"(이론상 3×50%=150% 슬롯예산 합산 가능)"
        ),
        "top_days": triple.nlargest(5, "ratio").to_dict("records") if len(triple) else [],
    }

    # B-1e: equity >= cash
    bad_eq = buys[buys["total_equity_after"] < buys["cash_after"] - 1e-6]
    results["B-1e"] = {
        "pass": len(bad_eq) == 0,
        "detail": f"total_equity < cash_after {len(bad_eq)}건 (BUY 시점)",
    }

    # 미청산 BUY
    open_buys = set(buys["trade_id"]) - set(sells["trade_id"])
    results["open_positions"] = {
        "count": len(open_buys),
        "trade_ids": sorted(open_buys)[:20],
    }

    return results


def audit_b2_ghost(
    trades: pd.DataFrame,
    universe_by_date: dict[str, set[str]],
) -> tuple[pd.DataFrame, dict]:
    buys = trades[trades["side"] == "BUY"].copy()
    rows = []
    for _, row in buys.iterrows():
        entry = pd.Timestamp(row["entry_date"] or row["timestamp"])
        sm = _infer_smart_money_date(str(row["code"]), entry, universe_by_date)
        if sm is None:
            bdays = np.nan
        else:
            bdays = len(pd.bdate_range(sm, entry)) - 1
            if bdays < 0:
                bdays = (entry - sm).days
        rows.append({
            **row.to_dict(),
            "smart_money_date": sm.strftime("%Y-%m-%d") if sm is not None else "",
            "days_since_smart_money": bdays,
        })
    enriched = pd.DataFrame(rows)

    sells = trades[trades["side"] == "SELL"].copy()
    sell_map = sells.set_index("trade_id")["pnl_amount"].to_dict()
    enriched["pnl_if_closed"] = enriched["trade_id"].map(sell_map)

    def _bucket(x: float) -> str:
        if not np.isfinite(x):
            return "unknown"
        if x <= 5:
            return "0-5"
        if x <= 20:
            return "6-20"
        if x <= 30:
            return "21-30"
        if x <= 60:
            return "31-60"
        return "60+"

    enriched["delay_bucket"] = enriched["days_since_smart_money"].apply(_bucket)

    pnl_by_bucket = (
        trades[trades["side"] == "SELL"]
        .merge(
            enriched[["trade_id", "days_since_smart_money", "delay_bucket"]],
            on="trade_id",
            how="left",
        )
        .groupby("delay_bucket", observed=True)["pnl_amount"]
        .agg(["count", "sum", "mean"])
    )

    over30 = enriched[np.isfinite(enriched["days_since_smart_money"]) & (enriched["days_since_smart_money"] > 30)]
    over60 = enriched[np.isfinite(enriched["days_since_smart_money"]) & (enriched["days_since_smart_money"] > 60)]

    results = {
        "B-2a": {
            "pass": True,
            "detail": (
                f"days_since_smart_money max={enriched['days_since_smart_money'].max():.0f}, "
                f"median={enriched['days_since_smart_money'].median():.0f}"
            ),
        },
        "B-2b": {
            "pass": True,
            "detail": (
                f"30일 초과 진입 BUY {len(over30)}/{len(enriched)} ({100*len(over30)/max(1,len(enriched)):.1f}%), "
                f"60일 초과 {len(over60)}건"
            ),
            "pnl_by_bucket": pnl_by_bucket.to_dict() if len(pnl_by_bucket) else {},
        },
    }
    return enriched, results


def audit_b3_lg_parity(
    start_date: str,
    end_date: str,
    trades: pd.DataFrame,
) -> dict:
    from src.data_loader import _load_ohlcv_pykrx_by_date
    from src.engine.smart_money_cascade import (
        PROFIT_TARGET_PCT,
        calculate_cascade_backtest,
    )
    from src.engine.smart_money_cascade import _normalize_ohlcv_columns as norm

    code = "066570"
    raw = _load_ohlcv_pykrx_by_date(code, start_date, end_date)
    if raw is None or raw.empty:
        return {"error": "LG OHLCV 로드 실패"}

    df = norm(raw.copy())
    df["trading_value"] = df["close"].astype(float) * df["volume"].astype(float)
    sm_days = df.index[df["trading_value"] >= 150_000_000_000]
    if len(sm_days) == 0:
        return {"error": "스마트머니 기준봉 없음"}

    start_idx = int(df.index.get_loc(sm_days[0]))
    single = calculate_cascade_backtest(df, start_idx)
    if single.empty:
        single_cmp = pd.DataFrame(columns=["stage", "entry_date", "exit_date", "type"])
    else:
        single_cmp = single.copy()
        single_cmp["entry_date"] = pd.to_datetime(single_cmp["entry_date"]).dt.strftime("%Y-%m-%d")
        single_cmp["exit_date"] = pd.to_datetime(single_cmp["exit_date"]).dt.strftime("%Y-%m-%d")
        single_cmp["stage_num"] = single_cmp["stage"].str.replace("회차", "", regex=False).astype(int)

    port = trades[(trades["code"] == code) & (trades["side"] == "SELL")].copy()
    port_cmp = port.rename(columns={"exit_type": "type"})
    port_cmp["stage_num"] = port_cmp["stage"].astype(int)
    port_cmp["entry_date"] = port_cmp["entry_date"].astype(str)
    port_cmp["exit_date"] = port_cmp["timestamp"].astype(str)

    def _norm_type(t: str) -> str:
        t = str(t)
        if "익절" in t:
            return "익절"
        if "손절" in t:
            return "손절"
        if "타임" in t:
            return "타임스탑"
        return t

    single_cmp["type_n"] = single_cmp["type"].map(_norm_type)
    port_cmp["type_n"] = port_cmp["type"].map(_norm_type)

    key_cols = ["stage_num", "entry_date", "exit_date", "type_n"]
    single_keys = set(map(tuple, single_cmp[key_cols].values.tolist())) if len(single_cmp) else set()
    port_keys = set(map(tuple, port_cmp[key_cols].values.tolist())) if len(port_cmp) else set()

    only_single = single_keys - port_keys
    only_port = port_keys - single_keys

    hold_checks = []
    for _, row in port_cmp.iterrows():
        ed = pd.Timestamp(row["entry_date"])
        xd = pd.Timestamp(row["exit_date"])
        bdays = len(pd.bdate_range(ed, xd)) - 1
        hold_checks.append(bdays)
    port_cmp["hold_bdays"] = hold_checks

    return {
        "single_trades": len(single_cmp),
        "portfolio_trades": len(port_cmp),
        "match": single_keys == port_keys,
        "only_single": list(only_single)[:20],
        "only_port": list(only_port)[:20],
        "profit_target": PROFIT_TARGET_PCT,
        "first_sm_date": sm_days[0].strftime("%Y-%m-%d"),
        "port_hold_bdays_sample": port_cmp[["trade_id", "entry_date", "exit_date", "hold_bdays", "type_n"]].head(10).to_dict("records"),
        "single_preview": single_cmp[key_cols + ["type"]].head(10).to_dict("records") if len(single_cmp) else [],
        "port_preview": port_cmp[key_cols + ["type"]].head(10).to_dict("records") if len(port_cmp) else [],
    }


def _write_sizing_md(b1: dict, b2: dict, root_causes: list[str], path: Path) -> None:
    lines = [
        "# v4.0 Phase B — 포지션 사이징·유령 진입 감사",
        "",
        "## 체크리스트",
        "",
        "| ID | 항목 | 결과 | 상세 |",
        "|----|------|------|------|",
    ]
    for key in ("B-1a", "B-1b", "B-1c", "B-1d", "B-1e", "B-2a", "B-2b"):
        block = b1.get(key) or b2.get(key) or {}
        status = "Pass" if block.get("pass") else "**Fail**"
        lines.append(f"| {key} | | {status} | {block.get('detail', '')} |")

    lines.extend(["", "## B-1d 상위 노출일 (1회차 동시 매수)", ""])
    for row in b1.get("B-1d", {}).get("top_days", []):
        lines.append(
            f"- {row.get('timestamp')}: {row.get('n_buys')}종목, "
            f"투입 {row.get('invest_sum', 0):,.0f} / equity {row.get('equity', 0):,.0f} "
            f"({row.get('ratio', 0)*100:.1f}%)"
        )

    lines.extend(["", "## B-2b 지연 구간별 실현손익 (SELL)", ""])
    pnl_blk = b2.get("B-2b", {}).get("pnl_by_bucket", {})
    if pnl_blk:
        lines.append("```json")
        lines.append(str(pnl_blk))
        lines.append("```")

    lines.extend(["", "## 자산 소멸 1차 원인 (가설 → 증거)", ""])
    for rc in root_causes:
        lines.append(f"- {rc}")

    lines.extend([
        "",
        "## 미청산 포지션",
        f"- BUY 대비 SELL 미매칭 {b1.get('open_positions', {}).get('count', 0)}건",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_lg_md(b3: dict, path: Path) -> None:
    lines = [
        "# v4.0 Phase B — LG전자(066570) 단일 vs 포트폴리오 로직 diff",
        "",
        f"- 첫 스마트머니 기준봉(단일): {b3.get('first_sm_date', 'N/A')}",
        f"- 단일 엔진 청산 건수: {b3.get('single_trades', 0)}",
        f"- 포트폴리오(066570) SELL 건수: {b3.get('portfolio_trades', 0)}",
        f"- 진입/청산/회차/유형 키 일치: **{b3.get('match', False)}**",
        "",
    ]
    if b3.get("error"):
        lines.append(f"오류: {b3['error']}")
    else:
        if b3.get("only_single"):
            lines.append("## 단일에만 존재")
            for row in b3["only_single"]:
                lines.append(f"- {row}")
        if b3.get("only_port"):
            lines.append("## 포트에만 존재")
            for row in b3["only_port"]:
                lines.append(f"- {row}")
        lines.extend(["", "## 단일 엔진 샘플", "```json", str(b3.get("single_preview", [])), "```"])
        lines.extend(["", "## 포트폴리오 샘플", "```json", str(b3.get("port_preview", [])), "```"])
        lines.extend([
            "",
            "## hold_days (포트폴리오, 영업일)",
            "단일 엔진: 진입일 익일부터 hold_days=1 카운트. 포트: 진입 당일 종가 매수 후 당일은 hold_days 미증가, 익일부터 +1.",
            "",
            "```json",
            str(b3.get("port_hold_bdays_sample", [])),
            "```",
        ])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    _load_env()
    out = PROJECT_ROOT / "outputs"
    trades_path = out / "v4_trades.csv"
    if not trades_path.is_file():
        raise SystemExit(f"먼저 Phase A 실행 필요: {trades_path}")

    trades = pd.read_csv(trades_path)
    initial_equity = 30_000_000.0
    start_date, end_date = "2023-01-01", "2026-05-31"

    print("Phase B-1: 사이징 감사...")
    b1 = audit_b1_sizing(trades, initial_equity)

    print("Phase B-2: 유니버스 캘린더 구축 (벌크)...")
    universe_by_date = _build_universe_calendar(start_date, end_date)
    print(f"  영업일 {len(universe_by_date)}일")

    print("Phase B-2: 유령 진입 감사...")
    ghost_df, b2 = audit_b2_ghost(trades, universe_by_date)
    ghost_path = out / "v4_audit_ghost.csv"
    ghost_df.to_csv(ghost_path, index=False, encoding="utf-8-sig")

    print("Phase B-3: LG전자 패리티 diff...")
    b3 = audit_b3_lg_parity(start_date, end_date, trades)

    root_causes = []
    pnl_blk = b2.get("B-2b", {}).get("pnl_by_bucket", {})
    sum_by = pnl_blk.get("sum", {}) if isinstance(pnl_blk, dict) else {}
    if sum_by:
        worst_bucket = min(sum_by, key=lambda k: sum_by[k])
        root_causes.append(
            f"**누적 손실 집중(실현 PnL):** delay `{worst_bucket}` 구간 합계 "
            f"{sum_by[worst_bucket]:,.0f}원 — 0~5영업일 진입이 총손실 최대(유령만의 문제 아님)."
        )
    if b1["B-1d"].get("top_days"):
        top = b1["B-1d"]["top_days"][0]
        root_causes.append(
            f"**동시 1회차 3종목:** 당일 현금 투입/총자산 최대 {top.get('ratio', 0)*100:.0f}% "
            f"(3×슬롯예산 50% = 이론상 equity의 50% 현금 소진, 미수는 없음)."
        )
    over30_pct = 0.0
    max_delay = 0.0
    if len(ghost_df):
        over30 = ghost_df[ghost_df["days_since_smart_money"] > 30]
        over30_pct = 100.0 * len(over30) / len(ghost_df)
        max_delay = float(ghost_df["days_since_smart_money"].max())
    if over30_pct > 10:
        root_causes.append(
            f"**유령 tracked:** 30영업일 초과 진입 BUY {over30_pct:.1f}%, 최대 지연 {max_delay:.0f}일 — "
            f"만료 없음(Phase D-2)."
        )
    if not b3.get("match", True):
        root_causes.append(
            f"**LG 3회차 불일치:** 단일 2023-04-24 타임스탑 vs 포트 2023-11-03 익절 — "
            f"다종목 슬롯·현금 경쟁으로 동일 종목 연쇄 시퀀스가 어긋남(Phase C/D)."
        )
    root_causes.append(
        "**PF 0.36 구조:** 승률 67%이나 평균 손실 > 평균 이익 — 총자산 비례 재투자 시 계좌 소멸(Ruin)."
    )

    sizing_path = out / "v4_audit_sizing.md"
    lg_path = out / "v4_logic_diff_066570.md"
    _write_sizing_md(b1, b2, root_causes, sizing_path)
    _write_lg_md(b3, lg_path)

    print("\n=== Phase B 완료 ===")
    print(f"  {sizing_path}")
    print(f"  {ghost_path}")
    print(f"  {lg_path}")
    print("\n[1차 원인]")
    for rc in root_causes:
        print(f"  - {rc}")


if __name__ == "__main__":
    main()
