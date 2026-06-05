"""
v6.1 WebSocket 이벤트 허브 — 스캔·진입·청산 실시간 브로드캐스트.
동기 스레드(감시 루프)에서도 asyncio.run_coroutine_threadsafe 로 안전 송신.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("WsHub")


class WsHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)
        logger.info("🔌 WS 클라이언트 연결 — 총 %d", len(self._clients))

    def disconnect(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)
        logger.info("🔌 WS 클라이언트 해제 — 잔여 %d", len(self._clients))

    async def _broadcast_async(self, payload: dict[str, Any]) -> None:
        if not self._clients:
            return
        text = json.dumps(payload, ensure_ascii=False)
        dead: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    def broadcast(self, payload: dict[str, Any]) -> None:
        """워커 스레드·동기 HTTP 핸들러에서 호출."""
        if not self._loop or not self._clients:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._broadcast_async(payload), self._loop)
        except Exception:
            logger.exception("WS broadcast 실패: %s", payload.get("event"))


hub = WsHub()


def ws_broadcast(payload: dict[str, Any]) -> None:
    hub.broadcast(payload)
