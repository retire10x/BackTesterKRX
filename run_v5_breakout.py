"""
v5.0 20일선 변곡점 스나이퍼 — 2단계 실행 별칭.

`run_v5_portfolio.py`와 동일하며, stdout/stderr를 outputs/v5_run.log에 함께 남긴다.
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


def main() -> None:
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
        from run_v5_portfolio import run_v5_portfolio_backtest

        run_v5_portfolio_backtest()
    finally:
        sys.stdout, sys.stderr = orig_out, orig_err
        log_fh.close()


if __name__ == "__main__":
    main()
