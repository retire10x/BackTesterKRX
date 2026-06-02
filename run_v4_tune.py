"""
v4.0 Phase G — YAML 하이퍼파라미터 튜닝 (main 병합 없음).

벌크 OHLCV는 1회만 로드한 뒤 v4_0.strategy 시나리오별 포트폴리오를 반복 실행한다.
산출: outputs/v4_tune_results.csv, outputs/v4_tune_report.md

사용:
  python run_v4_tune.py           # 방어·손익비 시나리오 전체
  python run_v4_tune.py --quick   # 6개 대표 시나리오만
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

import pandas as pd

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _load_env_file(path: str) -> None:
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except Exception:
        return
    for raw in lines:
        s = str(raw).strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        key = k.strip()
        val = v.strip()
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        if key and key not in os.environ:
            os.environ[key] = val


_load_env_file(os.path.join(project_root, ".env"))

from src.data_loader import load_config
from src.engine.portfolio_manager import PortfolioManager, load_merged_market_day_frames
from src.v4_config import load_v4_config, v4_config_with_strategy_overrides

START_DATE = "2023-01-01"
END_DATE = "2026-05-31"
OUT_CSV = os.path.join(project_root, "outputs", "v4_tune_results.csv")
OUT_MD = os.path.join(project_root, "outputs", "v4_tune_report.md")


def _yaml_strategy_baseline() -> dict[str, float | int]:
    return dict(load_v4_config().strategy.__dict__)


def _scenarios(quick: bool) -> list[tuple[str, dict[str, float | int]]]:
    """(이름, strategy 오버라이드) — baseline은 빈 dict."""
    scenarios: list[tuple[str, dict[str, float | int]]] = [
        ("baseline_yaml", {}),
        ("defense_3m_deploy15", {"fixed_invest_amount": 3_000_000, "max_daily_cash_deploy_ratio": 0.15}),
        ("defense_3m_deploy25", {"fixed_invest_amount": 3_000_000, "max_daily_cash_deploy_ratio": 0.25}),
        ("defense_5m_deploy25", {"fixed_invest_amount": 5_000_000, "max_daily_cash_deploy_ratio": 0.25}),
        ("defense_3m_deploy45", {"fixed_invest_amount": 3_000_000, "max_daily_cash_deploy_ratio": 0.45}),
    ]
    if quick:
        scenarios.extend(
            [
                ("rr_tight_3pct", {"stop_loss_ratio": 0.03, "target_profit_ratio": 0.03}),
                ("rr_wide_5pct", {"stop_loss_ratio": 0.05, "target_profit_ratio": 0.05}),
            ]
        )
        return scenarios

    scenarios.extend(
        [
            ("defense_5m_deploy15", {"fixed_invest_amount": 5_000_000, "max_daily_cash_deploy_ratio": 0.15}),
            ("nuliim_2pct", {"nuliim_ratio": 0.02}),
            ("nuliim_5pct", {"nuliim_ratio": 0.05}),
            ("rr_tight_sl3_tp3", {"stop_loss_ratio": 0.03, "target_profit_ratio": 0.03}),
            ("rr_balanced_sl4_tp4", {"stop_loss_ratio": 0.04, "target_profit_ratio": 0.04}),
            ("rr_wide_sl5_tp5", {"stop_loss_ratio": 0.05, "target_profit_ratio": 0.05}),
            ("rr_asym_sl3_tp5", {"stop_loss_ratio": 0.03, "target_profit_ratio": 0.05}),
            (
                "combo_def3m_rr_tight",
                {
                    "fixed_invest_amount": 3_000_000,
                    "max_daily_cash_deploy_ratio": 0.20,
                    "stop_loss_ratio": 0.03,
                    "target_profit_ratio": 0.04,
                    "nuliim_ratio": 0.03,
                },
            ),
            (
                "combo_def3m_rr_wide",
                {
                    "fixed_invest_amount": 3_000_000,
                    "max_daily_cash_deploy_ratio": 0.20,
                    "stop_loss_ratio": 0.05,
                    "target_profit_ratio": 0.05,
                    "nuliim_ratio": 0.04,
                },
            ),
        ]
    )
    return scenarios


def _run_one(
    day_frames,
    bdays,
    name: str,
    overrides: dict[str, float | int],
    initial_cash: float,
) -> dict[str, object]:
    v4 = v4_config_with_strategy_overrides(overrides)
    mgr = PortfolioManager(
        day_frames,
        bdays,
        start_date=START_DATE,
        end_date=END_DATE,
        phase_g_mode=True,
        v4_config=v4,
    )
    result = mgr.run()
    m = result.metrics
    s = v4.strategy
    pf = m["profit_factor"]
    pf_val = float("inf") if pf == float("inf") else float(pf)
    sells = result.trades_detail[result.trades_detail["side"] == "SELL"] if not result.trades_detail.empty else pd.DataFrame()
    buys = result.trades_detail[result.trades_detail["side"] == "BUY"] if not result.trades_detail.empty else pd.DataFrame()
    return {
        "scenario": name,
        "fixed_invest_amount": s.fixed_invest_amount,
        "max_daily_cash_deploy_ratio": s.max_daily_cash_deploy_ratio,
        "nuliim_ratio": s.nuliim_ratio,
        "stop_loss_ratio": s.stop_loss_ratio,
        "target_profit_ratio": s.target_profit_ratio,
        "final_equity": float(m["final_equity"]),
        "cumulative_return_pct": float(m["cumulative_return_pct"]),
        "profit_factor": pf_val,
        "mdd_pct": float(m["mdd_pct"]),
        "win_rate_pct": float(m["win_rate_pct"]),
        "total_trades": int(m["total_trades"]),
        "buy_count": int(len(buys)),
        "sell_count": int(len(sells)),
        "initial_cash": initial_cash,
        "profitable": float(m["final_equity"]) > initial_cash,
        "pf_ge_1": pf_val >= 1.0,
    }


def _write_report(df: pd.DataFrame, baseline: dict[str, float | int]) -> None:
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    by_pf = df.sort_values(["pf_ge_1", "profit_factor", "final_equity"], ascending=[False, False, False])
    by_eq = df.sort_values("final_equity", ascending=False)
    best_pf = by_pf.iloc[0] if len(by_pf) else None
    best_eq = by_eq.iloc[0] if len(by_eq) else None

    lines = [
        "# v4.0 Phase G — 하이퍼파라미터 튜닝 성적표",
        "",
        f"- 생성: {ts}",
        f"- 기간: {START_DATE} ~ {END_DATE}",
        "- **main 병합 없음** — 채택 시 `config/settings.yaml` `v4_0.strategy` 수동 반영",
        f"- Ruin 서면: [docs/v4_ruin_analysis.md](v4_ruin_analysis.md)",
        "",
        "## YAML 기준선 (튜닝 전)",
        "",
        f"| 키 | 값 |",
        f"|----|-----|",
    ]
    for k in (
        "fixed_invest_amount",
        "max_daily_cash_deploy_ratio",
        "nuliim_ratio",
        "stop_loss_ratio",
        "target_profit_ratio",
    ):
        v = baseline.get(k)
        if k == "fixed_invest_amount":
            lines.append(f"| `{k}` | {v:,.0f} |")
        elif "ratio" in k:
            lines.append(f"| `{k}` | {v} |")
        else:
            lines.append(f"| `{k}` | {v} |")

    def _table(sub: pd.DataFrame) -> str:
        cols = [
            "scenario",
            "fixed_invest_amount",
            "max_daily_cash_deploy_ratio",
            "nuliim_ratio",
            "stop_loss_ratio",
            "target_profit_ratio",
            "final_equity",
            "cumulative_return_pct",
            "profit_factor",
            "mdd_pct",
            "win_rate_pct",
            "sell_count",
        ]
        view = sub[cols].copy()
        view["fixed_invest_amount"] = view["fixed_invest_amount"].map(lambda x: f"{x:,.0f}")
        for c in ("nuliim_ratio", "stop_loss_ratio", "target_profit_ratio", "max_daily_cash_deploy_ratio"):
            view[c] = view[c].map(lambda x: f"{x:.3f}")
        view["final_equity"] = view["final_equity"].map(lambda x: f"{x:,.0f}")
        view["cumulative_return_pct"] = view["cumulative_return_pct"].map(lambda x: f"{x:.2f}")
        view["profit_factor"] = view["profit_factor"].map(lambda x: f"{x:.2f}")
        view["mdd_pct"] = view["mdd_pct"].map(lambda x: f"{x:.2f}")
        view["win_rate_pct"] = view["win_rate_pct"].map(lambda x: f"{x:.1f}")
        try:
            return view.to_markdown(index=False)
        except ImportError:
            return view.to_string(index=False)

    sorted_all = df.sort_values(
        ["pf_ge_1", "profit_factor", "final_equity"], ascending=[False, False, False]
    )
    lines.extend(["", "## 전체 결과 (PF 우선 정렬)", "", _table(sorted_all), "", "## PF ≥ 1.0 시나리오", ""])
    pf_ok = df[df["pf_ge_1"]]
    if pf_ok.empty:
        lines.append("_없음 — 손익비·사이징 추가 스윕 필요._")
    else:
        lines.append(_table(pf_ok.sort_values("final_equity", ascending=False)))

    lines.extend(["", "## 흑자(최종 > 초기) 시나리오", ""])
    prof = df[df["profitable"]]
    if prof.empty:
        lines.append("_없음._")
    else:
        lines.append(_table(prof.sort_values("final_equity", ascending=False)))

    if best_pf is not None:
        lines.extend(
            [
                "",
                "## 권장 검토 (자동 — 수동 채택)",
                "",
                f"- **PF 최우선:** `{best_pf['scenario']}` — PF {best_pf['profit_factor']:.2f}, "
                f"최종 {best_pf['final_equity']:,.0f}원 ({best_pf['cumulative_return_pct']:.2f}%)",
            ]
        )
    if best_eq is not None and (best_pf is None or best_eq["scenario"] != best_pf["scenario"]):
        lines.append(
            f"- **최종 자산 최우선:** `{best_eq['scenario']}` — "
            f"{best_eq['final_equity']:,.0f}원 ({best_eq['cumulative_return_pct']:.2f}%), PF {best_eq['profit_factor']:.2f}"
        )

    # 눌림목(nuliim_ratio) 단독 레버 — baseline·nuliim_* 및 동일 deploy/손익비 시나리오 비교
    nuliim_keys = (
        "fixed_invest_amount",
        "max_daily_cash_deploy_ratio",
        "stop_loss_ratio",
        "target_profit_ratio",
    )
    base_row = df[df["scenario"] == "baseline_yaml"]
    if not base_row.empty:
        base_vals = {k: base_row.iloc[0][k] for k in nuliim_keys}
        nuliim_only = df[
            df.apply(
                lambda r: all(r[k] == base_vals[k] for k in nuliim_keys),
                axis=1,
            )
        ].copy()
        if len(nuliim_only) >= 2:
            n_sorted = nuliim_only.sort_values(
                ["pf_ge_1", "profit_factor", "final_equity"], ascending=[False, False, False]
            )
            best_pf_n = n_sorted.iloc[0]
            best_eq_n = nuliim_only.sort_values("final_equity", ascending=False).iloc[0]
            lines.extend(
                [
                    "",
                    "## 눌림목 타점 (nuliim_ratio) — 동일 사이징·손익비",
                    "",
                    "_기준: YAML과 동일한 `fixed_invest_amount`·`max_daily_cash_deploy_ratio`·손절/익절._",
                    "",
                    _table(n_sorted),
                    "",
                    "### 권장 눌림 깊이 (자동)",
                    "",
                    f"- **PF 1위:** `{best_pf_n['scenario']}` — "
                    f"`nuliim_ratio={best_pf_n['nuliim_ratio']:.3f}` "
                    f"({float(best_pf_n['nuliim_ratio']) * 100:.1f}%), "
                    f"PF {best_pf_n['profit_factor']:.2f}, 최종 {best_pf_n['final_equity']:,.0f}원",
                    f"- **최종 자산 1위:** `{best_eq_n['scenario']}` — "
                    f"{float(best_eq_n['nuliim_ratio']) * 100:.1f}%, "
                    f"최종 {best_eq_n['final_equity']:,.0f}원, PF {best_eq_n['profit_factor']:.2f}",
                ]
            )
            shallow = nuliim_only.sort_values("nuliim_ratio").iloc[0]
            deep = nuliim_only.sort_values("nuliim_ratio", ascending=False).iloc[0]
            if shallow["scenario"] != deep["scenario"]:
                lines.append(
                    f"- **얕은 vs 깊은:** 얕음 `{shallow['scenario']}` ({float(shallow['nuliim_ratio'])*100:.1f}%, PF {shallow['profit_factor']:.2f}) "
                    f"· 깊음 `{deep['scenario']}` ({float(deep['nuliim_ratio'])*100:.1f}%, PF {deep['profit_factor']:.2f})"
                )

    from pathlib import Path

    Path(OUT_MD).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="v4.0 YAML strategy 튜닝 스윕")
    parser.add_argument("--quick", action="store_true", help="6개 대표 시나리오만 실행")
    args = parser.parse_args()

    cfg = load_config()
    v4_base = load_v4_config(cfg)
    initial_cash = float(v4_base.portfolio.initial_cash)
    baseline = _yaml_strategy_baseline()
    scenario_list = _scenarios(args.quick)

    print("🔧 v4.0 Phase G 튜닝 — 벌크 로드 (1회)...")
    day_frames, bdays = load_merged_market_day_frames(START_DATE, END_DATE, force_bulk=True)
    print(f"   {len(day_frames)} 영업일 · 시나리오 {len(scenario_list)}개")

    rows: list[dict[str, object]] = []
    for i, (name, overrides) in enumerate(scenario_list, 1):
        print(f"  [{i}/{len(scenario_list)}] {name} ...", flush=True)
        rows.append(_run_one(day_frames, bdays, name, overrides, initial_cash))

    df = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    _write_report(df, baseline)

    print(f"\n✅ 저장: {OUT_CSV}")
    print(f"✅ 저장: {OUT_MD}")
    n_pf = int(df["pf_ge_1"].sum())
    n_prof = int(df["profitable"].sum())
    print(f"   PF≥1: {n_pf}/{len(df)} · 흑자: {n_prof}/{len(df)}")
    if n_pf:
        top = df.sort_values("profit_factor", ascending=False).iloc[0]
        print(
            f"   PF 1위: {top['scenario']} — PF {top['profit_factor']:.2f}, "
            f"최종 {top['final_equity']:,.0f}원"
        )


if __name__ == "__main__":
    main()
