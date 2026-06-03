"""
v5.x 실행 별칭 — stdout/stderr를 outputs/v5_run.log에 Tee.

전략 설계 단계: run_v5_portfolio 와 동일하게 실행 전 질문한다.
  python run_v5_breakout.py              # 대화형 (스캔·실행 각각 질문)
  python run_v5_breakout.py --no-scan    # 기존 JSON · 실행만 질문
  python run_v5_breakout.py --yes        # 질문 생략 (자동화 전용)
"""
from __future__ import annotations

import os
import sys

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

LOG_PATH = os.path.join(project_root, "outputs", "v5_run.log")


class _Tee:
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for s in self._streams:
            try:
                s.write(data)
            except UnicodeEncodeError:
                enc = getattr(s, "encoding", None) or "utf-8"
                s.write(data.encode(enc, errors="replace").decode(enc, errors="replace"))
            s.flush()

    def flush(self):
        for s in self._streams:
            s.flush()


def main(argv: list[str] | None = None) -> None:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    log_fh = open(LOG_PATH, "w", encoding="utf-8")
    orig_out, orig_err = sys.stdout, sys.stderr
    for stream in (orig_out, orig_err):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass
    sys.stdout = _Tee(orig_out, log_fh)
    sys.stderr = _Tee(orig_err, log_fh)
    try:
        from run_v5_portfolio import _parse_args, run_v5_portfolio_backtest

        args = _parse_args(argv)
        scan = True if args.scan_universe else (False if args.no_scan else None)
        run_v5_portfolio_backtest(
            section=args.section,
            scan_universe=scan,
            skip_prompts=args.yes,
        )
    finally:
        sys.stdout, sys.stderr = orig_out, orig_err
        log_fh.close()


if __name__ == "__main__":
    main()
