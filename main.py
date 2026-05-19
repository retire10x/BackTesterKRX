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

from src.data_loader import fetch_filtered_universe, load_config
from src.metrics import run_backtest_detailed

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
    if not rest:
        from src.gui import main as gui_main

        gui_main()
        return
    sys.argv = [sys.argv[0]] + rest
    cli_main()


if __name__ == "__main__":
    main()
