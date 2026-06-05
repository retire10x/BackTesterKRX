"""
v6.0 라이브 대시보드 API 서버.

  python run_live_dashboard.py
  python run_live_dashboard.py --port 8765

마스터 봇(run_live_bot.py)과 별도 프로세스 — data/live_trading.db 만 공유.
대시보드 UI: http://127.0.0.1:8765/
API 문서:   http://127.0.0.1:8765/docs
"""
from __future__ import annotations

import argparse
import os
import sys

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _load_env_file(path: str) -> None:
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh.read().splitlines():
                s = str(raw).strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                key, val = k.strip(), v.strip().strip("\"'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception:
        pass


_load_env_file(os.path.join(project_root, ".env"))


def main() -> None:
    p = argparse.ArgumentParser(description="v6.0 라이브 대시보드 API")
    p.add_argument("--host", default=os.getenv("LIVE_DASHBOARD_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.getenv("LIVE_DASHBOARD_PORT", "8765")))
    args = p.parse_args()

    import uvicorn

    print(f"📊 대시보드 UI — http://{args.host}:{args.port}/")
    print(f"   사령탑 제어  — 스캔 · 진입 · 실시간 감시 토글")
    print(f"   API 문서    — http://{args.host}:{args.port}/docs")
    uvicorn.run(
        "src.web.dashboard_api:app",
        host=args.host,
        port=args.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
