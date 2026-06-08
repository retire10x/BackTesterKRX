"""
v6.0 라이브 대시보드 — REST API + 실시간 사령탑 제어.
읽기: SQLite 장부 · 쓰기(제어): LiveScreener / LiveEngine 메모리 직접 호출.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import asyncio
from datetime import datetime

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
    version="6.5.0",
    description="v5.5.2 라이브 매매 대시보드 · KIS 실시간 잔고 + DB 자체 통계",
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


async def _heartbeat_loop() -> None:
    """1초마다 대시보드 서버 생존 신호를 WS 클라이언트에 브로드캐스트."""
    _KST = __import__("zoneinfo").ZoneInfo("Asia/Seoul")
    while True:
        await asyncio.sleep(1.0)
        if ws_hub.client_count > 0:
            ts = datetime.now(_KST).strftime("%Y-%m-%d %H:%M:%S.%f")[:-4]
            await ws_hub._broadcast_async({"event": "HEARTBEAT", "timestamp": ts})


@app.on_event("startup")
async def _startup() -> None:
    ws_hub.set_loop(asyncio.get_running_loop())
    init_schema(_DB_PATH)
    get_control_bridge(_PROJECT_ROOT)
    asyncio.create_task(_heartbeat_loop())


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
def get_dashboard_positions() -> dict[str, object]:
    """DB 독자 장부 마스터 + KIS 잔고 참조 패널.

    보유 종목은 dry_run·실전 여부와 무관하게 SQLite holding_positions가 SSOT.
    실전 모드에서는 KIS 잔고를 추가 조회해 현재가·평가손익을 보강하고,
    kis_snapshot 필드에 증권사 잔고 원본을 담아 교차 검증에 활용한다.
    """
    # ── 1. DB 독자 장부 (항상 마스터) ──────────────────────────────────
    rows = fetch_holding_rows(_DB_PATH)

    # ── 2. KIS 현재가 보강 (실전 모드, 실패 시 무시) ───────────────────
    kis_price_map: dict[str, dict[str, object]] = {}
    kis_snapshot: dict[str, object] | None = None
    bridge = get_control_bridge(_PROJECT_ROOT)
    if not bridge.engine.gateway.dry_run:
        try:
            balances = bridge.engine.gateway.get_inquire_balance()
            for p in balances.get("positions") or []:
                sym = str(p.get("symbol", "")).zfill(6)
                kis_price_map[sym] = {
                    "current_price": p.get("current_price"),
                    "profit_rate": p.get("profit_rate"),
                }
            kis_snapshot = {
                "total_asset": balances.get("total_asset"),
                "available_cash": balances.get("available_cash"),
                "total_evaluation": balances.get("total_evaluation"),
                "stock_count": len(balances.get("positions") or []),
                "source": balances.get("source", "kis"),
                "updated_at": balances.get("updated_at"),
            }
        except Exception:
            pass

    # ── 3. DB 행 + KIS 현재가 병합 ─────────────────────────────────────
    positions: list[dict[str, object]] = []
    for r in rows:
        sym = str(r["symbol"]).zfill(6)
        kis_info = kis_price_map.get(sym, {})
        entry_price = float(r["entry_price"])
        current_price = float(kis_info.get("current_price") or entry_price)
        profit_rate = kis_info.get("profit_rate")
        if profit_rate is None and entry_price > 0:
            profit_rate = (current_price - entry_price) / entry_price
        positions.append(
            {
                **r,
                "current_price": current_price,
                "profit_rate": float(profit_rate or 0.0),
            }
        )

    return {
        "source": "db_master",
        "count": len(positions),
        "updated_at": positions[0]["updated_at"] if positions else None,
        "positions": positions,
        "kis_snapshot": kis_snapshot,
    }


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
def get_custom_trading_summary() -> dict[str, object]:
    """한투 미제공 통계 — trading_history 자체 연산."""
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
    result = get_control_bridge(_PROJECT_ROOT).run_entry_sync()
    if result.get("executed_count", 0) == 0 and result.get("status") == "rejected":
        return {
            "status": "rejected",
            "message": result.get("message", "한투 정규 매매시간이 아닙니다."),
            "executed_count": 0,
            "rejected_count": result.get("rejected_count", 0),
            "timestamp": result.get("timestamp"),
        }
    return result


@control_router.post("/watch/toggle")
def toggle_web_watch(req: WatchToggleRequest) -> dict[str, object]:
    """[장중 실시간 감시] 0.5초 루프 On/Off."""
    return get_control_bridge(_PROJECT_ROOT).toggle_watch(req.active)


@control_router.post("/sync")
def sync_kis_master() -> dict[str, object]:
    """[비상 수동 동기화] KIS 실잔고 → 로컬 DB 장부 강제 이식 · 자산 스냅샷 갱신."""
    return get_control_bridge(_PROJECT_ROOT).run_kis_sync()


@control_router.post("/reset")
def reset_system_database() -> dict[str, object]:
    """[마스터 시스템 초기화] 장부·유니버스 후보 DB·JSON 세척 · 원금 스냅샷 박제."""
    return get_control_bridge(_PROJECT_ROOT).run_reset_sync()


@control_router.get("/status")
def control_status() -> dict[str, object]:
    return get_control_bridge(_PROJECT_ROOT).status()


app.include_router(control_router)


if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
