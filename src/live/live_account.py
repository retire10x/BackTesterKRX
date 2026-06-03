"""
한국투자증권 Open API 계좌 게이트웨이 — OAuth2 · 슬롯 락 · 주문.
.env: KIS_APP_KEY, KIS_APP_SECRET, KIS_CANO, KIS_ACNT_PRDT_CD (또는 KIS_ACCOUNT_NUMBER)
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from src.live.live_config import LiveAccountConfig

KST = ZoneInfo("Asia/Seoul")

# 실전 / 모의 VTS
KIS_BASE_REAL = "https://openapi.koreainvestment.com:9443"
KIS_BASE_PAPER = "https://openapivts.koreainvestment.com:29443"


@dataclass
class LivePosition:
    code: str
    qty: int
    entry_price: float
    entry_date: str
    hold_days: int = 0


@dataclass
class AccountSnapshot:
    cash: float
    positions: list[LivePosition]
    open_slot_count: int


class SlotLockError(RuntimeError):
    """슬롯·예수금 부족으로 주문 차단."""


class LiveAccountGateway:
    """증권사 API + 하드웨어 슬롯 락."""

    def __init__(self, account: LiveAccountConfig, *, dry_run: bool | None = None):
        self.account = account
        self._token: str | None = None
        self._token_expires: datetime | None = None
        self._base_url = KIS_BASE_PAPER if account.mode == "paper" else KIS_BASE_REAL
        self._app_key = os.getenv("KIS_APP_KEY", "").strip()
        self._app_secret = os.getenv("KIS_APP_SECRET", "").strip()
        self.dry_run = dry_run if dry_run is not None else self._detect_dry_run()
        self._cano = os.getenv("KIS_CANO", "").strip()
        self._acnt_prdt = os.getenv("KIS_ACNT_PRDT_CD", "01").strip()
        acct_no = os.getenv("KIS_ACCOUNT_NUMBER", "").strip()
        if acct_no and len(acct_no) >= 8 and not self._cano:
            self._cano = acct_no[:8]
            if len(acct_no) > 8:
                self._acnt_prdt = acct_no[8:10]

    def _detect_dry_run(self) -> bool:
        if os.getenv("LIVE_DRY_RUN", "").strip().lower() in ("1", "true", "yes"):
            return True
        if not self._app_key or not self._app_secret:
            return True
        return False

    def ensure_token(self) -> str:
        if self.dry_run:
            return "DRY_RUN_TOKEN"
        if self._token and self._token_expires and datetime.now(KST) < self._token_expires:
            return self._token

        url = f"{self._base_url}/oauth2/tokenP"
        body = json.dumps({
            "grant_type": "client_credentials",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
        }).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"KIS 토큰 발급 실패: {e.read().decode()}") from e

        self._token = str(data.get("access_token", ""))
        expires_in = int(data.get("expires_in", 86400))
        self._token_expires = datetime.now(KST) + timedelta(seconds=max(expires_in - 60, 300))
        print(f"🔐 KIS OAuth2 토큰 발급 (만료 ~{expires_in}s)")
        return self._token

    def _headers(self, tr_id: str) -> dict[str, str]:
        token = self.ensure_token()
        return {
            "Content-Type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def get_snapshot(self, local_positions: list[LivePosition] | None = None) -> AccountSnapshot:
        """잔고·보유 종목 (dry_run 시 로컬 positions + 가상 현금)."""
        if self.dry_run:
            positions = list(local_positions or [])
            cash = float(os.getenv("LIVE_SIM_CASH", "100000"))
            return AccountSnapshot(
                cash=cash,
                positions=positions,
                open_slot_count=len(positions),
            )
        # 실전: TTTC8434R / VTTC8434R 등 상세 구현은 계좌 개설 후 tr_id 확정 필요
        raise NotImplementedError(
            "실전 잔고 조회는 KIS tr_id·계좌번호 검증 후 활성화하세요. "
            "개발 중에는 LIVE_DRY_RUN=1 또는 account.mode=paper 를 사용하세요."
        )

    def can_open_new_slot(self, snapshot: AccountSnapshot) -> tuple[bool, str]:
        if snapshot.open_slot_count >= self.account.max_slots:
            return False, f"슬롯 가득 ({snapshot.open_slot_count}/{self.account.max_slots})"
        need = self.account.bet_amount_per_slot * (1.0 + self.account.buy_cost_ratio)
        if snapshot.cash < need:
            return False, f"예수금 부족 (필요 {need:,.0f} / 보유 {snapshot.cash:,.0f})"
        return True, "OK"

    def buy_close_price(
        self,
        code: str,
        *,
        snapshot: AccountSnapshot | None = None,
    ) -> dict[str, Any]:
        """장마감 종가 매수 (동시호가). 슬롯 락 선검증."""
        c6 = str(code).zfill(6)
        snap = snapshot or self.get_snapshot()
        ok, reason = self.can_open_new_slot(snap)
        if not ok:
            raise SlotLockError(reason)
        if c6 in {p.code for p in snap.positions}:
            raise SlotLockError(f"이미 보유 중: {c6}")

        budget = self.account.bet_amount_per_slot
        if self.dry_run:
            print(f"🟢 [DRY_RUN] BUY {c6} 예산 {budget:,.0f}원 (종가 동시호가)")
            return {"ok": True, "dry_run": True, "code": c6, "budget": budget}

        raise NotImplementedError(
            "실전 매수 주문 API 연동은 KIS 주문 tr_id 설정 후 활성화합니다."
        )

    def sell_all(
        self,
        code: str,
        qty: int,
        *,
        exit_type: str,
        dry_run_note: str = "",
    ) -> dict[str, Any]:
        c6 = str(code).zfill(6)
        if self.dry_run:
            print(f"🔴 [DRY_RUN] SELL {c6} x{qty} ({exit_type}) {dry_run_note}")
            return {"ok": True, "dry_run": True, "code": c6, "qty": qty, "exit_type": exit_type}
        raise NotImplementedError("실전 매도 주문 API 연동 대기")
