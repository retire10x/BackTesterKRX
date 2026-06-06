"""
Angguri Studio · 텔레그램 비동기 알림 클라이언트.

환경변수 (config/.env 또는 프로젝트 루트 .env):
    TELEGRAM_BOT_TOKEN  - BotFather 발급 토큰
    TELEGRAM_CHAT_ID    - 메시지 수신 채팅 ID (개인/그룹)

사용:
    from src.automation.telegram_client import tg_client
    tg_client.send_message("본문 <b>HTML 가능</b>")

설계 원칙:
    - threading.Thread(daemon=True) 로 백그라운드 전송 → 감시 루프 속도 무영향
    - 토큰/채팅 ID 미설정 시 조용히 스킵 (봇 전체 중단 없음)
    - 네트워크 오류는 완전 격리 (except Exception: pass)
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger("TelegramClient")
KST = ZoneInfo("Asia/Seoul")


class TelegramClient:
    def __init__(self) -> None:
        self.token: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self._url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        if self.token and self.chat_id:
            logger.info("📱 텔레그램 알림 활성화 (chat_id=%s)", self.chat_id)
        else:
            logger.debug("📱 텔레그램 알림 비활성화 — .env에 TELEGRAM_BOT_TOKEN/CHAT_ID 설정 시 활성")

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def _send_packet(self, text: str) -> None:
        """실제 HTTP POST — 백그라운드 스레드에서만 호출."""
        if not self.enabled:
            return
        try:
            requests.post(
                self._url,
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
                timeout=5,
            )
        except Exception:
            pass  # 통신 오류가 봇 전체를 죽이지 않도록 완전 격리

    def send_message(self, text: str) -> None:
        """감시 루프 속도에 영향 없도록 daemon 스레드로 비동기 전송."""
        if not self.enabled:
            return
        t = threading.Thread(target=self._send_packet, args=(text,), daemon=True)
        t.start()


# 프로세스 단위 싱글턴
tg_client = TelegramClient()


# ── 메시지 빌더 헬퍼 ────────────────────────────────────────────────────────

def _now_kst_str() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def build_sync_message(
    *,
    available_cash: float,
    position_count: int,
    total_asset: float | None = None,
) -> str:
    asset_line = f"💰 총자산: {total_asset:,.0f}원\n" if total_asset else ""
    return (
        f"🟢 <b>[🤖 Angguri · 장전 완료]</b>\n"
        f"⏰ {_now_kst_str()}\n"
        f"{asset_line}"
        f"💵 예수금: {available_cash:,.0f}원\n"
        f"📦 이월 보유종목: {position_count}개\n"
        f"🔥 오늘 하루도 성투하십시오!"
    )


def build_entry_message(
    *,
    code: str,
    name: str,
    entry_price: float,
    quantity: int,
) -> str:
    invested = entry_price * quantity
    return (
        f"🚀 <b>[실전 매수 체결]</b>\n"
        f"📈 {name} ({code})\n"
        f"💵 평단: {entry_price:,.0f}원 × {quantity}주\n"
        f"💰 투입: {invested:,.0f}원\n"
        f"🔒 0.5초 매도 감시 즉시 Lock-on!"
    )


def build_exit_message(
    *,
    code: str,
    name: str,
    entry_price: float,
    exit_price: float,
    quantity: int,
    profit_rate: float,
    reason: str,
) -> str:
    REASON_LABEL = {
        "TAKE_PROFIT": "🔵 익절",
        "STOP_LOSS": "🔴 손절",
        "TIME_STOP": "⏱️ 타임스탑",
        "TIME_STOP_EOD": "🏁 장마감",
    }
    type_str = REASON_LABEL.get(reason, reason)
    pct = f"{profit_rate * 100:+.2f}%"
    realized = (exit_price - entry_price) * quantity
    return (
        f"💥 <b>[실전 청산 완료]</b>\n"
        f"📉 {name} ({code})\n"
        f"🎯 {type_str} ({pct})\n"
        f"💵 {entry_price:,.0f}원 → {exit_price:,.0f}원 × {quantity}주\n"
        f"💸 확정 손익: {realized:+,.0f}원"
    )


def build_close_message(
    *,
    total_asset: float | None,
    position_count: int,
    available_cash: float | None = None,
) -> str:
    asset_line = f"💰 최종 총자산: {total_asset:,.0f}원\n" if total_asset else ""
    cash_line = f"💵 예수금: {available_cash:,.0f}원\n" if available_cash else ""
    return (
        f"🏁 <b>[정규장 마감 브리핑]</b>\n"
        f"⏰ {_now_kst_str()}\n"
        f"{asset_line}"
        f"{cash_line}"
        f"📦 이월 보유종목: {position_count}개\n"
        f"💓 오늘 하루 엔진 정상 가동 완결."
    )


def build_reset_message(*, initial_cash: float) -> str:
    return (
        f"🔄 <b>[대시보드 초기화 완료]</b>\n"
        f"⏰ {_now_kst_str()}\n"
        f"💰 원금: {initial_cash:,.0f}원\n"
        f"🗑️ 장부·유니버스 후보군 전면 세척 완료.\n"
        f"⚠️ 새 유니버스를 스캔하고 운영을 재개하십시오."
    )
