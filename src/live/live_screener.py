"""
매일 장마감 전 코스닥 주도주 실시간 스캔 → config/live_today_universe.json
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from pykrx import stock

from src.data_loader import load_ohlcv
from src.live.live_config import (
    LiveScreenerConfig,
    LiveTradingConfig,
    load_live_config,
    load_live_settings_yaml,
    resolve_live_paths,
)
from src.utils.date_helper import resolve_overnight_scan_anchor

KST = ZoneInfo("Asia/Seoul")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("LiveScreener")


class LiveScreener:
    def __init__(
        self,
        config: LiveTradingConfig | dict[str, Any],
        *,
        project_root: str | None = None,
    ):
        if isinstance(config, LiveTradingConfig):
            self.cfg = config
            self.screener_config = config.screener
            raw = None
        else:
            raw = config if "screener" in config else {"screener": config.get("screener", config)}
            self.cfg = None
            scr = raw.get("screener", {}) if isinstance(raw, dict) else {}
            self.screener_config = scr

        scr = self.screener_config
        self.market = str(
            getattr(scr, "market", None) or (scr.get("market") if isinstance(scr, dict) else "KOSDAQ")
        ).upper()
        self.min_cap = float(
            getattr(scr, "min_market_cap", None) or scr.get("min_market_cap", 90_000_000_000)
        )
        self.max_cap = float(
            getattr(scr, "max_market_cap", None) or scr.get("max_market_cap", 400_000_000_000)
        )
        self.min_vol_amt = float(
            getattr(scr, "min_live_volume_amt", None) or scr.get("min_live_volume_amt", 5_000_000_000)
        )
        self.top_n = int(getattr(scr, "top_n", None) or scr.get("top_n", 40))
        self.min_history_bars = int(
            getattr(scr, "min_history_bars", None) or scr.get("min_history_bars", 120)
        )

        root = Path(project_root or Path(__file__).resolve().parents[2])
        if self.cfg is not None:
            paths = resolve_live_paths(self.cfg, str(root))
            self.output_path = paths["universe_json"]
            self.meta_path = paths["universe_meta"]
        else:
            rel = (
                scr.get("universe_output", "config/live_today_universe.json")
                if isinstance(scr, dict)
                else "config/live_today_universe.json"
            )
            out = Path(rel) if Path(rel).is_absolute() else root / rel
            self.output_path = str(out)
            self.meta_path = str(out.with_suffix(".meta.json"))

    @staticmethod
    def _verify_strict_pre_filter(df: pd.DataFrame, min_bars: int) -> tuple[bool, str]:
        """
        스캐너 2단계 프리필터 — .iloc[:-1] 확정 데이터 기준.

        통과 조건 (전부 AND):
          1. 일봉 수 >= min_bars (신규 상장주·데이터 부족 차단)
          2. MA60 우상향: MA60[-1] > MA60[-2]
          3. MA120 우상향: MA120[-1] > MA120[-2]
          4. 종가 > MA60 (60·120일선 정배열 통합 판정)

        반환: (통과 여부, 탈락 사유 문자열)
        """
        import numpy as np

        bars = len(df)
        if bars < min_bars:
            return False, f"일봉 {bars}개 < 최소 {min_bars}개 (신규 상장·데이터 부족)"

        # 당일 미완성 캔들 제외 확정 시리즈
        col = "close" if "close" in df.columns else ("Close" if "Close" in df.columns else None)
        if col is None:
            return False, "종가 컬럼 없음"

        confirmed = pd.to_numeric(df[col], errors="coerce").iloc[:-1]
        if len(confirmed) < 121:
            return False, f"확정 일봉 {len(confirmed)}개 < MA120 연산 최소 121개"

        ma60 = confirmed.rolling(60).mean()
        ma120 = confirmed.rolling(120).mean()

        ma60_now = float(ma60.iloc[-1])
        ma60_prev = float(ma60.iloc[-2])
        ma120_now = float(ma120.iloc[-1])
        ma120_prev = float(ma120.iloc[-2])
        last_close = float(confirmed.iloc[-1])

        if not all(np.isfinite(v) for v in (ma60_now, ma60_prev, ma120_now, ma120_prev, last_close)):
            return False, "MA60·MA120 산출 불가 (NaN)"

        if ma60_now <= ma60_prev:
            return False, f"MA60 우상향 미충족 ({ma60_prev:,.0f} → {ma60_now:,.0f})"
        if ma120_now <= ma120_prev:
            return False, f"MA120 우상향 미충족 ({ma120_prev:,.0f} → {ma120_now:,.0f})"
        if last_close <= ma60_now:
            return False, f"종가({last_close:,.0f}) ≤ MA60({ma60_now:,.0f}) — 역배열"

        return True, "OK"

    def _apply_screener_pre_filter(
        self,
        codes: list[str],
        target_date: str,
    ) -> list[str]:
        """
        스캐너 2단계 프리필터 실행기.

        각 종목의 OHLCV를 조회(250 calendar days)하여 _verify_strict_pre_filter를 적용.
        신규 상장주 + 중장기 역배열 종목을 유니버스 확정 전에 원천 차단.
        """
        if self.min_history_bars <= 0:
            return codes

        end_dt = pd.Timestamp(target_date)
        start_iso = (end_dt - pd.Timedelta(days=250)).strftime("%Y-%m-%d")
        end_iso = end_dt.strftime("%Y-%m-%d")

        passed: list[str] = []
        for code in codes:
            try:
                df = load_ohlcv(code, start_iso, end_iso)
                if df is None or df.empty:
                    logger.warning("⛔ [OHLCV 없음 차단] %s", code)
                    continue
                ok, reason = self._verify_strict_pre_filter(df, self.min_history_bars)
                if ok:
                    passed.append(code)
                else:
                    logger.warning("⛔ [프리필터 탈락] %s — %s", code, reason)
            except Exception as e:
                logger.warning("⛔ [OHLCV 조회 오류 차단] %s — %s", code, e)

        removed = len(codes) - len(passed)
        if removed:
            logger.info("🔒 [2단계 프리필터] %d종 탈락 → 잔여 %d종", removed, len(passed))
        return passed

    def execute_daily_scan(self, force_date: str | None = None) -> list[str]:
        """
        매일 오후 3시 15분(KST) 전후 가동.
        force_date: 테스트용 YYYYMMDD (미지정 시 마지막 영업일 자동 결정)
        """
        if force_date:
            target_date = force_date
        else:
            now_kst = datetime.now(KST)
            anchor = resolve_overnight_scan_anchor(now_kst.date(), reference_now=now_kst)
            target_date = pd.Timestamp(anchor.anchor_date).strftime("%Y%m%d")
            today_str = now_kst.strftime("%Y%m%d")
            if target_date != today_str:
                logger.info(
                    "📅 오늘(%s) 비영업일 → 마지막 거래일 %s 로 스캔 (policy: %s)",
                    today_str,
                    target_date,
                    anchor.anchor_policy_reason,
                )
        logger.info("📡 [실전 스캐너] %s %s 주도주 스캔 시작", target_date, self.market)

        try:
            from src.overnight_parity import prime_project_dotenv_from_root

            prime_project_dotenv_from_root(Path(__file__).resolve().parents[2])

            df_market = stock.get_market_cap_by_ticker(target_date, market=self.market)
            if df_market is None or getattr(df_market, "empty", True):
                logger.warning(
                    "⚠️ %s 시장 데이터 없음 — KRX_ID/KRX_PW·영업일·--date 확인",
                    target_date,
                )
                self._write_bundle([], target_date, note="no_market_data")
                return []

            if df_market["거래대금"].sum() == 0:
                logger.warning(
                    "⚠️ %s 거래 데이터 없음 — 장 마감 후 재실행하거나 --date 로 최근 영업일 지정",
                    target_date,
                )
                self._write_bundle([], target_date, note="empty_market_data")
                return []

            df_market = df_market.reset_index()
            rename: dict[str, str] = {str(df_market.columns[0]): "code"}
            for col in df_market.columns[1:]:
                label = str(col)
                if "시가총" in label or label == "시가총액":
                    rename[col] = "market_cap"
                elif "거래대금" in label:
                    rename[col] = "volume_amt"
                elif "거래량" in label:
                    rename[col] = "volume"
                elif "상장" in label:
                    rename[col] = "shares"
                elif "종가" in label:
                    rename[col] = "close"
            df_market = df_market.rename(columns=rename)
            if "market_cap" not in df_market.columns or "volume_amt" not in df_market.columns:
                raise ValueError(f"pykrx 컬럼 매핑 실패: {list(df_market.columns)}")
            df_market["code"] = df_market["code"].astype(str).str.zfill(6)

            cap_filtered = df_market[
                (df_market["market_cap"] >= self.min_cap) & (df_market["market_cap"] <= self.max_cap)
            ]
            vol_filtered = cap_filtered[cap_filtered["volume_amt"] >= self.min_vol_amt]
            final_sorted = vol_filtered.sort_values(by="volume_amt", ascending=False)
            top_df = final_sorted.head(self.top_n)

            scanned_codes = top_df["code"].tolist()
            logger.info("✅ 1차 필터 통과 %d종 (거래대금·시총 Top %d)", len(scanned_codes), self.top_n)

            # [2단계 프리필터] 신규 상장주·역배열 종목 사전 차단 (데이터 수·MA60/120 우상향·종가>MA60)
            scanned_codes = self._apply_screener_pre_filter(scanned_codes, target_date)
            logger.info("✅ 2차 필터 통과 %d종 (데이터 수·MA 추세 검증 완료)", len(scanned_codes))

            # top_df도 scanned_codes 기준으로 재필터하여 메타 리포트 정합성 유지
            top_df = top_df[top_df["code"].isin(scanned_codes)]

            self._write_bundle(scanned_codes, target_date, top_df=top_df)
            return scanned_codes

        except Exception as e:
            logger.error("❌ 유니버스 스캔 오류: %s", e)
            return []

    def _write_bundle(
        self,
        codes: list[str],
        target_date: str,
        *,
        top_df: pd.DataFrame | None = None,
        note: str = "",
    ) -> None:
        os.makedirs(os.path.dirname(self.output_path) or ".", exist_ok=True)
        normalized = [str(c).zfill(6) for c in codes]

        with open(self.output_path, "w", encoding="utf-8") as fh:
            json.dump(normalized, fh, ensure_ascii=False, indent=2)
            fh.write("\n")

        report_items: list[dict[str, Any]] = []
        if top_df is not None and not top_df.empty:
            for _, row in top_df.iterrows():
                code = str(row["code"]).zfill(6)
                try:
                    name = stock.get_market_ticker_name(code)
                except Exception:
                    name = "Unknown"
                report_items.append(
                    {
                        "rank": len(report_items) + 1,
                        "code": code,
                        "name": name,
                        "market_cap": f"{round(row['market_cap'] / 100_000_000, 1)}억 원",
                        "volume_amt": f"{round(row['volume_amt'] / 100_000_000, 1)}억 원",
                    }
                )

        meta_report = {
            "scan_time": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
            "base_date_actual": target_date,
            "note": note,
            "filters_applied": {
                "market": self.market,
                "min_market_cap": f"{int(self.min_cap / 100_000_000)}억 원",
                "max_market_cap": f"{int(self.max_cap / 100_000_000)}억 원",
                "min_volume_amt": f"{int(self.min_vol_amt / 100_000_000)}억 원",
            },
            "total_scanned_count": len(normalized),
            "scanned_items_report": report_items,
        }
        with open(self.meta_path, "w", encoding="utf-8") as fh:
            json.dump(meta_report, fh, ensure_ascii=False, indent=2)
            fh.write("\n")

        logger.info("📁 유니버스 저장 → %s", self.output_path)
        logger.info("📋 메타 리포트 → %s", self.meta_path)


def run_live_screener(
    *,
    config: LiveTradingConfig | None = None,
    project_root: str | None = None,
    as_of_date: str | None = None,
) -> list[str]:
    """live_engine / run_live_bot.py screener 명령용."""
    cfg = config if config is not None else load_live_config()
    force = None
    if as_of_date:
        force = pd.Timestamp(as_of_date).strftime("%Y%m%d")
    scanner = LiveScreener(cfg, project_root=project_root)
    return scanner.execute_daily_scan(force_date=force)


def load_live_universe(
    *,
    config: LiveTradingConfig | None = None,
    project_root: str | None = None,
) -> list[str]:
    cfg = config if config is not None else load_live_config()
    paths = resolve_live_paths(cfg, project_root)
    path = paths["universe_json"]
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"라이브 유니버스 없음: {path}\n"
            "먼저 실행: python scripts/run_live_screener.py"
        )
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    return [str(c).strip().zfill(6) for c in raw if str(c).strip()]


def run_from_yaml(
    *,
    yaml_path: str | None = None,
    force_date: str | None = None,
    project_root: str | None = None,
) -> list[str]:
    """수동 테스트: YAML live_trading 블록만으로 스캔."""
    raw = load_live_settings_yaml(yaml_path)
    scanner = LiveScreener(raw, project_root=project_root)
    return scanner.execute_daily_scan(force_date=force_date)
