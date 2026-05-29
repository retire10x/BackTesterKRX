"""
BackTesterKRX 시작점.
- 인자 없음: GUI (`src.gui`)
- `--watch` / `-w`: GUI 개발용 — `src/**/*.py` 및 루트 `main.py` 저장 시 자식 GUI 재시작 (watchdog)
- 그 외 인자: 터미널(CLI) 백테스트
"""
from __future__ import annotations

import argparse
import copy
import subprocess
import sys
import threading
import time
from pathlib import Path

from tabulate import tabulate

from src.data_loader import (
    fetch_filtered_universe,
    load_config,
    load_v3_0_overnight_scalper_data,
    scan_leader_pullback_candidates_bulk,
)
from src.metrics import run_backtest_detailed
from src.slope_ablation_batch import run_slope_ablation_batch
from src.stock_screener import default_screener_config, screen_universe, summary_line_for_entry

# v3.0 (Overnight Scalper)
from src.utils.date_helper import resolve_overnight_scan_anchor
from src.v3_execution_engine import execute_v3_overnight_backtest
from src.v3_metrics import run_v3_analytics
from src.v3_signal_generator import generate_v3_overnight_signals

# `--watch` 모드: 같은 저장으로 여러 이벤트가 연달아 올 때 디바운스(초)
WATCH_DEBOUNCE_SEC = 0.5
# 자식 GUI 종료·재기동 루프 폴링 간격(초) — 낮을수록 재시작 반응이 빠름
CHILD_POLL_SEC = 0.05

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (OSError, ValueError):
        pass


def merge_cli_into_config(cfg: dict, args: argparse.Namespace) -> dict:
    out = copy.deepcopy(cfg)
    if args.start_date:
        out.setdefault("period", {})["start_date"] = args.start_date
    if args.end_date:
        out.setdefault("period", {})["end_date"] = args.end_date
    if args.market:
        out.setdefault("universe", {})["market"] = args.market
    if args.search_keyword is not None:
        out.setdefault("universe", {})["search_keyword"] = args.search_keyword
    if args.code:
        out.setdefault("universe", {})["selected_code"] = args.code
    if args.ma_period is not None:
        out.setdefault("strategy", {})["ma_period"] = args.ma_period
    if args.interval:
        out.setdefault("strategy", {})["interval"] = args.interval
    if args.ma120:
        out.setdefault("strategy", {})["show_trend_ma120"] = True
    if args.ma200:
        out.setdefault("strategy", {})["show_trend_ma200"] = True
    if getattr(args, "screener_batch", False):
        out.setdefault("universe", {}).setdefault("screener", {})["enabled"] = True
    if getattr(args, "batch_max_workers", None) is not None:
        ub = out.setdefault("universe", {})
        sab = ub.setdefault("slope_ablation_batch", {})
        if isinstance(sab, dict):
            sab["max_workers"] = int(args.batch_max_workers)
    return out


def print_candidate_list(universe: dict[str, str]) -> None:
    rows = [[c, n] for c, n in sorted(universe.items())]
    print(tabulate(rows, headers=["코드", "종목명"], tablefmt="grid"))
    print(f"\n[안내] 후보 {len(rows)}개. universe.selected_code 에 하나를 적거나 --code 로 넣으세요.")


def run_backtest_cli(cfg: dict, override_code: str | None = None) -> bool:
    r = run_backtest_detailed(cfg, override_code=override_code)
    for ln in r.log_lines:
        print(ln)
    if not r.ok:
        print(f"[오류] {r.error}")
        return False
    if r.trade_markers_skipped > 0:
        print(
            f"[경고] 차트 타점 {r.trade_markers_skipped}건이 날짜 매칭 실패로 생략되었습니다. 로그의 [CRITICAL] 줄을 확인하세요.",
            file=sys.stderr,
        )
    print("\n" + "=" * 52)
    print(" 백테스트 성과 (싱글 종목)")
    print("=" * 52)
    print(tabulate(r.summary_rows, tablefmt="grid"))
    if r.report_path:
        print(f"\n[그래프] 저장: {r.report_path}")
    return True


