"""
한국투자증권 Open API 계좌 게이트웨이 — OAuth2 · 슬롯 락 · 잔고 · 주문.
.env: KIS_APP_KEY / KIS_APP_SECRET (실전), KIS_PAPER_APP_KEY / KIS_PAPER_APP_SECRET (모의)
      KIS_ACCOUNT_NUMBER, KIS_ACCOUNT_CODE, LIVE_DRY_RUN
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any

import requests
from zoneinfo import ZoneInfo

from src.live.live_config import LiveAccountConfig
from src.overnight_parity import prime_project_dotenv_from_root

KST = ZoneInfo("Asia/Seoul")
KIS_BASE_REAL = "https://openapi.koreainvestment.com:9443"
KIS_BASE_PAPER = "https://openapivts.koreainvestment.com:29443"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("LiveAccount")


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
    total_equity: float = 0.0
    stock_eval: float = 0.0
    dynamic_max_slots: int = 1


class SlotLockError(RuntimeError):
    """슬롯·예수금 부족으로 주문 차단."""


def compute_dynamic_max_slots(
    account: LiveAccountConfig,
    total_equity: float,
    *,
    raise_margin_call: bool = True,
) -> int:
    """Dynamic Max Slots = min(max(1, floor(equity/베팅)), max_slots_limit)."""
    min_cap = account.minimum_operational_capital
    min_bet = account.bet_amount_per_slot
    if total_equity < min_cap:
        if raise_margin_call:
            logger.critical(
                "🚨 [마진콜 셧다운] 현재 총자산(%s원)이 최소 운용 자금(%s원) 미만",
                f"{total_equity:,.0f}",
                f"{min_cap:,.0f}",
            )
            logger.critical(
                "🚫 기준 미달 자산으로 인한 도박성 매매를 방지하기 위해 봇의 매수 기능을 강제 동결합니다."
            )
            raise SlotLockError("Margin Call Active: Total Equity Under 50,000 KRW")
        return 0
    calculated = int(total_equity // min_bet)
    return min(
        max(account.min_slots_limit, calculated),
        account.max_slots_limit,
    )


def snapshot_after_local_fill(
    snap: AccountSnapshot,
    positions: list[LivePosition],
    *,
    cash_spent: float,
    account: LiveAccountConfig,
) -> AccountSnapshot:
    """주문 직후 잔고 API 재호출 없이 로컬 스냅샷·동적 슬롯 갱신."""
    cash = max(0.0, snap.cash - cash_spent)
    stock_eval = sum(p.qty * p.entry_price for p in positions)
    total_equity = cash + stock_eval
    dynamic = compute_dynamic_max_slots(account, total_equity, raise_margin_call=False)
    return AccountSnapshot(
        cash=cash,
        positions=list(positions),
        open_slot_count=len(positions),
        total_equity=total_equity,
        stock_eval=stock_eval,
        dynamic_max_slots=dynamic,
    )


def _is_kis_rate_limit(payload: str) -> bool:
    text = payload or ""
    return "EGW00201" in text or "초당 거래건수" in text


def _account_from_config(config: LiveAccountConfig | dict[str, Any]) -> LiveAccountConfig:
    if isinstance(config, LiveAccountConfig):
        return config
    acct = config.get("account", config) if isinstance(config, dict) else {}
    if not isinstance(acct, dict):
        raise TypeError("LiveAccountGateway: LiveAccountConfig 또는 live_trading dict 필요")
    return LiveAccountConfig(
        broker=str(acct.get("broker", "korea_investment")),
        mode=str(acct.get("mode", "paper")),
        bet_amount_per_slot=float(acct.get("bet_amount_per_slot", 50000)),
        min_slots_limit=int(acct.get("min_slots_limit", 1)),
        max_slots_limit=int(acct.get("max_slots_limit", acct.get("max_slots", 5))),
        minimum_operational_capital=float(acct.get("minimum_operational_capital", 50000)),
        buy_cost_ratio=float(acct.get("buy_cost_ratio", 0.00015)),
        sell_cost_ratio=float(acct.get("sell_cost_ratio", 0.00195)),
    )


class LiveAccountGateway:
    """증권사 API + 하드웨어 슬롯 락."""

    def __init__(
        self,
        config: LiveAccountConfig | dict[str, Any],
        *,
        dry_run: bool | None = None,
    ):
        prime_project_dotenv_from_root(Path(__file__).resolve().parents[2])
        self.account = _account_from_config(config)
        self.mode = self.account.mode
        self.base_url = KIS_BASE_REAL if self.mode == "real_money" else KIS_BASE_PAPER

        if self.mode == "paper":
            self.app_key = os.getenv("KIS_PAPER_APP_KEY", "").strip() or os.getenv("KIS_APP_KEY", "").strip()
            self.app_secret = os.getenv("KIS_PAPER_APP_SECRET", "").strip() or os.getenv(
                "KIS_APP_SECRET", ""
            ).strip()
            acct = os.getenv("KIS_PAPER_ACCOUNT_NUMBER", "").strip() or os.getenv(
                "KIS_ACCOUNT_NUMBER", ""
            ).strip()
        else:
            self.app_key = os.getenv("KIS_APP_KEY", "").strip()
            self.app_secret = os.getenv("KIS_APP_SECRET", "").strip()
            acct = os.getenv("KIS_ACCOUNT_NUMBER", "").strip()

        self.account_number = acct or os.getenv("KIS_CANO", "").strip()
        self.account_code = os.getenv("KIS_ACCOUNT_CODE", "").strip() or os.getenv("KIS_ACNT_PRDT_CD", "01").strip()
        if len(self.account_number) > 8:
            self.account_code = self.account_number[8:10] or self.account_code
            self.account_number = self.account_number[:8]

        self.access_token: str | None = None
        self._token_expires: datetime | None = None
        self._last_kis_call = 0.0
        self._kis_min_interval = float(os.getenv("KIS_API_MIN_INTERVAL_SEC", "0.5"))
        self.dry_run = dry_run if dry_run is not None else self._detect_dry_run()

        if self.dry_run:
            logger.warning("⚠️ [DRY-RUN] 실제 API 주문·잔고 조회는 시뮬레이션입니다.")
        else:
            self._issue_oauth_token()

    def _detect_dry_run(self) -> bool:
        if os.getenv("LIVE_DRY_RUN", "1").strip().lower() in ("1", "true", "yes"):
            return True
        return not (self.app_key and self.app_secret)

    def _issue_oauth_token(self) -> None:
        logger.info("📡 KIS OAuth2 토큰 발급 (%s)", self.mode)
        url = f"{self.base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }
        try:
            response = requests.post(
                url,
                headers={"content-type": "application/json"},
                data=json.dumps(payload),
                timeout=15,
            )
            if response.status_code != 200:
                logger.error("❌ 토큰 발급 실패: %s", response.text)
                raise ConnectionError("증권사 API 인증 실패 — 키·모드(paper/real)를 확인하세요.")
            res_data = response.json()
            self.access_token = str(res_data.get("access_token", ""))
            expires_in = int(res_data.get("expires_in", 86400))
            self._token_expires = datetime.now(KST) + timedelta(seconds=max(expires_in - 60, 300))
            logger.info("✅ OAuth2 토큰 발급 성공")
        except requests.RequestException as e:
            logger.error("❌ 증권사 통신 오류: %s", e)
            raise ConnectionError("증권사 API 연결 실패") from e

    def _ensure_token(self) -> str:
        if self.dry_run:
            return "DRY_RUN_TOKEN"
        if not self.access_token or (
            self._token_expires and datetime.now(KST) >= self._token_expires
        ):
            self._issue_oauth_token()
        return self.access_token or ""

    def _api_headers(self, tr_id: str) -> dict[str, str]:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._ensure_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def _throttle_kis(self) -> None:
        wait = self._kis_min_interval - (time.monotonic() - self._last_kis_call)
        if wait > 0:
            time.sleep(wait)

    def _mark_kis_call(self) -> None:
        self._last_kis_call = time.monotonic()

    def _get_json_with_retry(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str] | None = None,
        label: str = "KIS GET",
        max_attempts: int = 4,
    ) -> dict[str, Any]:
        last_err = ""
        for attempt in range(max_attempts):
            self._throttle_kis()
            response = requests.get(url, headers=headers, params=params, timeout=15)
            self._mark_kis_call()
            body = response.text
            if response.status_code == 200:
                data = response.json()
                rt = data.get("rt_cd")
                if rt is None or str(rt) == "0":
                    return data
                last_err = str(data.get("msg1", body))
                if _is_kis_rate_limit(body) and attempt + 1 < max_attempts:
                    delay = 1.0 + attempt * 0.7
                    logger.warning("%s 한도 초과 — %.1fs 후 재시도 (%d/%d)", label, delay, attempt + 1, max_attempts)
                    time.sleep(delay)
                    continue
                raise RuntimeError(f"{label} 오류: {last_err}")
            last_err = body
            if _is_kis_rate_limit(body) and attempt + 1 < max_attempts:
                delay = 1.0 + attempt * 0.7
                logger.warning("%s HTTP 한도 — %.1fs 후 재시도", label, delay)
                time.sleep(delay)
                continue
            raise RuntimeError(f"{label} 실패: {body}")
        raise RuntimeError(f"{label} 실패: {last_err}")

    def _post_json_with_retry(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
        label: str = "KIS POST",
        max_attempts: int = 4,
    ) -> dict[str, Any]:
        last_err = ""
        for attempt in range(max_attempts):
            self._throttle_kis()
            response = requests.post(
                url,
                headers=headers,
                data=json.dumps(payload),
                timeout=15,
            )
            self._mark_kis_call()
            body = response.text
            if response.status_code == 200:
                try:
                    return response.json()
                except Exception:
                    return {"raw": body}
            last_err = body
            if _is_kis_rate_limit(body) and attempt + 1 < max_attempts:
                delay = 1.0 + attempt * 0.7
                logger.warning("%s 한도 초과 — %.1fs 후 재시도", label, delay)
                time.sleep(delay)
                continue
            raise RuntimeError(f"{label} 실패: {body}")
        raise RuntimeError(f"{label} 실패: {last_err}")

    def _fetch_current_price(self, symbol: str) -> int:
        """주식현재가 시세 (FHKST01010100)."""
        c6 = str(symbol).zfill(6)
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        params = {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": c6}
        data = self._get_json_with_retry(
            url,
            headers=self._api_headers("FHKST01010100"),
            params=params,
            label="현재가 조회",
        )
        output = data.get("output") or {}
        price = int(output.get("stck_prpr") or 0)
        if price <= 0:
            raise RuntimeError(f"유효하지 않은 현재가: {c6}")
        return price

    def _raw_balance(self) -> dict[str, Any]:
        tr_id = "TTTC8434R" if self.mode == "real_money" else "VTTC8434R"
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        params = {
            "CANO": self.account_number,
            "ACNT_PRDT_CD": self.account_code,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        try:
            return self._get_json_with_retry(
                url,
                headers=self._api_headers(tr_id),
                params=params,
                label="잔고 조회",
            )
        except RuntimeError as e:
            msg = str(e)
            if "CHECK_ACNO" in msg:
                hint = (
                    "모의투자 전용 계좌번호(8자리)를 .env 의 "
                    "KIS_PAPER_ACCOUNT_NUMBER 에 설정하세요. "
                    "실전 계좌번호와 다릅니다."
                )
                raise RuntimeError(f"잔고 조회 계좌 오류: {msg} — {hint}") from e
            raise

    def _build_snapshot(
        self,
        *,
        cash: float,
        positions: list[LivePosition],
        total_equity: float | None = None,
        stock_eval: float | None = None,
    ) -> AccountSnapshot:
        stock = stock_eval if stock_eval is not None else sum(
            p.qty * p.entry_price for p in positions
        )
        equity = total_equity if total_equity is not None else (cash + stock)
        dynamic = compute_dynamic_max_slots(self.account, equity)
        return AccountSnapshot(
            cash=cash,
            positions=list(positions),
            open_slot_count=len(positions),
            total_equity=equity,
            stock_eval=stock,
            dynamic_max_slots=dynamic,
        )

    def get_snapshot(self, local_positions: list[LivePosition] | None = None) -> AccountSnapshot:
        """잔고·보유·총자산·동적 슬롯 한도."""
        if self.dry_run:
            positions = list(local_positions or [])
            default_cash = "10000000" if self.mode == "paper" else "100000"
            cash = float(os.getenv("LIVE_SIM_CASH", default_cash))
            return self._build_snapshot(cash=cash, positions=positions)

        data = self._raw_balance()
        holdings: list[LivePosition] = []
        stock_eval = 0.0
        for item in data.get("output1") or []:
            qty = int(item.get("hldg_qty") or item.get("ccld_qty") or 0)
            if qty <= 0:
                continue
            code = str(item.get("pdno") or item.get("pd_no") or "").zfill(6)
            avg = float(item.get("pchs_avg_pric") or item.get("avg_prvs") or 0)
            evlu = item.get("evlu_amt") or item.get("evlu_pfls_amt")
            try:
                line_eval = float(str(evlu).replace(",", "")) if evlu not in (None, "") else 0.0
            except ValueError:
                line_eval = 0.0
            if line_eval <= 0 and avg > 0:
                line_eval = avg * qty
            stock_eval += line_eval
            holdings.append(
                LivePosition(code=code, qty=qty, entry_price=avg, entry_date="", hold_days=0)
            )

        out2 = (data.get("output2") or [{}])[0]

        def _money(key: str) -> float:
            v = out2.get(key)
            if v is None or v == "":
                return 0.0
            try:
                return float(str(v).replace(",", ""))
            except ValueError:
                return 0.0

        cash = max(
            _money("ord_psbl_cash"),
            _money("prvs_rcdl_excc_amt"),
            _money("dnca_tot_amt"),
            _money("nxdy_excc_amt"),
        )
        total_equity = max(
            _money("tot_evlu_amt"),
            _money("nass_amt"),
            cash + stock_eval,
        )
        if total_equity <= 0:
            total_equity = cash + stock_eval
        if cash <= 0 and str(data.get("rt_cd", "")) != "0":
            logger.warning(
                "잔고 조회 비정상 rt_cd=%s msg=%s",
                data.get("rt_cd"),
                data.get("msg1"),
            )
        return self._build_snapshot(
            cash=cash,
            positions=holdings,
            total_equity=total_equity,
            stock_eval=stock_eval,
        )

    def can_open_new_slot(self, snapshot: AccountSnapshot) -> tuple[bool, str]:
        limit = snapshot.dynamic_max_slots
        if snapshot.open_slot_count >= limit:
            return False, (
                f"🚫 [진입 제한] 동적 포트폴리오 슬롯({limit}개)이 마감되었습니다."
            )
        min_bet = self.account.bet_amount_per_slot
        if snapshot.cash < min_bet:
            return (
                False,
                f"🚫 [예수금 부족] 신규 1슬롯 진입을 위한 가용 예수금({min_bet:,.0f}원)이 모자랍니다.",
            )
        return True, "OK"

    def check_dynamic_slot_lock(
        self,
        *,
        snapshot: AccountSnapshot | None = None,
        local_positions: list[LivePosition] | None = None,
    ) -> bool:
        """
        총자산 기반 동적 슬롯(1~5) · 5만 원 미만 마진콜 셧다운.
        """
        snap = snapshot or self.get_snapshot(local_positions)
        holding_count = snap.open_slot_count
        available_cash = snap.cash
        total_equity = snap.total_equity
        dynamic_max = snap.dynamic_max_slots

        logger.info(
            "📊 [자금 관리 연산] 총자산: %s원 (예수금 %s + 평가 %s) -> 동적 슬롯 한도: %d개 (현재 보유: %d개)",
            f"{total_equity:,.0f}",
            f"{available_cash:,.0f}",
            f"{snap.stock_eval:,.0f}",
            dynamic_max,
            holding_count,
        )

        if holding_count >= dynamic_max:
            raise SlotLockError(
                f"🚫 [진입 제한] 동적 포트폴리오 슬롯({dynamic_max}개)이 마감되었습니다."
            )
        min_bet = self.account.bet_amount_per_slot
        if available_cash < min_bet:
            raise SlotLockError(
                f"🚫 [예수금 부족] 신규 1슬롯 진입을 위한 가용 예수금({min_bet:,.0f}원)이 모자랍니다."
            )
        return True

    def buy_close_price(
        self,
        code: str,
        *,
        name: str = "",
        snapshot: AccountSnapshot | None = None,
    ) -> bool | dict[str, Any]:
        c6 = str(code).zfill(6)
        try:
            snap = snapshot or self.get_snapshot()
            ok, reason = self.can_open_new_slot(snap)
            if not ok:
                raise SlotLockError(reason)
            if c6 in {p.code for p in snap.positions}:
                raise SlotLockError(f"이미 보유 중: {c6}")

            label = name or c6
            logger.info("🎯 [주문 시그널] %s (%s) 종가 매수", c6, label)

            if self.dry_run:
                qty = max(1, int(self.account.bet_amount_per_slot // 70000))
                logger.info(
                    "🧪 [DRY-RUN] %s x%d (예산 %s원)",
                    c6,
                    qty,
                    f"{self.account.bet_amount_per_slot:,.0f}",
                )
                return True

            price = self._fetch_current_price(c6)
            qty = max(1, int(self.account.bet_amount_per_slot // price))
            if snap.cash < qty * price:
                raise SlotLockError(
                    f"예수금 부족 (필요 {qty * price:,} / 보유 {snap.cash:,.0f})"
                )

            tr_id = "TTTC0012U" if self.mode == "real_money" else "VTTC0012U"
            url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
            payload = {
                "CANO": self.account_number,
                "ACNT_PRDT_CD": self.account_code,
                "PDNO": c6,
                "ORD_DVSN": "01",
                "ORD_QTY": str(qty),
                "ORD_UNPR": "0",
            }
            try:
                res = self._post_json_with_retry(
                    url,
                    headers=self._api_headers(tr_id),
                    payload=payload,
                    label="매수 주문",
                )
            except RuntimeError as e:
                logger.error("❌ 주문 실패: %s", e)
                return False
            logger.info("🚀 [주문 전송] %s x%d @ 시장가 (기준가 %s)", c6, qty, f"{price:,}")
            return res
        except SlotLockError as e:
            logger.warning("🛡️ %s", e)
            return False

    def sell_all(
        self,
        code: str,
        qty: int | None = None,
        *,
        name: str = "",
        exit_type: str = "",
        reason: str = "",
        dry_run_note: str = "",
    ) -> bool | dict[str, Any]:
        c6 = str(code).zfill(6)
        label = exit_type or reason or name or c6
        logger.info("🚨 [청산] %s | %s %s", c6, label, dry_run_note)

        if self.dry_run:
            logger.info("🧪 [DRY-RUN] SELL %s x%s", c6, qty or "ALL")
            return True

        sell_qty = qty
        if sell_qty is None:
            snap = self.get_snapshot()
            pos = next((p for p in snap.positions if p.code == c6), None)
            if not pos:
                logger.warning("보유 없음: %s", c6)
                return False
            sell_qty = pos.qty

        tr_id = "TTTC0011U" if self.mode == "real_money" else "VTTC0011U"
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        payload = {
            "CANO": self.account_number,
            "ACNT_PRDT_CD": self.account_code,
            "PDNO": c6,
            "ORD_DVSN": "01",
            "ORD_QTY": str(sell_qty),
            "ORD_UNPR": "0",
        }
        response = requests.post(
            url,
            headers=self._api_headers(tr_id),
            data=json.dumps(payload),
            timeout=15,
        )
        if response.status_code != 200:
            logger.error("❌ 매도 실패: %s", response.text)
            return False
        return response.json()
