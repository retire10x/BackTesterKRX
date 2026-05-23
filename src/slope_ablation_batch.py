"""
use_slope_acceleration False vs True 동일 설정 ablation 배치.

(엔진: True일 때 최근 5봉 MA20의 OLS 기울기가 0 초과인 경우만 매수 허용.)

시총 하한 필터링된 유니버스 종목별로 baseline / slope_accel 각각 경량 백테스트 후
`output/slope_ablation.tsv` 로 내보냅니다(ThreadPoolExecutor 병렬).
"""
from __future__ import annotations

import copy
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from .data_loader import fetch_filtered_universe, fetch_listing_market_cap_krw_by_code
from .metrics import BacktestResult, run_backtest_detailed


def universe_by_min_cap(market: str, min_market_cap_krw: float) -> list[tuple[str, str]]:
    all_u = fetch_filtered_universe(market, "")
    m = fetch_listing_market_cap_krw_by_code(market)
    out: list[tuple[str, str]] = []
    for code_raw, name in sorted(all_u.items()):
        code = str(code_raw).strip().zfill(6)
        mc = m.get(code)
        if mc is not None and mc >= float(min_market_cap_krw):
            out.append((code, name))
    return out


def _pct_from_summary_cell(cell: str) -> float | None:
    s = str(cell).strip().replace(",", "")
    if not s:
        return None
    parts = s.split()
    if not parts:
        return None
    try:
        return float(parts[0])
    except ValueError:
        return None


def _row_from_run(variant: str, code: str, name: str, r: BacktestResult) -> list[str]:
    if not r.ok:
        return [
            variant,
            code,
            name,
            "0",
            (r.error or "").replace("\t", " ")[:240],
            "",
            "",
            "0",
            "0",
            "0",
        ]
    mp = {a[0]: a[1] for a in r.summary_rows}
    tr = _pct_from_summary_cell(str(mp.get("누적 수익률", "")))
    mdd = _pct_from_summary_cell(str(mp.get("최대 손실 낙폭", "")))
    nt = str(r.n_buy + r.n_sell)
    return [
        variant,
        code,
        name,
        "1",
        "",
        "" if tr is None else f"{tr:.6f}",
        "" if mdd is None else f"{mdd:.6f}",
        str(r.n_buy),
        str(r.n_sell),
        nt,
    ]