def run_screener_batch_cli(cfg: dict) -> bool:
    uni = cfg.get("universe") or {}
    period = cfg.get("period") or {}
    end_d = str(period.get("end_date") or "").strip()
    if not end_d:
        print("[오류] screener-batch 는 period.end_date 가 필요합니다. YAML 또는 --end 로 지정하세요.", file=sys.stderr)
        return False
    raw_scr = uni.get("screener") if isinstance(uni.get("screener"), dict) else {}
    scr = {**default_screener_config(), **raw_scr}

    lk = max(5, min(120, int(scr.get("lookback_trading_days", 20))))
    tn = max(1, min(200, int(scr.get("top_n", 100))))
    metric = "atr14"  # 엔진 고정(구 YAML volatility_metric 과 무관)
    ds = default_screener_config()
    try:
        mc_kw = float(scr.get("min_market_cap_krw", ds["min_market_cap_krw"]))
    except (TypeError, ValueError):
        mc_kw = float(ds["min_market_cap_krw"])
    hf_pair = bool(
        scr.get("hard_ma_pair_trend_filter", ds["hard_ma_pair_trend_filter"])
    )
    try:
        pb_cap = float(scr.get("pullback_rank_cap_pct", ds["pullback_rank_cap_pct"]))
    except (TypeError, ValueError):
        pb_cap = float(ds["pullback_rank_cap_pct"])

    picks = screen_universe(
        market=str(uni.get("market") or "KOSPI"),
        keyword=str(uni.get("search_keyword") or ""),
        end_date=end_d,
        lookback_trading_days=lk,
        top_n=tn,
        volatility_metric=metric,
        progress_cb=None,
        min_market_cap_krw=mc_kw,
        hard_ma_pair_trend_filter=hf_pair,
        pullback_rank_cap_pct=pb_cap,
    )
    if not picks:
        print("[오류] 스크리너 후보 없음.", file=sys.stderr)
        return False

    print(f"\n[스크리너 CLI] 종료일 {end_d} 일봉 기준 최근 {lk}영업일 | ATR14(%) 고정 랭킹 | 상위 {len(picks)}개\n")
    print(tabulate(
        [
            [
                i,
                e.code,
                e.name[:16],
                f"{e.volatility_raw:.6g}",
                int(round(e.turnover_krw_sum)),
                f"{e.pullback_from_high_pct:.2f}",
                f"{e.volume_contract_pct:.1f}",
                f"{e.combined_score:.4f}",
            ]
            for i, e in enumerate(picks, start=1)
        ],
        headers=[
            "순위",
            "코드",
            "종목명",
            "vol_raw",
            "거래대금합(원)",
            "고점낙폭%",
            "거래량건조%",
            "score",
        ],
        tablefmt="grid",
    ))

    out_dir = Path("output")
    out_dir.mkdir(parents=True, exist_ok=True)
    tsv = out_dir / "screener_last.tsv"
    try:
        with open(tsv, "w", encoding="utf-8") as fh:
            fh.write(
                "rank\tcode\tname\tvol_metric\tamount_krw_sum\tpullback_hi_pct\tvol_contract_pct\tscore_pct_mean\n"
            )
            for i, ent in enumerate(picks, start=1):
                fh.write(
                    f"{i}\t{ent.code}\t{ent.name}\t{ent.volatility_raw:.12g}"
                    f"\t{int(round(ent.turnover_krw_sum))}\t"
                    f"{ent.pullback_from_high_pct:.12g}\t{ent.volume_contract_pct:.12g}\t"
                    f"{ent.combined_score:.6g}\n"
                )
    except OSError as e:
        print(f"[경고] screener TSV 저장 실패: {e}", file=sys.stderr)

    agg_rows = []
    all_ok = True
    for i, ent in enumerate(picks, start=1):
        print("\n" + "-" * 60)
        print(f"[{i}/{len(picks)}] 백테스트 {ent.code} {ent.name}")
        print("-" * 60)
        r = run_backtest_detailed(cfg, override_code=ent.code)
        for ln in r.log_lines[-5:]:
            print(ln)
        if not r.ok:
            all_ok = False
            agg_rows.append(
                [
                    ent.code,
                    ent.name[:18],
                    "FAIL",
                    str(r.error or "")[:52],
                    summary_line_for_entry(ent),
                ]
            )
            continue
        m = {
            row[0]: row[1]
            for row in r.summary_rows
        }
        agg_rows.append(
            [
                ent.code,
                ent.name[:18],
                m.get("누적 수익률", "-"),
                m.get("최대 손실 낙폭", "-"),
                summary_line_for_entry(ent),
            ]
        )

    print("\n" + "=" * 72)
    print(f" 스크리너 배치 요약 (성공 포함 {sum(1 for row in agg_rows if row[2] != 'FAIL')} / {len(agg_rows)}) ")
    print("=" * 72)
    print(
        tabulate(
            agg_rows,
            headers=["코드", "종목명", "누적 수익률", "MDD", "스크린 요약 줄"],
            tablefmt="grid",
        )
    )
    print(f"\n스크린 TSV: {tsv.resolve()}")

    return all_ok


