"""
v6.0 라이브 대시보드 — REST API + 실시간 사령탑 제어.
읽기: SQLite 장부 · 쓰기(제어): LiveScreener / LiveEngine 메모리 직접 호출.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import asyncio

from fastapi import APIRouter, FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.live.live_db import (
    default_db_path,
    fetch_daily_snapshots,
    fetch_holding_rows,
    fetch_trading_history,
    fetch_trading_summary,
    init_schema,
)
from src.web.control_bridge import get_control_bridge
from src.web.ws_hub import hub as ws_hub

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_STATIC_DIR = Path(__file__).resolve().parent / "static"
_DB_PATH = os.getenv("LIVE_DB_PATH", "").strip() or default_db_path(_PROJECT_ROOT)

app = FastAPI(
    title="BackTesterKRX Live Dashboard API",
    version="6.3.0",
    description="v5.5.2 라이브 매매 대시보드 · DB 조회 + 실시간 사령탑 제어",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

control_router = APIRouter(prefix="/api/control", tags=["control"])


class WatchToggleRequest(BaseModel):
    active: bool


def _enrich_positions(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """미실현 손익 — pykrx 일봉 근사(실패 시 null)."""
    try:
        from src.live.live_engine import fetch_intraday_bar
    except Exception:
        return [{**r, "current_price": None, "unrealized_pnl_rate": None} for r in rows]

    enriched: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        symbol = str(item["symbol"])
        entry_price = float(item["entry_price"])
        bar = None
        try:
            bar = fetch_intraday_bar(symbol)
        except Exception:
            pass
        if bar and entry_price > 0:
            close = float(bar["close"])
            item["current_price"] = close
            item["unrealized_pnl_rate"] = close / entry_price - 1.0
            item["unrealized_pnl_amount"] = (close - entry_price) * int(item["quantity"])
        else:
            item["current_price"] = None
            item["unrealized_pnl_rate"] = None
            item["unrealized_pnl_amount"] = None
        enriched.append(item)
    return enriched


def _load_universe_report() -> dict[str, object]:
    bridge = get_control_bridge(_PROJECT_ROOT)
    meta_path = bridge.engine.paths.get("universe_meta") or str(
        Path(bridge.engine.paths["universe_json"]).with_suffix(".meta.json")
    )
    codes_path = bridge.engine.paths["universe_json"]
    items: list[dict[str, object]] = []
    scan_time = None
    base_date = None
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as fh:
                meta = json.load(fh)
            scan_time = meta.get("scan_time")
            base_date = meta.get("base_date_actual")
            for row in meta.get("scanned_items_report") or []:
                items.append(
                    {
                        "rank": row.get("rank"),
                        "code": str(row.get("code", "")).zfill(6),
                        "name": str(row.get("name", "")),
                        "market_cap": row.get("market_cap"),
                        "volume_amt": row.get("volume_amt"),
                    }
                )
        except Exception:
            pass
    codes: list[str] = []
    if os.path.isfile(codes_path):
        try:
            with open(codes_path, encoding="utf-8") as fh:
                raw = json.load(fh)
            codes = [str(c).strip().zfill(6) for c in raw if str(c).strip()]
        except Exception:
            pass
    return {
        "count": len(codes),
        "codes": codes,
        "scan_time": scan_time,
        "base_date": base_date,
        "items": items,
    }


@app.on_event("startup")
async def _startup() -> None:
    ws_hub.set_loop(asyncio.get_running_loop())
    init_schema(_DB_PATH)
    get_control_bridge(_PROJECT_ROOT)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """실시간 사령탑 이벤트 스트림 — SCAN / ENTRY / EXIT."""
    await ws_hub.connect(websocket)
    try:
        while True:
            msg = await websocket.receive_text()
            if msg.strip().lower() == "ping":
                await websocket.send_text('{"event":"PONG"}')
    except WebSocketDisconnect:
        ws_hub.disconnect(websocket)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, object]:
    bridge = get_control_bridge(_PROJECT_ROOT)
    return {
        "status": "ok",
        "db_path": _DB_PATH,
        "dry_run": bridge.engine.gateway.dry_run,
        "watch_active": bridge._watch.active,
    }


@app.get("/api/positions")
def api_positions() -> dict[str, object]:
    rows = fetch_holding_rows(_DB_PATH)
    positions = _enrich_positions(rows)
    updated_at = max((str(p["updated_at"]) for p in rows), default=None)
    return {"count": len(positions), "updated_at": updated_at, "positions": positions}


@app.get("/api/history")
def api_history(
    date_from: str | None = Query(None, alias="from", description="YYYYMMDD 또는 YYYY-MM-DD"),
    date_to: str | None = Query(None, alias="to", description="YYYYMMDD 또는 YYYY-MM-DD"),
) -> dict[str, object]:
    trades = fetch_trading_history(_DB_PATH, date_from=date_from, date_to=date_to)
    return {"count": len(trades), "trades": trades}


@app.get("/api/snapshots")
def api_snapshots(
    date_from: str | None = Query(None, alias="from"),
    date_to: str | None = Query(None, alias="to"),
) -> dict[str, object]:
    series = fetch_daily_snapshots(_DB_PATH, date_from=date_from, date_to=date_to)
    return {"count": len(series), "series": series}


@app.get("/api/summary")
def api_summary() -> dict[str, object]:
    return fetch_trading_summary(_DB_PATH)


@app.get("/api/universe")
def api_universe() -> dict[str, object]:
    return _load_universe_report()


@control_router.post("/scan")
def force_web_scan() -> dict[str, object]:
    """[주도주 즉시 스캔] 연산 마감까지 동기식 수행 후 완료 콜백."""
    return get_control_bridge(_PROJECT_ROOT).run_scan_sync()


@control_router.post("/entry")
def force_web_entry() -> dict[str, object]:
    """[황금 타점 즉시 진입] 연산·주문·DB 저장 마감까지 동기식 수행 후 완료 콜백."""
    return get_control_bridge(_PROJECT_ROOT).run_entry_sync()


@control_router.post("/watch/toggle")
def toggle_web_watch(req: WatchToggleRequest) -> dict[str, object]:
    """[장중 실시간 감시] 0.5초 루프 On/Off."""
    return get_control_bridge(_PROJECT_ROOT).toggle_watch(req.active)


@control_router.post("/reset")
def reset_system_database() -> dict[str, object]:
    """[마스터 시스템 초기화 v2] 장부·유니버스 후보 DB·JSON 세척 · 원금 5천만 원 스냅샷."""
    return get_control_bridge(_PROJECT_ROOT).run_reset_sync()


@control_router.get("/status")
def control_status() -> dict[str, object]:
    return get_control_bridge(_PROJECT_ROOT).status()


app.include_router(control_router)


if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
