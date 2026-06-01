"""
Phase C: LG전자(066570) 단일 엔진 vs 격리 포트폴리오 패리티 검증.
산출: outputs/v4_parity_single_066570.csv, v4_parity_portfolio_066570.csv, v4_parity_report.md
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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
        os.environ.setdefault(k, v)


CODE = "066570"
START_DATE = "2023-01-01"
END_DATE = "2026-05-31"
OUT = PROJECT_ROOT / "outputs"


def find_first_smart_money_anchor(
    day_frames: list,
    bdays: pd.DatetimeIndex,
    code: str,
    sim_start_idx: int,
    sim_end_idx: int,
) -> tuple[int, pd.Timestamp] | tuple[None, None]:
    from src.engine.portfolio_manager import _market_snapshot_for_scan
    from src.engine.smart_money_cascade import scan_smart_money_universe

    c6 = str(code).zfill(6)
    for day_idx in range(sim_start_idx, sim_end_idx + 1):
        snap = _market_snapshot_for_scan(day_frames[day_idx])
        if c6 in scan_smart_money_universe(snap):
            return day_idx, pd.Timestamp(bdays[day_idx]).normalize()
    return None, None


def run_single_cascade(df: pd.DataFrame, anchor_date: pd.Timestamp) -> pd.DataFrame:
    from src.engine.smart_money_cascade import calculate_cascade_backtest

    start_idx = int(df.index.get_loc(anchor_date))
    raw = calculate_cascade_backtest(df, start_idx)
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["stage_num", "entry_date", "exit_date", "type", "pnl"])

    out = raw.copy()
    out["entry_date"] = pd.to_datetime(out["entry_date"]).dt.strftime("%Y-%m-%d")
    out["exit_date"] = pd.to_datetime(out["exit_date"]).dt.strftime("%Y-%m-%d")
    out["stage_num"] = out["stage"].astype(str).str.replace("회차", "", regex=False).astype(int)
    out["type"] = out["type"].astype(str)
    return out[["stage_num", "entry_date", "exit_date", "type", "pnl"]]


def run_isolated_portfolio(
    day_frames: list,
    bdays: pd.DatetimeIndex,
    df_stock: pd.DataFrame,
) -> pd.DataFrame:
    from src.engine.portfolio_manager import PortfolioManager
    from src.v4_config import load_v4_config

    v4 = load_v4_config()
    manager = PortfolioManager(
        day_frames,
        bdays,
        start_date=START_DATE,
        end_date=END_DATE,
        max_slots=1,
        v4_config=v4,
        phase_g_mode=False,
        allowed_codes=frozenset({CODE}),
        anchor_first_smart_money_only=True,
        preload_ohlcv={CODE: df_stock},
    )
    result = manager.run()
    sells = result.trades_detail[result.trades_detail["side"] == "SELL"].copy()
    if sells.empty:
        return pd.DataFrame(columns=["stage_num", "entry_date", "exit_date", "type", "pnl_rate"])

    out = pd.DataFrame({
        "stage_num": sells["stage"].astype(int),
        "entry_date": sells["entry_date"].astype(str),
        "exit_date": sells["timestamp"].astype(str),
        "type": sells["exit_type"].astype(str),
        "pnl": sells["pnl_rate"].astype(float),
    })
    return out


def _norm_type(t: str) -> str:
    t = str(t)
    if "익절" in t:
        return "익절"
    if "손절" in t:
        return "손절"
    if "타임" in t:
        return "타임스탑"
    return t


def compare_frames(single: pd.DataFrame, port: pd.DataFrame) -> dict:
    for df in (single, port):
        df["type_n"] = df["type"].map(_norm_type)
        df["key"] = list(
            zip(
                df["stage_num"].astype(int),
                df["entry_date"].astype(str),
                df["exit_date"].astype(str),
                df["type_n"].astype(str),
            )
        )

    single_keys = set(single["key"].tolist()) if len(single) else set()
    port_keys = set(port["key"].tolist()) if len(port) else set()

    pnl_diff = []
    if len(single) == len(port) and len(single) > 0:
        merged = single.merge(
            port,
            on=["stage_num", "entry_date", "exit_date", "type_n"],
            suffixes=("_single", "_port"),
            how="outer",
        )
        if "pnl_single" in merged.columns and "pnl_port" in merged.columns:
            merged["pnl_diff"] = (
                pd.to_numeric(merged["pnl_port"], errors="coerce")
                - pd.to_numeric(merged["pnl_single"], errors="coerce")
            )
            pnl_diff = merged[merged["pnl_diff"].abs() > 1e-6][
                ["stage_num", "entry_date", "pnl_single", "pnl_port", "pnl_diff"]
            ].to_dict("records")

    return {
        "match": single_keys == port_keys,
        "single_count": len(single),
        "port_count": len(port),
        "only_single": sorted(single_keys - port_keys),
        "only_port": sorted(port_keys - single_keys),
        "pnl_mismatch": pnl_diff,
    }


def write_report(
    anchor_date: pd.Timestamp,
    cmp: dict,
    single: pd.DataFrame,
    port: pd.DataFrame,
    path: Path,
) -> None:
    lines = [
        "# v4.0 Phase C — LG전자(066570) 패리티 리포트",
        "",
        f"- 기간: {START_DATE} ~ {END_DATE}",
        f"- 스마트머니 앵커(Top20+1,500억, 벌크 기준): **{anchor_date.strftime('%Y-%m-%d')}**",
        f"- 격리 포트: `allowed_codes={{066570}}`, `max_slots=1`, `anchor_first_smart_money_only=True`, OHLCV 선적재",
        "",
        "## 결과",
        "",
        f"| 항목 | 값 |",
        f"|------|-----|",
        f"| 단일 엔진 청산 건수 | {cmp['single_count']} |",
        f"| 격리 포트 SELL 건수 | {cmp['port_count']} |",
        f"| 일자·회차·유형 키 일치 | **{cmp['match']}** |",
        "",
    ]
    if cmp["only_single"]:
        lines.append("## 단일에만 존재")
        for row in cmp["only_single"]:
            lines.append(f"- {row}")
        lines.append("")
    if cmp["only_port"]:
        lines.append("## 포트에만 존재")
        for row in cmp["only_port"]:
            lines.append(f"- {row}")
        lines.append("")
    if cmp["pnl_mismatch"]:
        lines.append("## PnL 비율 불일치 (동일 키)")
        for row in cmp["pnl_mismatch"]:
            lines.append(f"- {row}")
        lines.append("")

    lines.extend(["## 단일 엔진", "```", single.to_string(index=False), "```", ""])
    lines.extend(["## 격리 포트폴리오", "```", port.to_string(index=False), "```", ""])

    if cmp["match"]:
        lines.append("## 결론")
        lines.append("격리 조건에서 **진입/청산 시퀀스 동치** — Phase D는 자금·다종목 경쟁 패치로 진행.")
    else:
        lines.append("## 결론")
        lines.append("시퀀스 불일치 잔존 — `evaluate_daily_exit` vs `_resolve_exit` 추가 정렬(Phase D-4) 필요.")

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    _load_env()
    OUT.mkdir(parents=True, exist_ok=True)

    from src.data_loader import _load_ohlcv_pykrx_by_date
    from src.engine.portfolio_manager import load_merged_market_day_frames
    from src.engine.smart_money_cascade import _normalize_ohlcv_columns

    print("Phase C: 벌크 로드...")
    day_frames, bdays = load_merged_market_day_frames(
        START_DATE, END_DATE, force_bulk=True
    )
    sim_start_idx = int(bdays.get_indexer([START_DATE], method="bfill")[0])
    sim_end_idx = int(bdays.get_indexer([END_DATE], method="ffill")[0])

    anchor = find_first_smart_money_anchor(
        day_frames, bdays, CODE, sim_start_idx, sim_end_idx
    )
    if anchor[0] is None:
        raise SystemExit(f"{CODE} 스마트머니(Top20+1,500억) 앵커일 없음")

    anchor_idx, anchor_date = anchor
    print(f"앵커일: {anchor_date.strftime('%Y-%m-%d')} (day_idx={anchor_idx})")

    raw = _load_ohlcv_pykrx_by_date(CODE, START_DATE, END_DATE)
    if raw is None or raw.empty:
        raise SystemExit("LG OHLCV 로드 실패")
    df = _normalize_ohlcv_columns(raw)
    df.index = pd.DatetimeIndex(df.index).normalize()

    print("단일 엔진(calculate_cascade_backtest)...")
    single = run_single_cascade(df, anchor_date)
    single_path = OUT / "v4_parity_single_066570.csv"
    single.to_csv(single_path, index=False, encoding="utf-8-sig")

    print("격리 포트폴리오...")
    port = run_isolated_portfolio(day_frames, bdays, df)
    port_path = OUT / "v4_parity_portfolio_066570.csv"
    port.to_csv(port_path, index=False, encoding="utf-8-sig")

    cmp = compare_frames(single.copy(), port.copy())
    report_path = OUT / "v4_parity_report.md"
    write_report(anchor_date, cmp, single, port, report_path)

    print("\n=== Phase C 완료 ===")
    print(f"  {single_path}")
    print(f"  {port_path}")
    print(f"  {report_path}")
    print(f"  키 일치: {cmp['match']} (단일 {cmp['single_count']} / 포트 {cmp['port_count']})")
    if not cmp["match"]:
        print(f"  only_single: {cmp['only_single']}")
        print(f"  only_port: {cmp['only_port']}")


if __name__ == "__main__":
    main()