def cli_main() -> None:
    ap = argparse.ArgumentParser(
        description="BackTesterKRX — 일봉/주봉 · 싱글 종목 (설정은 YAML, 옵션으로 덮어쓰기)"
    )
    ap.add_argument("--list", action="store_true", help="키워드 필터 후보만 출력하고 종료")
    ap.add_argument("--config", type=str, default=None, help="설정 YAML 경로")
    ap.add_argument("--code", type=str, default=None, help="종목코드 6자리 (YAML 덮어쓰기)")
    ap.add_argument(
        "--interval",
        choices=["daily", "weekly"],
        default=None,
        help="봉 주기: daily(일봉), weekly(주봉). 미지정 시 YAML strategy.interval",
    )
    ap.add_argument("--start", dest="start_date", default=None, help="시작일 YYYY-MM-DD")
    ap.add_argument("--end", dest="end_date", default=None, help="종료일 YYYY-MM-DD")
    ap.add_argument("--keyword", dest="search_keyword", default=None, help="종목명 검색 키워드 (빈칸 허용)")
    ap.add_argument("--market", type=str, default=None, help="시장 (예: KOSPI)")
    ap.add_argument("--ma", type=int, dest="ma_period", default=None, help="이평선 N")
    ap.add_argument(
        "--ma120",
        action="store_true",
        help="PNG 차트에 120일/120봉 추세 이평 오버레이 (show_trend_ma120)",
    )
    ap.add_argument(
        "--ma200",
        action="store_true",
        help="PNG 차트에 200일/200봉 추세 이평 오버레이 (show_trend_ma200)",
    )
    ap.add_argument(
        "--screener-batch",
        action="store_true",
        help=(
            "일봉 스크리너로 후보 종목 필터링 후 순차 백테스트합니다"
            "(universe.screener + 기간 종료일 기준)."
        ),
    )
    ap.add_argument(
        "--slope-ablation-batch",
        action="store_true",
        help=(
            "KOSPI 등 시장 시총 하한 종목별로 strategy.use_slope_acceleration "
            "False/True 각각 경량 백테스트 후 output/slope_ablation.tsv 비교 출력."
        ),
    )
    ap.add_argument(
        "--batch-max-workers",
        type=int,
        default=None,
        metavar="N",
        help="배치(--slope-ablation-batch 등) 동시 종목 처리 스레드 수를 YAML 값 대신 고정합니다.",
    )
    ap.epilog = (
        "GUI 는 인자 없이: python main.py\n"
        "코드 저장 시 GUI 자동 재시작(개발): python main.py --watch  (감시: src/**/*.py, 루트 main.py)"
    )
    args = ap.parse_args()

    base = load_config(args.config)
    cfg = merge_cli_into_config(base, args)

    if args.list:
        uni = cfg.get("universe", {})
        m = uni.get("market", "KOSPI")
        kw = uni.get("search_keyword", "") or ""
        cand = fetch_filtered_universe(m, kw)
        print(f"[{m}] 키워드: '{kw or '(전체)'}' 후보 {len(cand)}개\n")
        print_candidate_list(cand)
        return

    if args.slope_ablation_batch:
        try:
            run_slope_ablation_batch(cfg)
            ok = True
        except Exception as e:
            print(f"[오류] slope-ablation-batch: {e}", file=sys.stderr)
            ok = False
    elif args.screener_batch:
        ok = run_screener_batch_cli(cfg)
    else:
        ok = run_backtest_cli(cfg, override_code=args.code)
    sys.exit(0 if ok else 1)


def _parse_argv_for_watch_and_rest() -> tuple[bool, list[str]]:
    raw = sys.argv[1:]
    watch = any(a in ("--watch", "-w") for a in raw)
    rest = [a for a in raw if a not in ("--watch", "-w")]
    return watch, rest


