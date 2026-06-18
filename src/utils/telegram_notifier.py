"""
v11.2 텔레그램 실시간 전술 알림.

TELEGRAM_BOT_TOKEN · TELEGRAM_CHAT_ID → .env 또는 config/config.json
메인 트레이딩 루프를 블로킹하지 않도록 daemon 스레드로 비동기 전송.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger("V11Telegram")
KST = ZoneInfo("Asia/Seoul")

REASON_LABEL = {
    "ORB_BREAKOUT": "15분 ORB 돌파",
    "STOP_LOSS": "손절",
    "TAKE_PROFIT_FULL": "익절(+5%)",
    "PARTIAL_TP_50": "반익절(+3%)",
    "RISK_FREE_BREAKEVEN": "본전 스탑",
    "TIME_STOP_1520": "타임스탑(15:20)",
}


def _load_credentials(project_root: Path | None = None) -> tuple[str, str]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if token and chat_id:
        return token, chat_id

    root = project_root or Path(__file__).resolve().parents[2]
    for rel in ("config/config.json", "config/telegram.json"):
        cfg_path = root / rel
        if not cfg_path.is_file():
            continue
        try:
            with open(cfg_path, encoding="utf-8") as fh:
                raw = json.load(fh)
            tg = raw.get("telegram") if isinstance(raw.get("telegram"), dict) else raw
            token = token or str(tg.get("TELEGRAM_BOT_TOKEN") or tg.get("bot_token") or "").strip()
            chat_id = chat_id or str(tg.get("TELEGRAM_CHAT_ID") or tg.get("chat_id") or "").strip()
            if token and chat_id:
                break
        except Exception as exc:
            logger.debug("텔레그램 config 로드 실패 (%s): %s", cfg_path, exc)
    return token, chat_id


class TelegramNotifier:
    """v11.2 ORB 모의투자 전용 텔레그램 알림."""

    def __init__(self, *, project_root: str | Path | None = None) -> None:
        root = Path(project_root) if project_root else Path(__file__).resolve().parents[2]
        self.token, self.chat_id = _load_credentials(root)
        self._url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        if self.enabled:
            logger.info("📱 v11.2 텔레그램 알림 활성화")
        else:
            logger.info("📱 v11.2 텔레그램 비활성 — .env 또는 config/config.json 설정 필요")

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def _send_packet(self, text: str) -> None:
        if not self.enabled:
            return
        try:
            requests.post(
                self._url,
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
                timeout=5,
            )
        except Exception as exc:
            logger.warning("텔레그램 전송 실패 (무시): %s", exc)

    def send(self, text: str) -> None:
        if not self.enabled:
            return
        threading.Thread(target=self._send_packet, args=(text,), daemon=True).start()

    @staticmethod
    def _hm_now() -> str:
        return datetime.now(KST).strftime("%H:%M")

    def notify_startup(self, *, watch_count: int, watch_preview: str = "") -> None:
        preview = f"\n📋 {watch_preview}" if watch_preview else ""
        self.send(
            f"🚀 <b>v11.2 라이브 모의투자 봇 기동 완료.</b>\n"
            f"감시 종목 {watch_count}개 세팅 완료.{preview}"
        )

    def notify_buy(self, *, name: str, code: str, price: float, hm: str | None = None) -> None:
        t = hm or self._hm_now()
        self.send(
            f"🟢 <b>[{t}] 매수 격발</b>: {name} ({code})\n"
            f"체결가: {price:,.0f}원\n"
            f"사유: 15분 ORB 돌파"
        )

    def notify_sell(
        self,
        *,
        name: str,
        code: str,
        pnl_rate: float,
        reason: str,
        hm: str | None = None,
    ) -> None:
        t = hm or self._hm_now()
        label = REASON_LABEL.get(reason, reason)
        self.send(
            f"🔴 <b>[{t}] 청산 완료</b>: {name} ({code})\n"
            f"손익: {pnl_rate * 100:+.1f}%\n"
            f"사유: {label}"
        )

    def notify_eod(
        self,
        *,
        total_equity: float,
        safe_vault: float,
        event: str,
        amount_moved: float = 0.0,
    ) -> None:
        event_msg = {
            "harvest": f"💰 수확 {amount_moved:,.0f}원 → 금고",
            "refill_full": f"🔒 수혈 {amount_moved:,.0f}원 (원금 복구)",
            "refill_partial": f"⚠️ 부분 수혈 {amount_moved:,.0f}원",
            "none": "변동 없음",
        }.get(event, event)
        self.send(
            f"🏦 <b>장 마감 정산</b>\n"
            f"총자산: {total_equity:,.0f}원\n"
            f"Safe Vault 금고: {safe_vault:,.0f}원\n"
            f"({event_msg})"
        )