def run_slope_ablation_batch(
    cfg: dict[str, Any],
    *,
    min_market_cap_krw: float | None = None,
    max_workers: int | None = None,
    progress_cb: Callable[[str], None] | None = None,
) -> Path:
    uni_cfg = cfg.get("universe") or {}
    sab = (
        uni_cfg.get("slope_ablation_batch")
        if isinstance(uni_cfg.get("slope_ablation_batch"), dict)
        else {}
    )
    mc = float(sab.get("min_market_cap_krw") or 0)
    if mc <= 0:
        scr = uni_cfg.get("screener")
        mc = (
            float((scr or {}).get("min_market_cap_krw") or 300_000_000_000)
            if isinstance(scr, dict)
            else 300_000_000_000
        )
    if min_market_cap_krw is not None:
        mc = float(min_market_cap_krw)

    nw = sab.get("max_workers", 6)
    if max_workers is not None:
        nw = max_workers
    try:
        nw = max(1, min(32, int(nw)))
    except (TypeError, ValueError):
        nw = 6

    market = str(uni_cfg.get("market") or "KOSPI")
    codes = universe_by_min_cap(market, mc)

    output_dir = Path("output")
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "slope_ablation.tsv"

    if not codes:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(
                "variant\tcode\tname\tok\terror\ttotal_return_pct\tmdd_pct\t"
                "n_buy\tn_sell\tn_trades\n"
            )
            fh.write("# summary\tuniverse_n\tbaseline_ok_n\tslope_ok_n\t(empty)\n")
        print(f"[경고] 시총 한도 내 종목 없음(market={market}, min_cap={mc}). TSV: {out_path}", flush=True)
        return out_path

    base = copy.deepcopy(cfg)
    base.setdefault("universe", {}).setdefault("screener", {})["enabled"] = False

    vrank = {"baseline": 0, "slope_accel": 1, "error": 99}

    def run_pair(pair: tuple[str, str]) -> list[list[str]]:
        code, name = pair
        rows_loc: list[list[str]] = []
        for variant, use_accel in (("baseline", False), ("slope_accel", True)):
            c = copy.deepcopy(base)
            c.setdefault("universe", {})["selected_code"] = code
            c.setdefault("strategy", {})["use_slope_acceleration"] = use_accel
            r = run_backtest_detailed(c, omit_report_artifacts=True)
            rows_loc.append(_row_from_run(variant, code, name, r))
        return rows_loc

    all_written: list[list[str]] = []
    with ThreadPoolExecutor(max_workers=nw) as ex:
        futs = {ex.submit(run_pair, pair): pair for pair in codes}
        for fut in as_completed(futs):
            code_hint, name_hint = futs[fut]
            try:
                all_written.extend(fut.result())
            except Exception as e:
                err = str(e).replace("\t", " ")[:240]
                all_written.extend(
                    [
                        [
                            "baseline",
                            code_hint,
                            name_hint.replace("\t", " ")[:120],
                            "0",
                            err,
                            "",
                            "",
                            "0",
                            "0",
                            "0",
                        ],
                        [
                            "slope_accel",
                            code_hint,
                            name_hint.replace("\t", " ")[:120],
                            "0",
                            err,
                            "",
                            "",
                            "0",
                            "0",
                            "0",
                        ],
                    ]
                )
            if progress_cb is not None:
                progress_cb(code_hint)

    variant_order_key = lambda z: (
        z[1],
        vrank.get(z[0], 50),
    )
    sorted_rows = sorted(all_written, key=variant_order_key)

    def floats_for(variant_tag: str) -> tuple[list[float], list[float], list[int]]:
        rets: list[float] = []
        mdds: list[float] = []
        trades: list[int] = []
        for row in sorted_rows:
            if len(row) < 10:
                continue
            if row[0] != variant_tag:
                continue
            if row[3] != "1":
                continue
            try:
                rets.append(float(row[5]))
            except ValueError:
                pass
            try:
                mdds.append(float(row[6]))
            except ValueError:
                pass
            try:
                trades.append(int(row[9]))
            except ValueError:
                pass
        return rets, mdds, trades

    nb_r, nb_m, nb_t = floats_for("baseline")
    na_r, na_m, na_t = floats_for("slope_accel")

    def fmean(xs: list[float]) -> float:
        return float(statistics.fmean(xs)) if xs else float("nan")

    def fmedian(xs: list[float]) -> float:
        return float(statistics.median(xs)) if xs else float("nan")

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(
            "variant\tcode\tname\tok\terror\ttotal_return_pct\tmdd_pct\t"
            "n_buy\tn_sell\tn_trades\n"
        )
        for row in sorted_rows:
            fh.write("\t".join(row) + "\n")
        fh.write(
            "# summary\tuniverse_n\tbaseline_ok_n\tslope_ok_n\t"
            "mean_ret_baseline\tmean_ret_slope\tmedian_ret_baseline\tmedian_ret_slope\t"
            "mean_mdd_baseline\tmean_mdd_slope\tmean_trades_baseline\tmean_trades_slope\n"
        )
        ok_b = sum(1 for row in sorted_rows if row[0] == "baseline" and row[3] == "1")
        ok_a = sum(1 for row in sorted_rows if row[0] == "slope_accel" and row[3] == "1")
        u_n = len(codes)
        mt_b = fmean(nb_t)
        mt_a = fmean(na_t)
        fh.write(
            f"# summary\t{u_n}\t{ok_b}\t{ok_a}\t{fmean(nb_r)}\t{fmean(na_r)}\t"
            f"{fmedian(nb_r)}\t{fmedian(na_r)}\t{fmean(nb_m)}\t{fmean(na_m)}\t"
            f"{mt_b}\t{mt_a}\n"
        )

    print(
        f"[slope_ablation] 종목수(시총≥한도): {len(codes)} | workers={nw} → TSV: {out_path.resolve()}"
    )
    print(
        f"  평균 누적수익률(%): baseline {fmean(nb_r):.4f} | slope_accel {fmean(na_r):.4f}"
    )
    print(
        f"  평균 MDD(%): baseline {fmean(nb_m):.4f} | slope_accel {fmean(na_m):.4f}"
    )
    print(
        f"  평균 거래건수(buy+sell): baseline {mt_b:.2f} | slope_accel {mt_a:.2f}"
    )

    return out_path


__all__ = ["run_slope_ablation_batch", "universe_by_min_cap"]