def run_gui_with_watchdog() -> None:
    """GUI 를 자식 프로세스로 띄우고, src/ 이하·루트 main.py 의 .py 변경 시 종료 후 재실행."""
    try:
        from watchdog.events import PatternMatchingEventHandler
        from watchdog.observers import Observer
    except ImportError as e:
        print(
            "watchdog 패키지가 필요합니다. venv 에서 실행: pip install -r requirements.txt",
            file=sys.stderr,
        )
        raise SystemExit(2) from e

    root = Path(__file__).resolve().parent
    src_dir = root / "src"
    main_py = root / "main.py"
    if not src_dir.is_dir():
        print(f"src 폴더를 찾을 수 없습니다: {src_dir}", file=sys.stderr)
        raise SystemExit(2)
    if not main_py.is_file():
        print(f"main.py 를 찾을 수 없습니다: {main_py}", file=sys.stderr)
        raise SystemExit(2)

    restart_event = threading.Event()
    debounce_lock = threading.Lock()
    last_fire = [0.0]

    def request_restart(path_hint: str) -> None:
        now = time.monotonic()
        with debounce_lock:
            if now - last_fire[0] < WATCH_DEBOUNCE_SEC:
                return
            last_fire[0] = now
        print(f"[watch] 코드 변경 감지 ({path_hint}) — GUI 재시작", flush=True)
        restart_event.set()

    skip_name_parts = frozenset(
        {"venv", ".venv", "__pycache__", "node_modules", ".git", ".tox", "dist", "build"}
    )

    class _PyChangeHandler(PatternMatchingEventHandler):
        def __init__(self) -> None:
            super().__init__(
                patterns=["*.py"],
                ignore_patterns=["*/__pycache__/*", "*\\__pycache__\\*"],
                ignore_directories=True,
                case_sensitive=False,
            )

        def _maybe_restart(self, src_path: str) -> None:
            try:
                p = Path(src_path).resolve()
            except OSError:
                return
            try:
                rel = p.relative_to(root)
            except ValueError:
                return
            if skip_name_parts.intersection(rel.parts):
                return
            # src 패키지 전체 + 프로젝트 루트의 main.py 만 (다른 루트 .py 는 무시)
            under_src = len(rel.parts) >= 1 and rel.parts[0] == "src"
            root_main = (
                len(rel.parts) == 1 and rel.name.lower() == "main.py"
            )
            if under_src or root_main:
                request_restart(str(rel.as_posix()))

        def on_modified(self, event):  # noqa: ANN001
            if not event.is_directory:
                self._maybe_restart(event.src_path)

        def on_created(self, event):  # noqa: ANN001
            if not event.is_directory:
                self._maybe_restart(event.src_path)

        def on_moved(self, event):  # noqa: ANN001
            """에디터가 임시 파일에 쓴 뒤 main.py 등으로 rename 하는 경우."""
            if not event.is_directory:
                self._maybe_restart(event.dest_path)

    handler = _PyChangeHandler()
    observer = Observer()
    observer.schedule(handler, str(src_dir), recursive=True)
    observer.schedule(handler, str(root), recursive=False)
    observer.start()
    print(
        "[watch] 자동 재시작 — 감시: src/**/*.py, ./main.py · "
        f"디바운스 {WATCH_DEBOUNCE_SEC}s · 종료: GUI 닫기 또는 Ctrl+C",
        flush=True,
    )

    proc: subprocess.Popen | None = None
    try:
        while True:
            restart_event.clear()
            proc = subprocess.Popen(
                [sys.executable, "-c", "from src.gui import main as _g; _g()"],
                cwd=str(root),
            )
            while proc.poll() is None:
                if restart_event.wait(timeout=CHILD_POLL_SEC):
                    break
            if restart_event.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
                continue
            break
    finally:
        observer.stop()
        observer.join(timeout=3)
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()


def main() -> None:
    watch, rest = _parse_argv_for_watch_and_rest()
    if watch:
        if rest:
            print(
                "오류: --watch / -w 는 GUI 전용입니다. 예: python main.py --watch",
                "(CLI 는 --watch 없이 실행하세요.)",
                file=sys.stderr,
            )
            raise SystemExit(2)
        run_gui_with_watchdog()
        return

    # v3.0 진입점: --mode 로 간단 분기
    # - --mode gui  : 기존 GUI 진입
    # - --mode cli  : v3.0 Overnight Scalper 백테스트 (대시보드만 출력)
    mode: str | None = None
    config_path: str | None = None
    raw = list(rest)
    if raw:
        for i in range(len(raw) - 1):
            if raw[i] == "--mode":
                mode = str(raw[i + 1]).strip().lower()
            if raw[i] == "--config":
                config_path = str(raw[i + 1]).strip()
    if mode in ("gui", "cli"):
        if mode == "gui":
            from src.gui import main as gui_main

            gui_main()
            return
        # mode == "cli"
        base_cfg = load_config(config_path)
        cfg = merge_v3_cli_into_config(base_cfg, raw)
        run_v3_0_overnight_cli(cfg)
        return

    if not rest:
        from src.gui import main as gui_main

        gui_main()
        return
    sys.argv = [sys.argv[0]] + rest
    cli_main()


def merge_v3_cli_into_config(cfg: dict, raw_argv: list[str]) -> dict:
    """v3.0 CLI: --start / --end 로 YAML period 덮어쓰기 (엔진 로직 변경 없음)."""
    out = copy.deepcopy(cfg)
    i = 0
    while i < len(raw_argv):
        key = raw_argv[i]
        if key == "--start" and i + 1 < len(raw_argv):
            out.setdefault("period", {})["start_date"] = raw_argv[i + 1]
            i += 2
            continue
        if key == "--end" and i + 1 < len(raw_argv):
            out.setdefault("period", {})["end_date"] = raw_argv[i + 1]
            i += 2
            continue
        i += 1
    return out


def run_v3_0_overnight_cli(cfg: dict) -> None:
    """v3.0 파이프라인: Data Loader → Signal → Execution → Analytics (대시보드만 출력)."""
    from src.v3_execution_engine import BUY_COST, SELL_COST

    period = cfg.get("period") or {}
    start_d = str(period.get("start_date") or "").strip()
    end_d = str(period.get("end_date") or "").strip()
    if not start_d or not end_d:
        raise SystemExit(
            "[v3.0 cli] period.start_date / period.end_date 가 필요합니다. "
            "YAML 또는 --start / --end 로 지정하세요."
        )

    print(
        f"[v3.0] period={start_d} ~ {end_d} | "
        f"BUY_COST={BUY_COST} ({BUY_COST * 100:.3f}%) | "
        f"SELL_COST={SELL_COST} ({SELL_COST * 100:.2f}%)"
    )

    uni = cfg.get("universe") or {}
    market = str(uni.get("market") or "KOSPI").strip().upper()
    if market not in ("KOSPI", "KOSDAQ"):
        market = "KOSPI"

    v3_cfg = cfg.get("v3_0") or {}
    limit = int(v3_cfg.get("universe_limit", 100))

    anchor_info = resolve_overnight_scan_anchor(end_d)
    end_load = anchor_info.anchor_date.strftime("%Y-%m-%d")
    from datetime import date as _date_mod

    if _date_mod.fromisoformat(start_d.strip()[:10]) > anchor_info.anchor_date:
        raise SystemExit(
            f"[v3.0 cli] period.start_date({start_d}) 가 앵커 종료일({end_load}) 보다 늦습니다. 설정을 확인하세요."
        )

    print(
        f"[v3.13 parity] requested={anchor_info.requested_calendar_date} "
        f"t0={anchor_info.anchor_date} policy={anchor_info.anchor_policy_reason}"
    )

    items = load_v3_0_overnight_scalper_data(
        start_date=start_d,
        end_date=end_load,
        market=market,
        universe_limit=limit,
    )

    if not items:
        run_v3_analytics([])
        return

    traded_frames: list = []
    for _code, df in items:
        df_sig = generate_v3_overnight_signals(df)
        df_tr = execute_v3_overnight_backtest(df_sig)
        traded_frames.append(df_tr)

    run_v3_analytics(traded_frames)

    v3_cfg = cfg.get("v3_0") or {}
    parity = scan_leader_pullback_candidates_bulk(
        end_d,
        market=market,
        universe_limit=limit,
        volume_burst_multiple=float(v3_cfg.get("volume_burst_multiple", 3.0)),
        vol_shrink_limit=float(v3_cfg.get("vol_shrink_limit", 0.5)),
    )
    print("\n" + "=" * 52 + "\nv3.30 주도주 눌림목 스캐너 (CLI/GUI parity)\n" + "=" * 52)
    if parity.get("ok"):
        prow = parity.get("rows") or []
        if not prow:
            print("(해당 규격 충족 종목 없음)")
        for code_p, rp, _mk, _tk in prow:
            print(f"  {str(code_p).zfill(6)}  상승률(시가대비) {rp:+.2f}%")
        pst = parity.get("stats") or {}
        print(
            f"\n(parity meta) universe_limit_applied={pst.get('universe_limit_applied')} "
            f"t0={pst.get('effective_anchor_date')} prev1={pst.get('prev_1')} "
            f"policy={pst.get('anchor_policy_reason')}"
        )
    else:
        print(f"[parity unavailable] reason={parity.get('reason')}")


if __name__ == "__main__":
    main()
