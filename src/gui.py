"""
데스크톱 GUI (CustomTkinter).
차트: `output/backtest_report.png` → **tk.Canvas**/`PhotoImage`. CTk 라벨·CTkImage는 둥근 마스크로 비트맵이 잘리므로 차트 패널에 사용하지 않음.
YAML·설정 dict·툴팁: `gui_helpers`. 엔진: `src.metrics.run_backtest_detailed`.
본문·툴팁 폰트는 `gui_helpers` pt(11/10/9)로 통일, CTk `set_*_scaling(None)` 으로 OS DPI 자동 연동.
메인 레이아웃은 grid weight 기반 반응형; 우측 패널은 `grid_propagate(False)`. **v4.10** 백테스트는 `defer_chart_render` 후 차트 후처리(**v3.1 GUI:** `materialize_backtest_chart_png_bytes` 로 메모리 PNG만 생성·디스크 미기록 / CLI·레거시는 `materialize_backtest_chart_png`). **v4.11** 차트 캔버스는 같은 image item 에 `PhotoImage` 를 `itemconfig` 로 원자 교체하여 선삭제 깜빡임 방지하며, PNG 생성 대기 동안 차트 줄 Braille 로딩 표시만 갱신한다. **v4.14** 검색은 `execute_pipelined_screening`(시총 Top·매수규칙·김직선 1봉 순차 AND) 단일 파이프라인이다. **v4.14_Fix** 파이프라인 시총 하한·표시 행 상한 보정·골든 OFF 바이패스는 `stock_screener` 에서 처리한다. **v4.15** 검색 2단계는 골든 OFF 여도 진입 필터를 종봉 AND 적용. **v4.16_Patch** 김직선 3단계: 기준봉 거래량 300%/TOP3·고가돌파 허용 `τ∈[T-3,T]`·경과일·정렬. 매매 패널 키는 `merge_live_trade_panel_into_strategy`/`extract_live_strategy_config`.
"""
from __future__ import annotations

import copy
import io
import json
import math
import os
import threading
import time
from collections import deque
import tkinter as tk
from datetime import date, timedelta
from tkinter import messagebox

import customtkinter as ctk
import pandas as pd
from pandas.tseries.offsets import BDay

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")
# v3.76: set_widget_scaling / set_window_scaling 호출하지 않음 → CTk 기본값(1.0×OS DPI)으로
# Per-monitor DPI 자동 추적(ScalingTracker.deactivate_automatic_dpi_awareness=False). None 은 API 미지원.

from PIL import Image, ImageTk
from tkcalendar import DateEntry

from src.data_loader import (
    default_backtest_period_range,
    months_before,
    fetch_filtered_universe,
    fetch_listing_market_cap_krw_by_code,
    load_config,
    load_ohlcv,
    load_ohlcv_with_dynamic_buffer,
    slice_ohlcv_user_period,
    normalize_krx_listing_market,
    ohlcv_warm_start_date,
    PULLBACK_MIN_OHLCV_BARS,
    scan_leader_pullback_candidates_bulk,
)
from src.gui_helpers import (
    HoverTooltip,
    extract_live_strategy_config,
    apply_yaml_to_widgets,
    date_entry_theme_kw,
    format_gui_list_hist,
    format_gui_list_hist_pullback_snapshot,
    format_gui_list_leader_pullback,
    format_gui_list_pipeline,
    format_gui_list_triple,
    listing_market_from_gui_badge,
    gui_body_font,
    gui_summary_five_lines,
    history_row_normalize,
    parse_gui_list_row_code,
    trading_rules_static_text,
    try_build_config,
    GUI_DATE_ENTRY_WIDTH,
    GUI_FONT_SIZE_PT,
    GUI_LIST_FONT_SIZE_PT,
    gui_ctk_font_pt,
    gui_hint_font,
    gui_list_font_tuple,
    gui_nav_font_tuple,
    gui_tk_font_pt,
    gui_action_btn_font,
    dump_last_gui_session,
    bootstrap_gui_pullback_scan_ssot,
    normalize_universe_limit_choice,
    universe_limit_combo_value,
    universe_limit_display_label,
    UNIVERSE_LIMIT_OPTIONS,
)
from src.filters import pullback_bulk_markets_for_scan
from src.backtest_constants import (
    CHART_MA_TOGGLE_PERIODS,
    CHART_ZOOM_MIN_VISIBLE_BARS,
    CHART_ZOOM_WHEEL_FACTOR,
)
from src.backtest_chart import render_backtest_chart_png_bytes, slice_chart_viewport
from src.chart_renderer import ohlc_overlay_for_chart
from src.metrics import (
    BacktestResult,
    materialize_backtest_chart_png_bytes,
    normalize_interval,
    prepare_chart_trend_ma,
    run_backtest_detailed,
)
from src.pullback_backtest import run_pullback_timeline_backtest
from src.v3_scan_config import default_pullback_scan_params
from src.utils.date_helper import resolve_overnight_scan_anchor
from src.stock_screener import (
    EntryEventTrackPick,
    KimLineOneBarPick,
    PipelineScreenerPick,
    RankedUniversePick,
    ScreenerEntry,
    default_screener_config,
    execute_pipelined_screening,
    pipeline_screener_pick_sort_tuple,
)

def _format_round_eok_krw(value_krw: float | None) -> str:
    """원화 금액을 억 단위 반올림 후 `12,345억` 형식(거래대금 등)."""
    if value_krw is None:
        return "-"
    try:
        v = float(value_krw)
    except (TypeError, ValueError):
        return "-"
    if not math.isfinite(v) or v <= 0:
        return "-"
    return f"{int(round(v / 1e8)):,d}억"


def _format_marcap_display_krw(value_krw: float | None) -> str:
    """시총: 억 반올림. 극대 시총은 UI 가독용 `4,800천억` 형(100만 억 원 이상)."""
    if value_krw is None:
        return "-"
    try:
        v = float(value_krw)
    except (TypeError, ValueError):
        return "-"
    if not math.isfinite(v) or v <= 0:
        return "-"
    eok = int(round(v / 1e8))
    if eok >= 1_000_000:
        return f"{int(round(eok / 1000)):,d}천억"
    return f"{eok:,d}억"


def _leader_pullback_bulk_fail_message(reason: str) -> str:
    """벌크 스캔 실패 사유 → 사용자 메시지 (폴백 없이 중단)."""
    r = str(reason or "").strip() or "unknown"
    if r == "cancelled":
        return "스캔이 사용자에 의해 중단되었습니다."
    if r.startswith("timeout_"):
        return (
            f"벌크 스캔 타임아웃 ({r}).\n"
            "종목별 폴백 스캔은 사용하지 않습니다. 잠시 후 다시 시도하세요."
        )
    known = {
        "krx_auth_missing": (
            "KRX 로그인 정보(KRX_ID/KRX_PW)가 없어 벌크 스캔을 수행할 수 없습니다.\n"
            "프로젝트 루트 .env 파일을 확인한 뒤 다시 시도하세요."
        ),
        "pykrx_import_failed": "pykrx 모듈을 불러오지 못했습니다.",
        "ohlcv_bulk_failed": (
            "pykrx 벌크 OHLCV 조회에 실패했습니다.\n"
            "네트워크·KRX 서비스 상태를 확인하세요."
        ),
        "ohlcv_history_short": "스캔에 필요한 영업일 이력이 부족합니다.",
        "ohlcv_join_empty": "벌크 OHLCV 종목 교집합이 비어 있습니다.",
        "ohlcv_columns_missing": "벌크 OHLCV 필수 컬럼이 누락되었습니다.",
    }
    if r in known:
        return known[r]
    return (
        f"벌크 스캔 실패 (reason={r}).\n"
        "종목별 폴백 스캔은 사용하지 않습니다."
    )


def _prime_krx_env_from_dotenv() -> None:
    """
    GUI 실행 시 `.env`를 수동 로드해 KRX 인증 누락으로 인한 벌크 실패를 줄인다.
    - python-dotenv 의존성 없이 최소 파서만 사용.
    - 이미 프로세스 환경에 값이 있으면 덮어쓰지 않는다.
    """
    if str(os.getenv("KRX_ID") or "").strip() and str(os.getenv("KRX_PW") or "").strip():
        return
    env_path = os.path.join(os.getcwd(), ".env")
    if not os.path.isfile(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return

    parsed: dict[str, str] = {}
    for raw in lines:
        line = str(raw).strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        key = str(k).strip()
        val = str(v).strip().strip('"').strip("'")
        if key:
            parsed[key] = val

    aliases = {
        "KRX_ID": ("KRX_ID", "PYKRX_ID"),
        "KRX_PW": ("KRX_PW", "PYKRX_PW"),
    }
    for target, keys in aliases.items():
        if str(os.getenv(target) or "").strip():
            continue
        for k in keys:
            vv = str(parsed.get(k) or "").strip()
            if vv:
                os.environ[target] = vv
                break


# ==========================================
# 스크리너 결과 → 리스트박스 표시용 정규화 (방어적 정렬·슬라이싱)
# ==========================================


def _screener_list_sort_key(item: object) -> tuple:
    """검색 결과 리스트 표시 순서(v4.16 김패턴 포함 파이프라인 우선 규격)."""
    if isinstance(item, PipelineScreenerPick):
        return (0,) + tuple(pipeline_screener_pick_sort_tuple(item))
    if isinstance(item, KimLineOneBarPick):
        pl = item.pattern_label
        gd = pl.startswith("고가돌파")
        zn = pl.startswith("중심선지지")
        tier = 0 if gd else (1 if zn else 2)
        age_raw = getattr(item, "kim_breakout_age_trading_days", None)
        age = age_raw if (gd and isinstance(age_raw, int)) else 99
        return (1, tier, age, -float(item.base_bar_turnover_krw), str(item.code))
    row = _screener_gui_item_to_code_name_score(item)
    if row is None:
        return (9, "")
    code, _name, sc = row
    return (2, -float(sc), str(code))


def _screener_gui_item_to_code_name_score(item: object) -> tuple[str, str, float] | None:
    """임의 객체/딕셔너리/시퀀스에서 (종목코드, 종목명, 정렬용 점수) 추출."""
    if isinstance(item, PipelineScreenerPick):
        c = str(item.code).strip().zfill(6)
        n = str(item.name).strip()
        return (c, n, float(item.combined_score or 0.0))
    if isinstance(item, EntryEventTrackPick):
        c = str(item.code).strip().zfill(6)
        n = str(item.name).strip()
        return (c, n, float(-item.signal_age_trading_days))
    if isinstance(item, KimLineOneBarPick):
        c = str(item.code).strip().zfill(6)
        n = str(item.name).strip()
        tier = 0.0 if item.pattern_label.startswith("고가돌파") else 1.0
        return (
            c,
            n,
            float(-tier * 1e15 - float(item.base_bar_turnover_krw)),
        )
    if isinstance(item, RankedUniversePick):
        c = str(item.code).strip().zfill(6)
        n = str(item.name).strip()
        return (c, n, float(item.combined_score))
    if isinstance(item, ScreenerEntry):
        c = str(item.code).strip().zfill(6)
        n = str(item.name).strip()
        return (c, n, float(item.combined_score))
    if isinstance(item, dict):
        c = str(item.get("code") or item.get("Code") or "").strip().zfill(6)
        n = str(item.get("name") or item.get("Name") or "").strip()
        raw = item.get(
            "combined_score", item.get("score", item.get("quant_score", 0.0))
        )
        try:
            sc = float(raw)
        except (TypeError, ValueError):
            sc = 0.0
        if c and c != "000000":
            return (c, n, sc)
        return None
    if isinstance(item, (tuple, list)):
        if len(item) < 2:
            return None
        c = str(item[0]).strip().zfill(6)
        n = str(item[1]).strip() if len(item) > 1 else ""
        sc = 0.0
        if len(item) >= 5:
            try:
                sc = float(item[4])
            except (TypeError, ValueError):
                sc = 0.0
        if c and c != "000000":
            return (c, n, sc)
        return None
    c = str(getattr(item, "code", "") or "").strip().zfill(6)
    n = str(getattr(item, "name", "") or "").strip()
    if not c or c == "000000":
        return None
    try:
        sc = float(getattr(item, "combined_score", 0.0))
    except (TypeError, ValueError):
        sc = 0.0
    return (c, n, sc)


# ==========================================
# [최상단 전역 변수 설정 구역] - 완벽히 정돈됨
# ==========================================
# v3.65: 좌측 패널 슬림 고정폭(기존 278px 대비 ~28% 축소)
FIXED_LEFT_W = 240
LEFT_PANEL_PAD_X = 2
LEFT_PANEL_PAD_Y = 2

# 날짜(DateEntry) 열 목표 픽셀 폭
DATE_GRID_MIN_W = 88
DATE_MONTH_NAV_BTN_W = 22
DATE_MONTH_NAV_BTN_H = 22
DATE_TODAY_BTN_W = 38

# 차트 패널: 영업일 기준(±7, ±1) 기간 평행 이동 시 라벨·자동 재실행과 연계
# 차트 이미지 위 좌·우 클릭 영역 (px, place)
CHART_NAV_STRIP_W = 50
DATE_CLAMP_MIN = date(1990, 1, 1)

# 차트 contain 타깃: 프레임 실측에서 여유를 크게 차감(저해상도·우측/하단축 미세 클립 방지)
CHART_IMG_INNER_MARGIN_X = 0
CHART_IMG_INNER_MARGIN_Y = 0
CHART_IMG_MIN_FW = 300
CHART_IMG_MIN_FH = 200

# 최근 실행 종목 이력: 메모리·디스크 모두 최대 이 개수 (FIFO)
BACKTEST_HISTORY_MAX = 30
BACKTEST_HISTORY_FILE = os.path.join("output", "backtest_history.json")

# v3.60: Top N(100/300/500) · 컴팩트 버튼·동작 타이머
GUI_MAIN_BTN_HEIGHT = 24
GUI_HIST_DEL_BTN_HEIGHT = 19
GUI_CANCEL_BTN_FG = ("#E57373", "#B45353")
GUI_CANCEL_BTN_HOVER = ("#EF5350", "#C62828")
CHART_IDLE_GUIDE_TEXT = (
    "차트에 종목을 연 뒤 [🚀 백테스트]로\n"
    "눌림목 타임라인 검증을 실행하세요."
)

# 기본 메인 창 크기 및 최소 크기(노트북·외부 모니터 공통). 실제 배치는 화면에 맞게 클램프 후 중앙 정렬.
MAIN_WINDOW_INITIAL_W = 1400
MAIN_WINDOW_INITIAL_H = 850
MAIN_WINDOW_MIN_W = 1280
MAIN_WINDOW_MIN_H = 720


class LeaderPullbackScanWorker(threading.Thread):
    """v3.30: 주도주 눌림목 스캐너 백그라운드 워커(Tk 메인 루프와 완전 분리)."""

    def __init__(
        self,
        *,
        owner: "BacktestGUI",
        market: str,
        end_date: str,
    ) -> None:
        super().__init__(daemon=True)
        self.owner = owner
        self.market = market
        self.end_date = end_date
        self.cancel_event = owner._scan_cancel_event

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        try:
            rows, evidence = self.owner._run_leader_pullback_scan(
                self.market, self.end_date
            )
            if self.cancel_event.is_set():
                self.owner.after(0, self.owner._finalize_v31_scan_cancelled)
                return
            self.owner.after(
                0,
                lambda rr=rows, ev=evidence: self.owner._finalize_v31_scan(rr, ev),
            )
        except Exception as ex:
            if self.cancel_event.is_set():
                self.owner.after(0, self.owner._finalize_v31_scan_cancelled)
                return
            self.owner.after(0, lambda m=str(ex): self.owner._finalize_search_failure(m))


class BacktestGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        gui_body_font()  # CTkFont — Tk 루트 존재 후 캐시(모듈 import 시 생성 불가)

        self.title(
            "BackTesterKRX v4.50 주도주 눌림목 스캐너 (Dynamic Buffer & 6M SSOT)"
        )

        self._apply_initial_window_geometry()

        self._candidates: list[tuple[str, str, float | None]] = []
        self._scan_result_snapshot: list[
            tuple[str, str, float, str, str, str]
        ] = []
        self._scan_evidence_by_code: dict[str, object] = {}
        self._scan_evidence_anchor: str = ""
        self._scan_ticker_market: dict[str, str] = {}
        self._last_batch_picks: list[object] = []
        self._busy = False
        self._scan_cancel_event = threading.Event()
        self._scan_thread: LeaderPullbackScanWorker | None = None
        self._backtest_busy = False
        self._backtest_cancel_event = threading.Event()
        self._cash_format_guard = False
        self._op_timer_after_id: str | None = None
        self._op_timer_start: float | None = None
        self._op_timer_btn = None
        self._op_timer_base = ""
        # 마지막으로 성공한 단일/배치 차트 종목 코드 — 차트 기간 패닝 시 YAML·리스트 무관하게 유지
        self._last_active_stock_code = ""
        # v3.88: 티커→한글 종목명 SSOT — 더블클릭·기간 내비 모두 동일 경로에서 조회
        self.ticker_to_name: dict[str, str] = {}
        # v4.8: 패닝·Refresh 시 GUI 시장 드롭다운과 달라도 성공 실행 당시 상장 시장으로 try_build 고정
        self._last_run_listing_market: str | None = None
        self._chart_ohlcv_cache_df = None  # 타입: pd.DataFrame | None
        self._chart_ohlcv_cache_code = ""
        self._img_flat_ref: ImageTk.PhotoImage | None = None
        self._last_chart_path: str | None = None
        self._last_chart_bytes: bytes | None = None
        # v4.11: 캔버스 단일 image item — itemconfig 로 스왑해 선삭제 백색 플래시 방지
        self._chart_canvas_image_item: int | None = None
        self._chart_resize_after_id: str | None = None
        self._shift_auto_run_after_id: str | None = None
        self._chart_configure_px: tuple[int, int] | None = None
        # 백그라운드 materialize 가장 최신 요청만 화면에 반영(연타 대비)
        self._chart_materialize_ticket: int = 0
        self._chart_spinner_after_id: str | None = None
        self._chart_spinner_active: bool = False
        self._chart_spinner_idx: int = 0
        # 휠 줌: 전체 봉 캐시 + 표시 구간 [i0, i1] (i1=None 이면 마지막 봉)
        self._chart_canvas_state: dict | None = None
        self._chart_zoom_i0: int = 0
        self._chart_zoom_i1: int | None = None
        self._chart_zoom_after_id: str | None = None
        # 드래그 팬: 이동 중 캔버스 이미지만 이동, 릴리스 시 구간 반영·재렌더
        self._chart_pan_active: bool = False
        self._chart_pan_press_x: int = 0
        self._chart_pan_drag_dx: int = 0
        self._chart_pan_image_origin: tuple[float, float] = (0.0, 0.0)

        self.var_interval = ctk.StringVar(value="daily")
        self.var_ma_period = ctk.StringVar(value="20")
        self._trend_vars: dict[int, ctk.BooleanVar] = {
            p: ctk.BooleanVar(value=True) for p in CHART_MA_TOGGLE_PERIODS
        }
        self.var_show_candle = ctk.BooleanVar(value=True)
        self.var_show_volume = ctk.BooleanVar(value=True)
        self.var_buy_fee_pct = ctk.StringVar(value="0.015")
        self.var_sell_fee_pct = ctk.StringVar(value="0.20")
        # v3.70: SSOT 부트스트랩 전까지 빈 값 — bootstrap_gui_pullback_scan_ssot 에서 주입
        self.var_volume_burst_multiple = ctk.StringVar(value="")
        self.var_vol_shrink_limit = ctk.StringVar(value="")
        self.var_use_momentum_filter = ctk.BooleanVar(value=True)
        self.var_keyword = ctk.StringVar(value="")
        self.var_cash = ctk.StringVar(value="5,000,000")
        self.var_pf_mcap_top100 = ctk.BooleanVar(value=False)
        self.var_pf_buy_rules = ctk.BooleanVar(value=False)
        self.var_pf_kim_candle = ctk.BooleanVar(value=False)
        # (코드, 표시명, 시총 스냅샷, 시장, 상승률문자열, 시총문자열, 거래대금문자열)
        self._history_deque: deque[tuple] = deque(
            maxlen=BACKTEST_HISTORY_MAX
        )

        self.grid_columnconfigure(0, weight=0, minsize=FIXED_LEFT_W)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)

        left = ctk.CTkFrame(
            self, corner_radius=4, width=FIXED_LEFT_W, border_width=1
        )
        left.grid(
            row=0, column=0, sticky="nsw", padx=(4, 2), pady=(6, 6)
        )
        left.grid_propagate(False)

        scan_params_block = ctk.CTkFrame(left, fg_color="transparent")
        scan_params_block.pack(
            fill="x", padx=LEFT_PANEL_PAD_X, pady=(LEFT_PANEL_PAD_Y, 2)
        )

        row_params_dates = ctk.CTkFrame(scan_params_block, fg_color="transparent")
        row_params_dates.pack(fill="x", pady=(0, 2))

        # 시작일
        d0 = ctk.CTkFrame(row_params_dates, fg_color="transparent")
        d0.pack(side="left", padx=(0, 12)) # 종료 패널과의 간격을 위해 우측 padx를 12로 확보
        # side="left" 구조에서 expand=True를 주면 프레임의 전체 높이를 꽉 채운 후, 
        # anchor="center"를 통해 정확히 세로 중앙에 글자를 배치합니다.
        ctk.CTkLabel(d0, text="시작", font=gui_body_font()).pack(
            side="left", 
            padx=(0, 6), 
            expand=True, 
            anchor="center"
        )
        self._date_start = DateEntry(
            d0,
            width=GUI_DATE_ENTRY_WIDTH,
            date_pattern="yyyy-mm-dd",
            font=gui_tk_font_pt(GUI_LIST_FONT_SIZE_PT),
            **date_entry_theme_kw(),
        )
        _ds, _de = default_backtest_period_range()
        self._date_start.set_date(_ds)
        # DateEntry도 프레임 내에서 세로 중앙 정렬되도록 expand와 anchor를 맞춰줍니다.
        self._date_start.pack(side="left", expand=True, anchor="center")

        # 종료일
        d1 = ctk.CTkFrame(row_params_dates, fg_color="transparent")
        d1.pack(side="left")
        # 종료 라벨도 세로 중앙 정렬 강제
        ctk.CTkLabel(d1, text="종료", font=gui_body_font()).pack(
            side="left", 
            padx=(0, 6), 
            expand=True, 
            anchor="center"
        )
        self._date_end = DateEntry(
            d1,
            width=GUI_DATE_ENTRY_WIDTH,
            date_pattern="yyyy-mm-dd",
            font=gui_tk_font_pt(GUI_LIST_FONT_SIZE_PT),
            **date_entry_theme_kw(),
        )
        self._date_end.set_date(_de)
        # DateEntry 세로 중앙 정렬 강제
        self._date_end.pack(side="left", expand=True, anchor="center")

        date_month_nav = ctk.CTkFrame(row_params_dates, fg_color="transparent")
        date_month_nav.pack(side="right", padx=(6, 0))
        _date_nav_btn_kw = dict(
            width=DATE_MONTH_NAV_BTN_W,
            height=DATE_MONTH_NAV_BTN_H,
            corner_radius=4,
            border_width=1,
            border_color=("gray75", "gray35"),
            fg_color=("gray95", "gray25"),
            hover_color=("gray85", "gray35"),
            text_color=("black", "white"),
            font=gui_body_font(),
            cursor="hand2",
        )
        self.btn_date_prev_month = ctk.CTkButton(
            date_month_nav,
            text="\u25C0",
            command=lambda: self._on_date_shift_months(1),
            **_date_nav_btn_kw,
        )
        self.btn_date_prev_month.pack(side="left", padx=(0, 2))
        HoverTooltip(self.btn_date_prev_month, "1개월 전으로 이동")
        self.btn_date_next_month = ctk.CTkButton(
            date_month_nav,
            text="\u25B6",
            command=lambda: self._on_date_shift_months(-1),
            **_date_nav_btn_kw,
        )
        self.btn_date_next_month.pack(side="left")
        HoverTooltip(self.btn_date_next_month, "1개월 후로 이동")
        self.btn_date_today = ctk.CTkButton(
            date_month_nav,
            text="오늘",
            width=DATE_TODAY_BTN_W,
            height=DATE_MONTH_NAV_BTN_H,
            corner_radius=4,
            border_width=1,
            border_color=("gray75", "gray35"),
            fg_color=("gray95", "gray25"),
            hover_color=("gray85", "gray35"),
            text_color=("black", "white"),
            font=gui_body_font(),
            cursor="hand2",
            command=self._on_date_reset_to_today,
        )
        self.btn_date_today.pack(side="left", padx=(4, 0))
        HoverTooltip(self.btn_date_today, "6개월 전 ~ 오늘 기간으로 설정")

        row_params_row2 = ctk.CTkFrame(scan_params_block, fg_color="transparent")
        row_params_row2.pack(fill="x")

        sf_market = ctk.CTkFrame(row_params_row2, fg_color="transparent")
        sf_market.pack(side="left", padx=(0, 3))
        ctk.CTkLabel(sf_market, text="시장", font=gui_body_font()).pack(anchor="w", pady=(0, 1))
        self.var_market = ctk.StringVar(value="KOSPI")
        ctk.CTkOptionMenu(
            sf_market,
            values=["KOSPI", "KOSDAQ", "ALL"],
            variable=self.var_market,
            width=90,
            height=26,
            font=gui_body_font(),
        ).pack(anchor="w")

        sf_univ = ctk.CTkFrame(row_params_row2, fg_color="transparent")
        sf_univ.pack(side="left", padx=(0, 3))
        ctk.CTkLabel(sf_univ, text="Top", font=gui_body_font()).pack(
            anchor="w", pady=(0, 1)
        )
        self.combo_universe = ctk.CTkComboBox(
            sf_univ,
            values=list(UNIVERSE_LIMIT_OPTIONS),
            width=92,
            height=26,
            font=gui_body_font(),
        )
        self.combo_universe.pack(anchor="w")

        f_burst = ctk.CTkFrame(row_params_row2, fg_color="transparent")
        f_burst.pack(side="left", padx=(0, 3))
        ctk.CTkLabel(f_burst, text="세력", font=gui_body_font()).pack(anchor="w", pady=(0, 1))
        ctk.CTkEntry(
            f_burst,
            textvariable=self.var_volume_burst_multiple,
            width=40,
            height=26,
            font=gui_body_font(),
        ).pack(anchor="w")

        f_shrink = ctk.CTkFrame(row_params_row2, fg_color="transparent")
        f_shrink.pack(side="left")
        ctk.CTkLabel(f_shrink, text="눌림", font=gui_body_font()).pack(anchor="w", pady=(0, 1))
        ctk.CTkEntry(
            f_shrink,
            textvariable=self.var_vol_shrink_limit,
            width=40,
            height=26,
            font=gui_body_font(),
        ).pack(anchor="w")
        ctk.CTkCheckBox(
            row_params_row2,
            text="MA5 >= MA10",
            variable=self.var_use_momentum_filter,
            checkbox_width=16,
            checkbox_height=16,
            font=gui_body_font(),
        ).pack(side="left", padx=(8, 0), anchor="s")

        row_mode = ctk.CTkFrame(left, fg_color="transparent")
        row_mode.pack(fill="x", padx=LEFT_PANEL_PAD_X, pady=(2, 2))
        ctk.CTkLabel(
            row_mode,
            text="🔥 주도주 눌림목 리스트",
            font=gui_body_font(),
        ).pack(anchor="w", pady=(0, 2))

        list_frame = ctk.CTkFrame(left, fg_color="transparent")
        list_frame.pack(fill="both", expand=True, padx=LEFT_PANEL_PAD_X, pady=(0, 2))
        self.list_codes = tk.Listbox(
            list_frame,
            height=7,
            font=gui_list_font_tuple(),
            selectmode=tk.SINGLE,
            activestyle="dotbox",
            exportselection=False,
        )
        sb = tk.Scrollbar(
            list_frame, orient="vertical", command=self.list_codes.yview
        )
        self.list_codes.configure(yscrollcommand=sb.set)
        self.list_codes.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.list_codes.bind("<Double-Button-1>", self._on_search_list_dbl_click)

        row_scan_btns = ctk.CTkFrame(left, fg_color="transparent")
        row_scan_btns.pack(fill="x", padx=LEFT_PANEL_PAD_X, pady=(0, 4))
        row_scan_btns.grid_columnconfigure(0, weight=1)
        row_scan_btns.grid_columnconfigure(1, weight=1)
        row_scan_btns.grid_columnconfigure(2, weight=1)
        self.btn_run = ctk.CTkButton(
            row_scan_btns,
            text="🔵 스캔",
            height=GUI_MAIN_BTN_HEIGHT,
            font=gui_action_btn_font(),
            command=self._on_search,
        )
        self.btn_run.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.btn_scan_cancel = ctk.CTkButton(
            row_scan_btns,
            text="🔴 스캔 중단",
            height=GUI_MAIN_BTN_HEIGHT,
            font=gui_action_btn_font(),
            fg_color=GUI_CANCEL_BTN_FG,
            hover_color=GUI_CANCEL_BTN_HOVER,
            command=self._on_scan_cancel,
            state="disabled",
        )
        self.btn_scan_cancel.grid(row=0, column=1, sticky="ew", padx=(3, 3))
        self.btn_export_evidence = ctk.CTkButton(
            row_scan_btns,
            text="📥 근거",
            height=GUI_MAIN_BTN_HEIGHT,
            font=gui_action_btn_font(),
            command=self._on_export_scan_evidence,
        )
        self.btn_export_evidence.grid(row=0, column=2, sticky="ew", padx=(3, 0))
        HoverTooltip(
            self.btn_export_evidence,
            "검출 전 종목 근거 Excel 일괄 저장 (outputs/evidences/)",
        )

        hist_block = ctk.CTkFrame(left, fg_color="transparent")
        hist_block.pack(fill="x", padx=LEFT_PANEL_PAD_X, pady=(0, 4))

        hist_toolbar = ctk.CTkFrame(hist_block, fg_color="transparent")
        hist_toolbar.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(
            hist_toolbar,
            text=f"📂 최근 이력 (FIFO {BACKTEST_HISTORY_MAX})",
            font=gui_body_font(),
        ).pack(side="left", anchor="w")
        self.btn_history_del = ctk.CTkButton(
            hist_toolbar,
            text="🗑️ 이력 삭제",
            width=88,
            height=GUI_HIST_DEL_BTN_HEIGHT,
            font=gui_ctk_font_pt(GUI_FONT_SIZE_PT - 1),
            command=self._on_history_delete,
        )
        self.btn_history_del.pack(side="right", anchor="ne")

        hist_list_frame = ctk.CTkFrame(hist_block, fg_color="transparent")
        hist_list_frame.pack(fill="x")
        self.list_history = tk.Listbox(
            hist_list_frame,
            height=9,
            font=gui_list_font_tuple(),
            selectmode=tk.SINGLE,
            activestyle="dotbox",
            exportselection=False,
        )
        hsb = tk.Scrollbar(
            hist_list_frame, orient="vertical", command=self.list_history.yview
        )
        self.list_history.configure(yscrollcommand=hsb.set)
        self.list_history.pack(side="left", fill="both", expand=True)
        hsb.pack(side="right", fill="y")
        self.list_history.bind("<Double-Button-1>", self._on_history_list_dbl_click)

        backtest_panel = ctk.CTkFrame(left, corner_radius=2, border_width=1)
        backtest_panel.pack(fill="x", padx=LEFT_PANEL_PAD_X, pady=(2, 4))

        ctk.CTkLabel(
            backtest_panel,
            text="⚙️ 단일 종목 백테스트",
            font=gui_body_font(),
        ).pack(anchor="w", padx=2, pady=(4, 2))

        row_bt_btns = ctk.CTkFrame(backtest_panel, fg_color="transparent")
        row_bt_btns.pack(fill="x", padx=2, pady=(0, 3))
        row_bt_btns.grid_columnconfigure(0, weight=1)
        row_bt_btns.grid_columnconfigure(1, weight=1)
        self.btn_pullback_backtest = ctk.CTkButton(
            row_bt_btns,
            text="🚀 백테스트",
            height=GUI_MAIN_BTN_HEIGHT,
            font=gui_action_btn_font(),
            command=self._on_pullback_backtest,
        )
        self.btn_pullback_backtest.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.btn_backtest_cancel = ctk.CTkButton(
            row_bt_btns,
            text="⏹️ 테스트 중단",
            height=GUI_MAIN_BTN_HEIGHT,
            font=gui_action_btn_font(),
            fg_color=GUI_CANCEL_BTN_FG,
            hover_color=GUI_CANCEL_BTN_HOVER,
            command=self._on_backtest_cancel,
            state="disabled",
        )
        self.btn_backtest_cancel.grid(row=0, column=1, sticky="ew", padx=(3, 0))

        row_bt_fields = ctk.CTkFrame(backtest_panel, fg_color="transparent")
        row_bt_fields.pack(fill="x", padx=2, pady=(0, 2))

        ctk.CTkLabel(row_bt_fields, text="가상원금", font=gui_body_font()).pack(
            side="left", padx=(0, 2)
        )
        self.entry_cash_bt = ctk.CTkEntry(
            row_bt_fields,
            textvariable=self.var_cash,
            width=82,
            height=26,
            font=gui_body_font(),
        )
        self.entry_cash_bt.pack(side="left", padx=(0, 6))
        self.var_cash.trace_add("write", self._on_cash_format_trace)

        ctk.CTkLabel(row_bt_fields, text="매도시점", font=gui_body_font()).pack(
            side="left", padx=(0, 2)
        )
        self.combo_sell_timing = ctk.CTkComboBox(
            row_bt_fields,
            values=[
                "0분(시가)",
                "5분 후",
                "10분 후",
                "30분 후",
                "1시간 후",
            ],
            width=88,
            height=26,
            font=gui_body_font(),
        )
        self.combo_sell_timing.set("0분(시가)")
        self.combo_sell_timing.pack(side="left")

        self.txt_backtest_report = ctk.CTkTextbox(
            backtest_panel,
            height=110,
            font=gui_ctk_font_pt(GUI_LIST_FONT_SIZE_PT),
            wrap="word",
            border_width=1,
        )
        self.txt_backtest_report.pack(fill="both", expand=True, padx=2, pady=(2, 2))
        self.txt_backtest_report.insert(
            "1.0",
            "아직 실행된 백테스트 내역이 없습니다.\n\n"
            "차트에 종목을 고른 뒤 [🔴 백테스트]로 눌림목·타임라인 검증을 실행하세요.",
        )
        self.txt_backtest_report.configure(state="disabled")

        _hint_color = ("gray55", "gray60")
        ctk.CTkLabel(
            backtest_panel,
            text="※ 수수료 고정(0.015%/0.20%) | 매도 시점은 추후 1분봉 도입 후 활성화",
            font=gui_hint_font(),
            text_color=_hint_color,
            anchor="w",
            wraplength=FIXED_LEFT_W - 8,
        ).pack(anchor="w", padx=2, pady=(0, 4))

        self.var_filter_trend = ctk.BooleanVar(value=False)
        self.var_slope_threshold = ctk.StringVar(value="0.01")
        self.var_filter_breakout = ctk.BooleanVar(value=False)
        self.var_filter_timebuf = ctk.BooleanVar(value=False)
        self.check_slope_accel_var = tk.BooleanVar(value=False)

        self.var_golden_buy = ctk.BooleanVar(value=False)
        self.var_dead_sell = ctk.BooleanVar(value=False)

        self.var_trailing_stop = ctk.BooleanVar(value=False)
        self.var_trailing_reference_pct = ctk.StringVar(value="10")
        self.var_trailing_drop_below_pct = ctk.StringVar(value="3.0")
        self.var_trailing_drop_above_pct = ctk.StringVar(value="5.0")

        right = ctk.CTkFrame(self, corner_radius=10)
        self._right_panel = right
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 8), pady=(8, 8))
        # True 면 자식 요구 최소 높이만큼만 커져 Row2(weight=1) 차트행이 실제 화면에서 압축·클립됨
        right.grid_propagate(False)
        # Row0·1: 규칙·플레이어 — 세로 확장(weight=0)으로 고정 높이만 차지 → 남은 공간 전부 차트행으로.
        right.grid_rowconfigure(0, weight=0)
        right.grid_rowconfigure(1, weight=0)
        right.grid_rowconfigure(2, weight=1, minsize=120)
        right.grid_columnconfigure(0, weight=1)
        top_selected = ctk.CTkFrame(right, fg_color="transparent")
        top_selected.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 4))
        self.lbl_selected_stock = ctk.CTkLabel(
            top_selected,
            text="현재 선택 종목 : -",
            font=gui_ctk_font_pt(GUI_FONT_SIZE_PT, weight="bold"),
            anchor="w",
        )
        self.lbl_selected_stock.pack(side="left")

        self.btn_rules_refresh = None

        # v3.1: 우측 매매 규칙 툴박스 제거(화면 다이어트)

        # 차트 컨트롤 패널 (레거시 버튼 유지)
        self.chart_control_panel = ctk.CTkFrame(
            right, fg_color="transparent"
        )
        self.chart_control_panel.grid(row=1, column=0, sticky="new", padx=8, pady=(0, 2))

        btn_container = ctk.CTkFrame(self.chart_control_panel, fg_color="transparent")
        btn_container.pack(fill="x")

        inner_btns = ctk.CTkFrame(btn_container, fg_color="transparent")
        inner_btns.pack(side="left")
        self.btn_fast_rewind = ctk.CTkButton(
            inner_btns,
            text="⏪",
            width=36,
            height=36,
            corner_radius=4,
            border_width=1,
            border_color=("gray75", "gray35"),
            fg_color=("gray95", "gray25"),
            hover_color=("gray85", "gray35"),
            text_color=("black", "white"),
            font=gui_nav_font_tuple(),
            cursor="hand2",
            command=lambda: self._on_chart_pan_bdays(-7),
        )
        self.btn_fast_rewind.pack(side="left", padx=6)
        HoverTooltip(self.btn_fast_rewind, "7영업일 전으로 이동 (-7d)")

        self.btn_prev_7 = ctk.CTkButton(
            inner_btns,
            text="◀",
            width=36,
            height=36,
            corner_radius=4,
            border_width=1,
            border_color=("gray75", "gray35"),
            fg_color=("gray95", "gray25"),
            hover_color=("gray85", "gray35"),
            text_color=("black", "white"),
            font=gui_nav_font_tuple(),
            cursor="hand2",
            command=lambda: self._on_chart_pan_bdays(-1),
        )
        self.btn_prev_7.pack(side="left", padx=6)
        HoverTooltip(self.btn_prev_7, "1영업일 전으로 이동 (-1d)")

        self.btn_next_7 = ctk.CTkButton(
            inner_btns,
            text="▶",
            width=36,
            height=36,
            corner_radius=4,
            border_width=1,
            border_color=("gray75", "gray35"),
            fg_color=("gray95", "gray25"),
            hover_color=("gray85", "gray35"),
            text_color=("black", "white"),
            font=gui_nav_font_tuple(),
            cursor="hand2",
            command=lambda: self._on_chart_pan_bdays(1),
        )
        self.btn_next_7.pack(side="left", padx=6)
        HoverTooltip(self.btn_next_7, "1영업일 후로 이동 (+1d)")

        self.btn_fast_forward = ctk.CTkButton(
            inner_btns,
            text="⏩",
            width=36,
            height=36,
            corner_radius=4,
            border_width=1,
            border_color=("gray75", "gray35"),
            fg_color=("gray95", "gray25"),
            hover_color=("gray85", "gray35"),
            text_color=("black", "white"),
            font=gui_nav_font_tuple(),
            cursor="hand2",
            command=lambda: self._on_chart_pan_bdays(7),
        )
        self.btn_fast_forward.pack(side="left", padx=6)
        HoverTooltip(self.btn_fast_forward, "7영업일 후로 이동 (+7d)")

        # 현재 기간 표시 라벨 추가 (플레이 버튼 우측)
        self.lbl_current_period = ctk.CTkLabel(
            inner_btns,
            text="",
            font=gui_body_font(),
            text_color=("gray25", "gray75"),
        )
        self.lbl_current_period.pack(side="left", padx=(18, 6))

        self.lbl_chart_loading = ctk.CTkLabel(
            inner_btns,
            text="",
            width=18,
            font=gui_ctk_font_pt(GUI_FONT_SIZE_PT - 2),
            text_color=("gray40", "gray60"),
        )
        self.lbl_chart_loading.pack(side="left", padx=(4, 0))

        ma_toggle_frame = ctk.CTkFrame(btn_container, fg_color="transparent")
        ma_toggle_frame.pack(side="right", padx=(10, 2), pady=0)
        self._ma_toggle_checkboxes: dict[int, ctk.CTkCheckBox] = {}
        for p in CHART_MA_TOGGLE_PERIODS:
            cb = ctk.CTkCheckBox(
                ma_toggle_frame,
                text=f"{p}일",
                variable=self._trend_vars[p],
                font=gui_body_font(),
                width=24,
                checkbox_width=14,
                checkbox_height=14,
            )
            cb.pack(side="left", padx=(2, 4))
            self._ma_toggle_checkboxes[p] = cb
            HoverTooltip(cb, f"{p}일 이동평균선 표시/숨김")

        self.btn_chart_zoom_reset = ctk.CTkButton(
            ma_toggle_frame,
            text="줌 리셋",
            width=56,
            height=24,
            font=gui_body_font(),
            command=self._chart_reset_zoom,
        )
        self.btn_chart_zoom_reset.pack(side="left", padx=(4, 0))
        HoverTooltip(
            self.btn_chart_zoom_reset,
            "차트 확대/축소를 처음 구간으로 되돌립니다",
        )

        self.chart_frame = ctk.CTkFrame(
            right, fg_color=("gray95", "gray17")
        )
        self.chart_frame.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 4))

        self.chart_overlay_host = ctk.CTkFrame(
            self.chart_frame, fg_color="transparent"
        )
        self.chart_overlay_host.pack(fill="both", expand=True, padx=5, pady=5)

        self.chart_plain_frame = ctk.CTkFrame(
            self.chart_overlay_host, fg_color="transparent"
        )
        self.chart_plain_frame.pack(fill="both", expand=True)

        _flat_bg = "#2b2b2b" if ctk.get_appearance_mode() == "Dark" else "#e8ecef"
        self._chart_flat_canvas = tk.Canvas(
            self.chart_plain_frame,
            highlightthickness=0,
            bd=0,
            bg=_flat_bg,
        )
        self._chart_flat_canvas.pack(fill="both", expand=True)
        self._bind_chart_zoom_events(self._chart_flat_canvas)
        self._chart_flat_canvas.bind("<Configure>", self._on_chart_flat_canvas_configure)

        self.chart_overlay_host.bind("<Configure>", self._on_chart_frame_configure)
        self.chart_frame.bind("<Configure>", self._on_chart_frame_configure)

        apply_yaml_to_widgets(self)
        bootstrap_gui_pullback_scan_ssot(self)
        self._refresh_trading_rules_display()
        self.var_golden_buy.trace_add("write", lambda *_: self._refresh_trading_rules_display())
        self.var_dead_sell.trace_add("write", lambda *_: self._refresh_trading_rules_display())
        self.var_ma_period.trace_add("write", lambda *_: self._refresh_trading_rules_display())
        self.var_interval.trace_add("write", lambda *_: self._refresh_trading_rules_display())
        self.var_filter_trend.trace_add("write", lambda *_: self._refresh_trading_rules_display())
        self.var_filter_breakout.trace_add("write", lambda *_: self._refresh_trading_rules_display())
        self.var_filter_timebuf.trace_add("write", lambda *_: self._refresh_trading_rules_display())
        self.var_slope_threshold.trace_add("write", lambda *_: self._refresh_trading_rules_display())
        self.var_trailing_stop.trace_add(
            "write", lambda *_: self._refresh_trading_rules_display()
        )
        self.var_trailing_reference_pct.trace_add(
            "write", lambda *_: self._refresh_trading_rules_display()
        )
        self.var_trailing_drop_below_pct.trace_add(
            "write", lambda *_: self._refresh_trading_rules_display()
        )
        self.var_trailing_drop_above_pct.trace_add(
            "write", lambda *_: self._refresh_trading_rules_display()
        )

        for p in CHART_MA_TOGGLE_PERIODS:
            self._trend_vars[p].trace_add(
                "write",
                lambda *_: self.after(0, self._on_rules_refresh_chart),
            )

        self.lbl_status = ctk.CTkLabel(
            self,
            text="준비됨",
            anchor="w",
            font=gui_body_font(),
            text_color="gray",
        )
        self.lbl_status.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="ew",
            padx=16,
            pady=(0, 10),
        )

        self._set_summary("")
        self._update_period_label()

        # 매수 필터 인터락 등록 (YAML 반영값 유지 — 예전처럼 추세를 True로 강제 덮어쓰지 않음)
        self.var_filter_trend.trace_add("write", self._sync_buy_filters_interlock)
        self._sync_buy_filters_interlock()

        self._load_backtest_history_from_disk()
        self._sync_history_listbox()
        self._chart_flat_show_message(CHART_IDLE_GUIDE_TEXT)
        self.var_sell_fee_pct.set("0.2")
        self.protocol("WM_DELETE_WINDOW", self._on_user_close)

    def _refresh_trading_rules_display(self, *_args: object) -> None:
        """우측 매매 규칙 패널(읽기 전용 텍스트) - 제거됨."""
        pass

    def _chart_canvas_bg_plain(self) -> str:
        """차트 배경색(테마와 맞춤)."""
        return "#2b2b2b" if ctk.get_appearance_mode() == "Dark" else "#e8ecef"

    def _start_chart_loading_spinner(self) -> None:
        """차트 패널 인접 라벨 — 백그라운드 렌더 중 조용히 표시(v4.11)."""
        aid = getattr(self, "_chart_spinner_after_id", None)
        if aid is not None:
            try:
                self.after_cancel(aid)
            except (tk.TclError, ValueError):
                pass
            self._chart_spinner_after_id = None
        self._chart_spinner_active = True
        self._pulse_chart_loading_spinner()

    def _pulse_chart_loading_spinner(self) -> None:
        if not self._chart_spinner_active:
            return
        glyphs = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
        self._chart_spinner_idx = (self._chart_spinner_idx + 1) % len(glyphs)
        try:
            self.lbl_chart_loading.configure(text=glyphs[self._chart_spinner_idx])
        except tk.TclError:
            return
        self._chart_spinner_after_id = self.after(100, self._pulse_chart_loading_spinner)

    def _stop_chart_loading_spinner(self) -> None:
        self._chart_spinner_active = False
        aid = getattr(self, "_chart_spinner_after_id", None)
        if aid is not None:
            try:
                self.after_cancel(aid)
            except (tk.TclError, ValueError):
                pass
            self._chart_spinner_after_id = None
        try:
            self.lbl_chart_loading.configure(text="")
        except tk.TclError:
            pass

    def _chart_flat_show_message(self, message: str) -> None:
        """PNG 없을 때·오류 메시지 — 이미지 뒤에 배치하기 위해 순수 tk.Canvas 텍스트 사용."""
        self._chart_pan_active = False
        self._img_flat_ref = None
        self._chart_canvas_image_item = None
        self._last_chart_path = None
        self._last_chart_bytes = None
        c = getattr(self, "_chart_flat_canvas", None)
        if c is None:
            return

        try:
            c.delete("all")
            c.configure(bg=self._chart_canvas_bg_plain())
            
            # 기존에 남아있을 수 있는 위젯 제거
            for child in c.winfo_children():
                child.destroy()
        except tk.TclError:
            return
            
        fill = "#d0d0d0" if ctk.get_appearance_mode() == "Dark" else "#333333"
        
        try:
            # 1. 캔버스의 현재 크기 구하기 (정중앙 좌표 계산용)
            # 만약 캔버스가 아직 그려지기 전(width=1)이면 대략적인 기본값을 주거나 
            # 캔버스 너비의 절반인 c.winfo_width() / 2 를 사용합니다.
            width = max(c.winfo_width(), 200)
            height = max(c.winfo_height(), 200)
            cx = width / 2
            cy = height / 2
            
            # 2. CTkLabel 대신 순수 Canvas 텍스트 객체 생성
            # 'text_msg'라는 태그를 부여하여 추후 관리가 쉽도록 합니다.
            c.create_text(
                cx,
                cy,
                text=str(message),
                font=gui_tk_font_pt(GUI_FONT_SIZE_PT),
                fill=fill,
                justify="center",
                anchor="center",
                tags="text_msg",
            )
            
            # 3. 중요: 텍스트 레이어를 맨 아래(뒤)로 내리기
            # 이렇게 하면 나중에 캔버스에 이미지를 그릴 때 텍스트를 덮어씌울 수 있습니다.
            c.tag_lower("text_msg")
        
        except tk.TclError:
            pass

    def _recenter_canvas_text(self, event: tk.Event) -> None:
        """창·패널 리사이즈 시 idle 안내 텍스트를 정중앙에 두고 이미지 뒤 레이어로 유지."""
        c = event.widget
        try:
            if not c.find_withtag("text_msg"):
                return
            cx = max(event.width, 1) / 2
            cy = max(event.height, 1) / 2
            c.coords("text_msg", cx, cy)
            c.tag_lower("text_msg")
        except tk.TclError:
            pass

    def _on_chart_flat_canvas_configure(self, event: tk.Event) -> None:
        """캔버스 리사이즈: 안내 텍스트 재중앙 + 차트 캐시 리페인트 예약."""
        self._recenter_canvas_text(event)
        if not self._last_chart_path and not self._last_chart_bytes:
            return
        if int(getattr(event, "width", 0) or 0) < 64 or int(getattr(event, "height", 0) or 0) < 48:
            return
        if self._chart_resize_after_id is not None:
            try:
                self.after_cancel(self._chart_resize_after_id)
            except (tk.TclError, ValueError):
                pass
        self._chart_resize_after_id = self.after(75, self._deferred_repaint_chart)

    def _apply_initial_window_geometry(self) -> None:
        """
        시작 시 원하는 초기 크기로 열고 모니터 중앙에 배치한다.
        화면이 더 작으면 가로·세로 여백을 남기고 클램프한다.
        """
        try:
            self.update_idletasks()
            sw = int(self.winfo_screenwidth())
            sh = int(self.winfo_screenheight())
        except tk.TclError:
            self.geometry(f"{MAIN_WINDOW_INITIAL_W}x{MAIN_WINDOW_INITIAL_H}")
            self.minsize(MAIN_WINDOW_MIN_W, MAIN_WINDOW_MIN_H)
            return

        margin = 24
        avail_w = max(1, sw - margin)
        avail_h = max(1, sh - margin)
        ww = min(MAIN_WINDOW_INITIAL_W, avail_w)
        wh = min(MAIN_WINDOW_INITIAL_H, avail_h)

        center_x = max(0, (sw - ww) // 2)
        center_y = max(0, (sh - wh) // 2)
        self.geometry(f"{ww}x{wh}+{center_x}+{center_y}")
        self.minsize(
            min(MAIN_WINDOW_MIN_W, ww),
            min(MAIN_WINDOW_MIN_H, wh),
        )

    def _on_chart_frame_configure(self, _event: tk.Event) -> None:
        """차트 패널·호스트 크기 변경 시 overlay 실측으로 PNG contain 리페인트를 예약한다."""
        try:
            self.chart_overlay_host.update_idletasks()
        except tk.TclError:
            return
        w = int(self.chart_overlay_host.winfo_width())
        h = int(self.chart_overlay_host.winfo_height())
        self._chart_configure_px = (w, h)

        if w < 64 or h < 48:
            return
        if not self._last_chart_path and not self._last_chart_bytes:
            return
        if self._chart_resize_after_id is not None:
            self.after_cancel(self._chart_resize_after_id)
        self._chart_resize_after_id = self.after(75, self._deferred_repaint_chart)

    def _raw_chart_overlay_measured_size(self) -> tuple[int, int]:
        """chart_overlay_host 패딩 반영 전 논리 크기. winfo 미결정 시 차트 프레임·우패널에서 추정."""
        try:
            self.update_idletasks()
        except tk.TclError:
            pass

        ow = oh = 0
        try:
            ow = int(self.chart_overlay_host.winfo_width())
            oh = int(self.chart_overlay_host.winfo_height())
        except tk.TclError:
            pass
        if ow > 16 and oh > 16:
            return ow, oh

        try:
            cw = int(self.chart_frame.winfo_width())
            ch = int(self.chart_frame.winfo_height())
            if cw > 24 and ch > 28:
                return max(ow, cw - 10), max(oh, ch - 10)
        except tk.TclError:
            pass

        cp = self._chart_configure_px
        if cp and cp[0] > 20 and cp[1] > 20:
            return int(cp[0]), int(cp[1])

        try:
            rw = int(self._right_panel.winfo_width())
            rh = int(self._right_panel.winfo_height())
            if rw > 64 and rh > 110:
                reserved = min(max(220, int(rh * 0.40)), 560)
                est_h = max(CHART_IMG_MIN_FH, rh - reserved)
                est_w = max(CHART_IMG_MIN_FW, rw - 24)
                return est_w, est_h
        except (tk.TclError, AttributeError):
            pass

        try:
            ww = int(self.winfo_width())
            wh = int(self.winfo_height())
            est_w = max(CHART_IMG_MIN_FW, ww - FIXED_LEFT_W - 72)
            est_h = max(CHART_IMG_MIN_FH, wh - 380)
            if est_w > 80 and est_h > 80:
                return est_w, est_h
        except tk.TclError:
            pass

        try:
            sw = int(self.winfo_screenwidth())
            sh = int(self.winfo_screenheight())
            return (
                max(CHART_IMG_MIN_FW, int(sw * 0.44)),
                max(CHART_IMG_MIN_FH, int(sh * 0.36)),
            )
        except tk.TclError:
            return (CHART_IMG_MIN_FW * 2, int(CHART_IMG_MIN_FH * 1.5))

    def _defer_chart_image_paint(self, image_path: str | None, attempt: int = 0) -> None:
        """초기 레이아웃에서 winfo 미수령 시에도 실측 후 리페인트."""
        if not image_path or not os.path.isfile(image_path):
            self._update_chart_image(image_path)
            return
        try:
            self.update_idletasks()
        except tk.TclError:
            pass

        ow, oh = self._raw_chart_overlay_measured_size()

        tiny = ow < 90 or oh < 90
        if tiny and attempt < 14:
            self.after(42, lambda: self._defer_chart_image_paint(image_path, attempt + 1))
            return
        self._update_chart_image(image_path)

    def _chart_render_targets_for_engine(self) -> tuple[int, int] | None:
        """백테스트 워커에 전달할 PNG 타깃 픽셀(메인 스레드에서만)."""
        try:
            return self._chart_overlay_host_inner_pixel_size()
        except tk.TclError:
            return None

    def _deferred_repaint_chart(self) -> None:
        self._chart_resize_after_id = None
        if not self._last_chart_path and not self._last_chart_bytes:
            return
        st = self._chart_canvas_state
        sim = st.get("sim") if isinstance(st, dict) else None
        if sim is not None and len(sim) > 0 and not self._busy:
            try:
                self._render_chart_viewport_sync()
            except Exception:
                self._redraw_chart_from_cache()
        else:
            self._redraw_chart_from_cache()

    def _chart_overlay_host_inner_pixel_size(self) -> tuple[int, int]:
        """
        chart_overlay_host 실측 픽셀에서 마진 차감한 타깃 (프레임 맞춤 resize·PNG 동기 기준).

        호출 전 `_update_chart_image` 에서 메인 레이아웃용 `update_idletasks` 가 선행된다.
        """
        measured_width, measured_height = self._raw_chart_overlay_measured_size()

        fw = max(
            CHART_IMG_MIN_FW, measured_width - CHART_IMG_INNER_MARGIN_X
        )
        fh = max(
            CHART_IMG_MIN_FH, measured_height - CHART_IMG_INNER_MARGIN_Y
        )
        return fw, fh

    def _bind_chart_zoom_events(self, canvas: tk.Canvas) -> None:
        """차트 캔버스 휠 줌·왼쪽 드래그 팬(Windows/Linux)."""
        canvas.bind("<Enter>", lambda _e: canvas.focus_set())
        canvas.bind("<MouseWheel>", self._on_chart_mousewheel)
        canvas.bind("<Button-4>", self._on_chart_mousewheel_linux)
        canvas.bind("<Button-5>", self._on_chart_mousewheel_linux)
        canvas.bind("<ButtonPress-1>", self._on_chart_pan_press)
        canvas.bind("<B1-Motion>", self._on_chart_pan_motion)
        canvas.bind("<ButtonRelease-1>", self._on_chart_pan_release)

    def _on_chart_mousewheel(self, event: tk.Event) -> None:
        if getattr(event, "delta", 0) > 0:
            self._chart_zoom_step(1)
        elif getattr(event, "delta", 0) < 0:
            self._chart_zoom_step(-1)

    def _on_chart_mousewheel_linux(self, event: tk.Event) -> None:
        if int(getattr(event, "num", 0)) == 4:
            self._chart_zoom_step(1)
        elif int(getattr(event, "num", 0)) == 5:
            self._chart_zoom_step(-1)

    def _chart_render_kw_from_px(
        self, chart_px: tuple[int, int] | None
    ) -> dict[str, object]:
        if chart_px is None:
            return {
                "figsize": None,
                "save_dpi": 300,
                "layout_preset": "report",
            }
        w_px, h_px = chart_px
        dpi = 100
        return {
            "figsize": (
                max(3.2, float(w_px) / dpi),
                max(2.2, float(h_px) / dpi),
            ),
            "save_dpi": dpi,
            "layout_preset": "gui_target",
        }

    def _chart_state_from_replay(
        self, replay: dict, *, render_kw: dict[str, object]
    ) -> dict:
        sim = replay["sim"]
        full_close = replay["full_close"]
        trend_flags = replay["trend_flags"]
        trend_plot, trend_vis = prepare_chart_trend_ma(
            full_close, sim.index, trend_flags
        )
        for p in CHART_MA_TOGGLE_PERIODS:
            trend_vis[p] = bool(self._trend_vars[p].get())
        return {
            "sim": sim,
            "trades": list(replay.get("trades") or []),
            "name": str(replay.get("name") or ""),
            "bar_label": str(replay.get("bar_label") or "일봉"),
            "ma_n": int(replay.get("ma_n", 20)),
            "ret_series": replay["ret_series"],
            "trend_ma": trend_plot,
            "trend_visible": trend_vis,
            "show_candle": bool(replay.get("show_chart_candle", True)),
            "show_volume": bool(replay.get("show_chart_volume", True)),
            **render_kw,
        }

    def _chart_install_canvas_state(self, state: dict | None) -> None:
        """줌용 전체 봉 캐시 설치 및 구간 초기화."""
        self._chart_canvas_state = state
        self._chart_zoom_i0 = 0
        self._chart_zoom_i1 = None

    def _chart_visible_range(self) -> tuple[int, int]:
        st = self._chart_canvas_state
        if not st:
            return 0, 0
        n = len(st["sim"])
        if n <= 0:
            return 0, 0
        i0 = max(0, int(self._chart_zoom_i0))
        i1 = (
            n - 1
            if self._chart_zoom_i1 is None
            else min(n - 1, int(self._chart_zoom_i1))
        )
        if i0 > i1:
            i0, i1 = 0, n - 1
        return i0, i1

    def _chart_is_zoomed_in(self) -> bool:
        """전체 구간보다 좁게 보일 때만 드래그 팬 허용."""
        st = self._chart_canvas_state
        if not st:
            return False
        n = len(st["sim"])
        if n < 2:
            return False
        i0, i1 = self._chart_visible_range()
        return (i1 - i0 + 1) < n

    def _chart_pixels_per_bar(self) -> float:
        i0, i1 = self._chart_visible_range()
        visible = max(1, i1 - i0 + 1)
        try:
            fw, _fh = self._chart_canvas_pixel_size()
        except tk.TclError:
            fw = 800
        return max(1.0, float(fw) / float(visible))

    def _chart_canvas_pixel_size(self) -> tuple[int, int]:
        """캔버스 실측 픽셀 — 차트 PNG를 꽉 채우고 중앙 정렬."""
        cvs = getattr(self, "_chart_flat_canvas", None)
        if cvs is not None:
            try:
                cvs.update_idletasks()
                w = int(cvs.winfo_width())
                h = int(cvs.winfo_height())
                if w > 1 and h > 1:
                    return max(CHART_IMG_MIN_FW, w), max(CHART_IMG_MIN_FH, h)
            except tk.TclError:
                pass
        return self._chart_overlay_host_inner_pixel_size()

    def _chart_reset_canvas_image_position(self) -> None:
        """PNG 갱신·팬 종료 후 이미지 캔버스 정중앙 배치."""
        item = getattr(self, "_chart_canvas_image_item", None)
        cvs = getattr(self, "_chart_flat_canvas", None)
        if item is None or cvs is None:
            return
        try:
            cvs.update_idletasks()
            cx = max(1, int(cvs.winfo_width())) // 2
            cy = max(1, int(cvs.winfo_height())) // 2
            cvs.coords(item, cx, cy)
            self._chart_pan_image_origin = (float(cx), float(cy))
        except tk.TclError:
            pass
        self._chart_pan_drag_dx = 0

    def _chart_mount_photo_on_canvas(self, cvs_f, photo: ImageTk.PhotoImage) -> None:
        """캔버스 실측 중앙에 PhotoImage(CENTER) 배치 — chart_main 태그·text_msg 레이어 유지."""
        if cvs_f is None:
            return
        try:
            cvs_f.configure(bg=self._chart_canvas_bg_plain())
            cvs_f.update_idletasks()
            cx = max(1, int(cvs_f.winfo_width())) // 2
            cy = max(1, int(cvs_f.winfo_height())) // 2
        except tk.TclError:
            cx, cy = 0, 0

        self._img_flat_ref = photo
        item_existing = getattr(self, "_chart_canvas_image_item", None)
        has_chart = False
        try:
            has_chart = bool(cvs_f.find_withtag("chart_main"))
        except tk.TclError:
            has_chart = False

        if item_existing is not None and has_chart:
            try:
                cvs_f.itemconfigure(item_existing, image=photo)
                cvs_f.coords(item_existing, cx, cy)
            except tk.TclError:
                self._chart_canvas_image_item = None
                has_chart = False

        if not has_chart:
            try:
                self._chart_canvas_image_item = cvs_f.create_image(
                    cx,
                    cy,
                    anchor=tk.CENTER,
                    image=photo,
                    tags=("chart_main",),
                )
            except tk.TclError:
                return

        try:
            cvs_f.tag_lower("chart_main")
            if cvs_f.find_withtag("text_msg"):
                cvs_f.tag_lower("text_msg")
        except tk.TclError:
            pass
        self._chart_pan_image_origin = (float(cx), float(cy))
        self._chart_pan_drag_dx = 0

    def _chart_shift_viewport(self, bar_shift: int) -> None:
        if bar_shift == 0 or not self._chart_canvas_state:
            return
        n = len(self._chart_canvas_state["sim"])
        i0, i1 = self._chart_visible_range()
        new_i0 = i0 + int(bar_shift)
        new_i1 = i1 + int(bar_shift)
        if new_i0 < 0:
            new_i1 -= new_i0
            new_i0 = 0
        if new_i1 >= n:
            new_i0 -= new_i1 - (n - 1)
            new_i1 = n - 1
        new_i0 = max(0, new_i0)
        if new_i1 < new_i0:
            return
        self._chart_zoom_i0 = new_i0
        self._chart_zoom_i1 = new_i1

    def _on_chart_pan_press(self, event: tk.Event) -> None:
        if self._busy or not self._chart_is_zoomed_in():
            return
        item = getattr(self, "_chart_canvas_image_item", None)
        cvs = getattr(self, "_chart_flat_canvas", None)
        if item is None or cvs is None:
            return
        try:
            coords = cvs.coords(item)
        except tk.TclError:
            return
        self._chart_pan_active = True
        self._chart_pan_press_x = int(event.x)
        self._chart_pan_drag_dx = 0
        ox = float(coords[0]) if coords else 0.0
        oy = float(coords[1]) if len(coords) > 1 else 0.0
        self._chart_pan_image_origin = (ox, oy)
        try:
            cvs.configure(cursor="fleur")
        except tk.TclError:
            pass

    def _on_chart_pan_motion(self, event: tk.Event) -> None:
        if not self._chart_pan_active:
            return
        item = getattr(self, "_chart_canvas_image_item", None)
        cvs = getattr(self, "_chart_flat_canvas", None)
        if item is None or cvs is None:
            return
        dx = int(event.x) - self._chart_pan_press_x
        self._chart_pan_drag_dx = dx
        ox, oy = self._chart_pan_image_origin
        try:
            cvs.coords(item, ox + dx, oy)
        except tk.TclError:
            pass

    def _on_chart_pan_release(self, event: tk.Event) -> None:
        if not self._chart_pan_active:
            return
        self._chart_pan_active = False
        cvs = getattr(self, "_chart_flat_canvas", None)
        if cvs is not None:
            try:
                cvs.configure(cursor="")
            except tk.TclError:
                pass

        dx = int(self._chart_pan_drag_dx)
        self._chart_reset_canvas_image_position()

        if abs(dx) < 4 or not self._chart_canvas_state:
            return

        ppb = self._chart_pixels_per_bar()
        bar_shift = int(round(-dx / ppb)) if ppb > 0 else 0
        if bar_shift == 0:
            bar_shift = 1 if dx < 0 else -1

        self._chart_shift_viewport(bar_shift)
        if self._chart_zoom_after_id is not None:
            try:
                self.after_cancel(self._chart_zoom_after_id)
            except Exception:
                pass
            self._chart_zoom_after_id = None
        if self._busy:
            self._schedule_chart_zoom_rerender()
            return
        try:
            self._render_chart_viewport_sync()
        except Exception as e:
            self.set_status_message(f"차트 팬 갱신 실패: {e}")

    def _chart_zoom_step(self, direction: int) -> None:
        """direction: +1 확대(휠 업), -1 축소(휠 다운)."""
        if not self._chart_canvas_state:
            return
        sim = self._chart_canvas_state["sim"]
        n = len(sim)
        if n < 2:
            return
        i0, i1 = self._chart_visible_range()
        visible = i1 - i0 + 1
        center = (i0 + i1) / 2.0
        min_vis = CHART_ZOOM_MIN_VISIBLE_BARS

        if direction > 0:
            new_vis = max(min_vis, int(round(visible * CHART_ZOOM_WHEEL_FACTOR)))
        else:
            new_vis = min(
                n,
                max(min_vis, int(round(visible / CHART_ZOOM_WHEEL_FACTOR))),
            )
            if new_vis >= n:
                self._chart_reset_zoom()
                return

        new_i0 = int(round(center - (new_vis - 1) / 2.0))
        new_i1 = new_i0 + new_vis - 1
        if new_i0 < 0:
            new_i1 -= new_i0
            new_i0 = 0
        if new_i1 >= n:
            shift = new_i1 - (n - 1)
            new_i0 = max(0, new_i0 - shift)
            new_i1 = n - 1

        self._chart_zoom_i0 = new_i0
        self._chart_zoom_i1 = new_i1
        self._schedule_chart_zoom_rerender()

    def _chart_reset_zoom(self) -> None:
        if not self._chart_canvas_state:
            return
        self._chart_zoom_i0 = 0
        self._chart_zoom_i1 = None
        self._schedule_chart_zoom_rerender()

    def _schedule_chart_zoom_rerender(self) -> None:
        if self._chart_zoom_after_id is not None:
            try:
                self.after_cancel(self._chart_zoom_after_id)
            except Exception:
                pass
        self._chart_zoom_after_id = self.after(100, self._chart_zoom_rerender_flush)

    def _chart_zoom_rerender_flush(self) -> None:
        self._chart_zoom_after_id = None
        if self._busy:
            self._chart_zoom_after_id = self.after(150, self._chart_zoom_rerender_flush)
            return
        try:
            self._render_chart_viewport_sync()
        except Exception as e:
            self.set_status_message(f"차트 줌 갱신 실패: {e}")

    def _render_chart_viewport_sync(self) -> None:
        """메모리 PNG 재생성(줌·이평 토글). output/ 미기록."""
        st = self._chart_canvas_state
        if not st:
            return
        i0, i1 = self._chart_visible_range()
        sim_v, trades_v, ret_v, trend_v = slice_chart_viewport(
            st["sim"],
            st["trades"],
            st["ret_series"],
            st.get("trend_ma"),
            i0,
            i1,
        )
        trend_vis = dict(st.get("trend_visible") or {})
        for p in CHART_MA_TOGGLE_PERIODS:
            trend_vis[p] = bool(self._trend_vars[p].get())

        png_bytes = render_backtest_chart_png_bytes(
            sim_v,
            trades_v,
            str(st["name"]),
            str(st["bar_label"]),
            int(st["ma_n"]),
            ret_v,
            trend_ma=trend_v,
            trend_ma_visible=trend_vis,
            show_candle=bool(st.get("show_candle", True)),
            show_volume=bool(st.get("show_volume", True)),
            figsize=st.get("figsize"),
            save_dpi=int(st.get("save_dpi", 100)),
            layout_preset=str(st.get("layout_preset", "gui_target")),
            ohlc_overlay=st.get("ohlc_overlay"),
        )
        self._update_chart_image_from_png_bytes(png_bytes)

    def _redraw_chart_from_cache(self, attempt: int = 0) -> None:
        """캐시된 디스크 경로·메모리 PNG를 캔버스 실측 크기로 리사이즈 후 중앙 마운트."""
        try:
            self.update_idletasks()
            self.chart_frame.update_idletasks()
            self.chart_overlay_host.update_idletasks()
        except tk.TclError:
            pass

        fw, fh = self._chart_canvas_pixel_size()
        if fw <= 0 or fh <= 0:
            if attempt < 12:
                self.after(
                    42,
                    lambda a=attempt + 1: self._redraw_chart_from_cache(a),
                )
            return

        png_bytes: bytes | None = None
        if self._last_chart_bytes:
            png_bytes = self._last_chart_bytes
        elif self._last_chart_path and os.path.isfile(self._last_chart_path):
            try:
                with open(self._last_chart_path, "rb") as f:
                    png_bytes = f.read()
            except OSError:
                png_bytes = None

        if not png_bytes:
            return

        try:
            with Image.open(io.BytesIO(png_bytes)) as pil_img:
                rgb = pil_img.convert("RGB")
                resized = rgb.resize((fw, fh), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(resized)
            cvs_f = self._chart_flat_canvas
            self._chart_mount_photo_on_canvas(cvs_f, photo)
        except Exception as e:
            self._last_chart_path = None
            self._last_chart_bytes = None
            self._chart_canvas_image_item = None
            self._img_flat_ref = None
            self._chart_flat_show_message(f"이미지 로드 실패: {e}")

    def _update_chart_image(self, image_path: str | None) -> None:
        """레거시 디스크 PNG — 경로 캐시 후 공통 리드로우."""
        if not image_path or not os.path.isfile(image_path):
            self._last_chart_path = None
            self._last_chart_bytes = None
            self._chart_canvas_image_item = None
            self._img_flat_ref = None
            self._chart_flat_show_message("그래프 파일을 찾을 수 없습니다.")
            return

        self._last_chart_path = image_path
        self._last_chart_bytes = None
        self._redraw_chart_from_cache()

    def _update_chart_image_from_png_bytes(self, png_bytes: bytes | None) -> None:
        """v3.1: 메모리 PNG 바이트 캐시 후 공통 리드로우."""
        if not png_bytes:
            self._last_chart_path = None
            self._last_chart_bytes = None
            self._chart_canvas_image_item = None
            self._img_flat_ref = None
            self._chart_flat_show_message("차트 데이터가 없습니다.")
            return

        self._last_chart_bytes = png_bytes
        self._last_chart_path = None
        self._redraw_chart_from_cache()

    def _set_summary(self, text: str):
        _ = text

    def _sync_buy_filters_interlock(self, *_args: object) -> None:
        """v3.1: 우측 규칙 패널 제거로 인터락 동작 비활성화."""
        return

    def set_status_message(self, msg: str) -> None:
        """좌측 하단 상태 표시줄에 한 줄 메시지를 표시합니다."""
        self.lbl_status.configure(text=str(msg))

    def _clear_search_results_listbox(self) -> None:
        """검색 결과 리스트박스·후보 캐시·배치용 마지막 픽 무효화."""
        try:
            self.list_codes.delete(0, tk.END)
        except tk.TclError:
            pass
        self._candidates = []
        self._last_batch_picks = []

    def update_gui_with_screener_results(
        self,
        final_top_n_list: list[object],
        *,
        announce: bool = True,
    ) -> None:
        """
        검색·스크린 결과를 리스트박스에 반영(v4.14).
        PipelineScreenerPick: 티커·종목명·시총·매수조건·캔들·이격도.
        레거시 형식(ScreenerEntry 등)은 3열 또는 기존 포맷으로 표시.
        """
        try:
            self.list_codes.delete(0, tk.END)
        except tk.TclError:
            pass
        self._candidates = []
        self._last_batch_picks = []

        if not final_top_n_list:
            if announce:
                self.set_status_message("조건에 부합하는 종목이 없습니다.")
            return

        # 결과 건수 상한은 `execute_pipelined_screening` 의 disp_cap 과 일치해야 함(GUI 재잘림 금지).
        m_raw = self.var_market.get().strip().upper() or "KOSPI"
        m_use = m_raw if m_raw in ("KOSPI", "KOSDAQ", "ETF") else "KOSPI"
        mcap_fallback = fetch_listing_market_cap_krw_by_code(m_use)

        packed: list[tuple[tuple, object, str, str, str, float, float | None]] = []
        for item in final_top_n_list:
            row = _screener_gui_item_to_code_name_score(item)
            if row is None:
                continue
            code, name, sc = row
            mc = getattr(item, "market_cap_krw", None)
            if mc is None and isinstance(item, RankedUniversePick):
                mc = item.market_cap_krw
            if mc is None:
                mr = mcap_fallback.get(code) if isinstance(mcap_fallback, dict) else None
                mc = None
                if mr is not None:
                    try:
                        v = float(mr)
                        if v == v and v > 0:
                            mc = v
                    except (TypeError, ValueError):
                        mc = None

            if isinstance(item, PipelineScreenerPick):
                line = format_gui_list_pipeline(
                    code,
                    name,
                    mc,
                    entry_match_flag=item.entry_match_flag,
                    candle_pattern=item.candle_pattern,
                    spread_from_ref_pct=item.spread_from_ref_pct,
                )
            else:
                line = format_gui_list_triple(code, name, mc)

            sk = _screener_list_sort_key(item)
            packed.append((sk, item, line, code, name, float(sc), mc))

        packed.sort(key=lambda z: z[0])
        total_raw = len(packed)
        truncated = packed
        self._last_batch_picks = [r[1] for r in truncated]
        self._candidates = [(r[3], r[4], r[6]) for r in truncated]

        for sk, _it, ln, *_rest in truncated:
            self.list_codes.insert(tk.END, ln)
        for _sk, _it, _ln, code, name, _sc, _mc in truncated:
            self._register_ticker_name(code, name)
        if truncated:
            try:
                self.list_codes.selection_set(0)
            except tk.TclError:
                pass
        if announce:
            self.set_status_message(
                f"검색 완료: {len(truncated)}건 표시 (총 {total_raw}건 중 필터 적용 후)"
            )

    def _update_period_label(self) -> None:
        try:
            sd = self._date_start.get_date()
            ed = self._date_end.get_date()
            self.lbl_current_period.configure(
                text=f"조회 기간: {sd.strftime('%Y-%m-%d')} ~ {ed.strftime('%Y-%m-%d')}"
            )
        except Exception:
            pass

    def _shift_period_trading_days(self, delta_bdays: int) -> None:
        """시작·종료를 같은 영업일 수만큼 평행 이동 (BDay; 야간·공휴일 휴장은 미반영)."""
        try:
            sd = self._date_start.get_date()
            ed = self._date_end.get_date()
        except (ValueError, tk.TclError):
            return
        span = max(0, (ed - sd).days)
        today = date.today()
        try:
            ns = (pd.Timestamp(sd) + BDay(delta_bdays)).date()
            ne = (pd.Timestamp(ed) + BDay(delta_bdays)).date()
        except (ValueError, OSError, pd.errors.OutOfBoundsDatetime):
            return
        if ne > today:
            ne = today
            ns = ne - timedelta(days=span)
        if ns < DATE_CLAMP_MIN:
            ns = DATE_CLAMP_MIN
            ne = min(ns + timedelta(days=span), today)
        if ns > ne:
            ns = ne
        self._date_start.set_date(ns)
        self._date_end.set_date(ne)
        self._update_period_label()

    def _shift_period_months(self, delta_months: int) -> None:
        """시작·종료를 같은 달 수만큼 평행 이동(캘린더 월; 말일 클램프)."""
        try:
            sd = self._date_start.get_date()
            ed = self._date_end.get_date()
        except (ValueError, tk.TclError):
            return
        span = max(0, (ed - sd).days)
        today = date.today()
        try:
            ns = months_before(sd, delta_months)
            ne = months_before(ed, delta_months)
        except (ValueError, OSError):
            return
        if ne > today:
            ne = today
            ns = ne - timedelta(days=span)
        if ns < DATE_CLAMP_MIN:
            ns = DATE_CLAMP_MIN
            ne = min(ns + timedelta(days=span), today)
        if ns > ne:
            ns = ne
        self._date_start.set_date(ns)
        self._date_end.set_date(ne)
        self._update_period_label()

    def _on_date_shift_months(self, delta_months: int) -> None:
        """입력 패널 날짜 행: 1개월 단위 기간 이동 후 차트 자동 갱신(종목 선택 시)."""
        self._shift_period_months(delta_months)
        self._schedule_auto_run_after_shift()

    def _set_period_default_six_months_to_today(self) -> None:
        """시작=6개월 전, 종료=오늘 (`default_backtest_period_range` SSOT)."""
        s_d, e_d = default_backtest_period_range()
        self._date_start.set_date(s_d)
        self._date_end.set_date(e_d)
        self._update_period_label()

    def _on_date_reset_to_today(self) -> None:
        """입력 패널 '오늘': 6개월 전~오늘 기간 설정 후 차트 자동 갱신."""
        self._set_period_default_six_months_to_today()
        self._schedule_auto_run_after_shift()

    def _stash_chart_daily_cache(self, df: pd.DataFrame, code: str) -> None:
        """차트 패널 패닝용 일봉 전량 버퍼(메인 스레드 저장)."""
        self._chart_ohlcv_cache_df = df
        self._chart_ohlcv_cache_code = str(code).zfill(6)

    @property
    def current_code(self) -> str:
        """현재 차트·재실행에 유지해야 할 활성 종목(6자리). 없으면 빈 문자열."""
        c = str(getattr(self, "_last_active_stock_code", "") or "").strip().zfill(6)
        if c and c != "000000":
            return c
        cc = str(getattr(self, "_chart_ohlcv_cache_code", "") or "").strip().zfill(6)
        return cc if cc and cc != "000000" else ""

    def _register_ticker_name(self, ticker: str, name: str) -> None:
        """스캔·이력·리스트에서 수집한 종목명을 ticker_to_name 에 등록."""
        t = str(ticker or "").strip().zfill(6)
        n = str(name or "").strip()
        if t and t != "000000" and n:
            self.ticker_to_name[t] = n

    def _resolve_stock_name(self, ticker: str) -> str:
        """티커→한글명. 등록 dict·후보·이력 순으로 조회."""
        t = str(ticker or "").strip().zfill(6)
        if not t or t == "000000":
            return t
        hit = self.ticker_to_name.get(t)
        if hit:
            return hit
        for code, name, _mc in self._candidates:
            if str(code).strip().zfill(6) == t and str(name or "").strip():
                self.ticker_to_name[t] = str(name).strip()
                return self.ticker_to_name[t]
        for tup in self._history_deque:
            c, nm, _mc, _lm = history_row_normalize(tup)
            if c == t and str(nm or "").strip():
                self.ticker_to_name[t] = str(nm).strip()
                return self.ticker_to_name[t]
        return t

    def _sync_selected_stock_label(self, ticker: str, stock_name: str) -> None:
        t = str(ticker or "").strip().zfill(6)
        nm = str(stock_name or "").strip() or t
        try:
            self.lbl_selected_stock.configure(text=f"현재 선택 종목 : {t} | {nm}")
        except (tk.TclError, AttributeError):
            pass

    def get_selected_list_ticker(self) -> str:
        """검색 리스트 선택 → 티커. 없으면 빈 문자열."""
        try:
            sel = self.list_codes.curselection()
        except tk.TclError:
            return ""
        if not sel:
            return ""
        try:
            raw = self.list_codes.get(sel[0])
        except (tk.TclError, IndexError):
            return ""
        cd, _nm = self._split_codes_list_line(str(raw))
        return cd.strip().zfill(6) if cd else ""

    def render_stock_chart(
        self,
        ticker: str | None,
        *,
        period_nav: bool = False,
        listing_market_override: str | None = None,
        silent_try_build: bool = False,
    ) -> None:
        """더블클릭·기간 내비에 구애받지 않는 유일한 차트 렌더링 컨트롤러."""
        if not ticker:
            return
        t = str(ticker).strip().zfill(6)
        if not t or t == "000000":
            return
        if self._busy and not period_nav:
            return

        stock_name = self._resolve_stock_name(t)
        chart_title = stock_name
        self._sync_selected_stock_label(t, stock_name)

        cfg = try_build_config(
            self,
            silent=silent_try_build,
            selected_code_override=t,
            market_override=listing_market_override,
            period_nav=period_nav,
        )
        if cfg is None:
            if period_nav:
                self.lbl_status.configure(
                    text="기간 이동: 먼저 리스트에서 종목을 선택해 차트를 연 뒤 패닝하세요.",
                )
            else:
                self.set_status_message(
                    "설정을 만들 수 없습니다. 기간·수수료·가상 원금 입력을 확인하세요."
                )
            return

        self.update_chart_canvas(t, chart_title, cfg)

    def _on_rules_refresh_chart(self) -> None:
        """현재 선택 종목의 기간 차트만 다시 렌더링(v3.1: 비백테스트)."""
        if self._busy:
            self.set_status_message("다른 작업이 진행 중입니다. 완료 후 다시 시도하세요.")
            return
        code = self.current_code
        if not code or code == "000000":
            messagebox.showinfo(
                "차트 새로고침",
                "먼저 리스트에서 종목을 선택해 차트를 표시하세요.",
            )
            self.set_status_message(
                "활성 종목이 없습니다. 검색 결과·이력에서 종목을 선택하세요."
            )
            return
        self.render_stock_chart(code, period_nav=True)

    def _schedule_auto_run_after_shift(self) -> None:
        if self._shift_auto_run_after_id is not None:
            self.after_cancel(self._shift_auto_run_after_id)
        self._shift_auto_run_after_id = self.after(400, self._flush_auto_run_after_shift)

    def _flush_auto_run_after_shift(self) -> None:
        self._shift_auto_run_after_id = None
        if self._busy:
            self._shift_auto_run_after_id = self.after(
                280, self._flush_auto_run_after_shift
            )
            return
        nav = self.current_code
        if not nav or nav == "000000":
            self.lbl_status.configure(
                text="기간 이동: 먼저 리스트에서 종목을 선택해 차트를 연 뒤 패닝하세요.",
            )
            return
        self.render_stock_chart(nav, period_nav=True)

    def _on_chart_pan_bdays(self, delta_bdays: int) -> None:
        """차트 좌·우 오버레이: 영업일 기준으로 기간 이동 후 자동 재실행."""
        self._shift_period_trading_days(delta_bdays)
        self._schedule_auto_run_after_shift()

    def update_chart_canvas(
        self, ticker: str, chart_title: str, cfg: dict | None
    ) -> None:
        """5중 이평·거래량 캔버스 갱신 코어(v3.88 단일 렌더러 내부)."""
        if cfg is None or self._busy:
            return

        code = str(ticker or "").strip().zfill(6)
        if not code or code == "000000":
            self.set_status_message("차트 표시 대상 종목 코드가 없습니다.")
            return

        period = cfg.get("period") or {}
        start_s = str(period.get("start_date") or "").strip()
        end_s = str(period.get("end_date") or "").strip()
        if not start_s or not end_s:
            self.set_status_message("기간(start/end)을 확인하세요.")
            return

        self._pending_run_code = code
        self._busy = True
        self._update_period_label()
        self.btn_run.configure(state="disabled", text="차트 로딩 중…")
        self.set_status_message("기간별 차트 생성 중…")
        chart_px = self._chart_render_targets_for_engine()
        show_candle = bool((cfg.get("strategy") or {}).get("show_chart_candle", True))
        show_volume = bool((cfg.get("strategy") or {}).get("show_chart_volume", True))
        ma_n = int((cfg.get("strategy") or {}).get("ma_period", 20))
        trend_ma: dict[int, pd.Series] = {}
        trend_visible: dict[int, bool] = {}
        for p in CHART_MA_TOGGLE_PERIODS:
            trend_visible[p] = bool(self._trend_vars[p].get())

        title_resolved = str(chart_title or "").strip() or code

        _chart_mkt_raw = (cfg.get("universe") or {}).get("market")
        if not _chart_mkt_raw and hasattr(self, "var_market"):
            _chart_mkt_raw = self.var_market.get()
        chart_mkt = str(_chart_mkt_raw or "KOSPI").strip().upper()
        chart_market = chart_mkt if chart_mkt in ("KOSPI", "KOSDAQ") else None

        def work() -> None:
            try:
                _prime_krx_env_from_dotenv()
                pool = load_ohlcv_with_dynamic_buffer(
                    code, start_s, end_s, market=chart_market, user_slice=False
                )
                if pool is None or pool.empty:
                    raise RuntimeError("선택한 기간에 차트 데이터가 없습니다.")

                sim = slice_ohlcv_user_period(pool, start_s, end_s)
                if sim is None or sim.empty:
                    raise RuntimeError("선택한 기간에 차트 데이터가 없습니다.")
                for col in ("Open", "High", "Low", "Close"):
                    if col not in sim.columns:
                        raise RuntimeError(f"OHLCV 필수 컬럼 누락: {col}")
                if "Volume" not in sim.columns:
                    sim["Volume"] = 0.0

                sim = sim.sort_index()
                close_pool = pd.to_numeric(pool["Close"], errors="coerce")
                ret_series = pd.Series(0.0, index=sim.index)
                for p in CHART_MA_TOGGLE_PERIODS:
                    trend_ma[p] = (
                        close_pool.rolling(int(p), min_periods=1)
                        .mean()
                        .reindex(sim.index)
                    )

                figsize = None
                save_dpi = 300
                layout_preset = "report"
                if chart_px is not None:
                    w_px, h_px = chart_px
                    dpi = 100
                    figsize = (
                        max(3.2, float(w_px) / dpi),
                        max(2.2, float(h_px) / dpi),
                    )
                    save_dpi = dpi
                    layout_preset = "gui_target"

                ohlc_overlay = ohlc_overlay_for_chart(
                    sim, code, title_resolved, end_s
                )
                canvas_state = {
                    "sim": sim,
                    "trades": [],
                    "name": title_resolved,
                    "bar_label": "일봉",
                    "ma_n": ma_n,
                    "ret_series": ret_series,
                    "trend_ma": trend_ma,
                    "trend_visible": dict(trend_visible),
                    "show_candle": show_candle,
                    "show_volume": show_volume,
                    "figsize": figsize,
                    "save_dpi": save_dpi,
                    "layout_preset": layout_preset,
                    "ohlc_overlay": ohlc_overlay,
                }
                png_bytes = render_backtest_chart_png_bytes(
                    sim=sim,
                    trades=[],
                    name=title_resolved,
                    bar_label="일봉",
                    ma_n=ma_n,
                    ret_series=ret_series,
                    trend_ma=trend_ma,
                    trend_ma_visible=trend_visible,
                    show_candle=show_candle,
                    show_volume=show_volume,
                    figsize=figsize,
                    save_dpi=save_dpi,
                    layout_preset=layout_preset,
                    ohlc_overlay=ohlc_overlay,
                )

                self.after(
                    0,
                    lambda b=png_bytes, ct=title_resolved, cs=canvas_state: self._finish_chart_only(
                        code, b, ct, cs
                    ),
                )
            except Exception as e:
                self.after(0, lambda m=str(e): self._finish_chart_only_error(m))

        threading.Thread(target=work, daemon=True).start()

    def _finish_chart_only(
        self,
        code: str,
        png_bytes: bytes,
        display_name: str = "",
        canvas_state: dict | None = None,
    ) -> None:
        self._busy = False
        self.btn_run.configure(state="normal", text="🔵 스캔")
        self._last_active_stock_code = code
        if canvas_state is not None:
            self._chart_install_canvas_state(canvas_state)
        self._update_chart_image_from_png_bytes(png_bytes)
        self.set_status_message("완료")
        nm = str(display_name or "").strip() or self._resolve_stock_name(code)
        self._sync_selected_stock_label(code, nm)

    def _finish_chart_only_error(self, msg: str) -> None:
        self._busy = False
        self.btn_run.configure(state="normal", text="🔵 스캔")
        self._chart_flat_show_message(f"차트 생성 실패: {msg}")
        self.set_status_message(f"차트 생성 실패: {msg}")

    def _run_backtest(self, cfg: dict | None) -> None:
        if cfg is None or self._busy:
            return
        self._pending_run_code = str(
            (cfg.get("universe") or {}).get("selected_code") or ""
        ).zfill(6)

        cc = self._pending_run_code
        if cc and cc != getattr(self, "_chart_ohlcv_cache_code", "") and cc != "000000":
            self._chart_ohlcv_cache_df = None
            self._chart_ohlcv_cache_code = ""

        u_pb = cfg.get("universe") or {}
        mk_pb = normalize_krx_listing_market(u_pb.get("market", "KOSPI"))
        self._pending_run_listing_market = mk_pb if mk_pb is not None else "KOSPI"

        self._busy = True
        self._update_period_label()
        self.btn_run.configure(state="disabled", text="계산 중…")
        try:
            self.btn_rules_refresh.configure(state="disabled")
        except (tk.TclError, AttributeError):
            pass
        self.lbl_status.configure(text="백테스트 계산 중…")

        try:
            self.update_idletasks()
        except tk.TclError:
            pass
        chart_px = self._chart_render_targets_for_engine()

        def work():
            try:
                preload = None
                try:
                    st_iv = normalize_interval(
                        str((cfg.get("strategy") or {}).get("interval", "daily"))
                    )
                    end_s = str((cfg.get("period") or {}).get("end_date") or "").strip()
                    ust = str((cfg.get("period") or {}).get("start_date") or "").strip()
                    cd = self._pending_run_code
                    if cd and cd != "000000" and end_s:
                        cdf = getattr(self, "_chart_ohlcv_cache_df", None)
                        cdc = getattr(self, "_chart_ohlcv_cache_code", "") or ""
                        if (
                            st_iv in ("daily", "weekly")
                            and cdf is not None
                            and not cdf.empty
                            and cdc == cd
                        ):
                            tsend = pd.Timestamp(end_s).normalize()
                            ok_end = bool(tsend <= cdf.index.max().normalize())
                            ok_warm = True
                            if ust and ok_end:
                                wneed = pd.Timestamp(
                                    str(ohlcv_warm_start_date(ust, interval=st_iv))
                                ).normalize()
                                ok_warm = cdf.index.min().normalize() <= wneed
                            if ok_end and ok_warm:
                                preload = cdf
                        if preload is None and st_iv in ("daily", "weekly"):
                            ts_end = pd.Timestamp(end_s)
                            ext_candidates = [(ts_end - pd.Timedelta(days=365 * 10)).normalize()]
                            if ust:
                                ext_candidates.append(
                                    pd.Timestamp(
                                        str(
                                            ohlcv_warm_start_date(
                                                ust, interval=st_iv
                                            )
                                        )
                                    ).normalize()
                                )
                            ext_start = min(ext_candidates).strftime("%Y-%m-%d")
                            big = load_ohlcv(cd, ext_start, end_s)
                            if big is not None and not big.empty:
                                preload = big.copy()
                                pf, pcode = preload, cd
                                self.after(
                                    0,
                                    lambda pf=pf, pcode=pcode: self._stash_chart_daily_cache(
                                        pf, pcode
                                    ),
                                )
                except Exception:
                    preload = None

                res = run_backtest_detailed(
                    cfg,
                    ohlcv_preloaded_daily=preload if preload is not None else None,
                    defer_chart_render=True,
                    write_signal_debug_log=False,
                )
                self.after(
                    0,
                    lambda r=res, px=chart_px: self._finish_run(r, deferred_chart_px=px),
                )
            except Exception as e:
                import traceback
                traceback.print_exc()
                err_res = BacktestResult(
                    ok=False,
                    error=f"백테스트 중 오류가 발생했습니다: {e}",
                    summary_rows=[],
                    report_path=None,
                    log_lines=[f"Error: {e}"],
                )
                self.after(0, lambda r=err_res: self._finish_run(r))

        threading.Thread(target=work, daemon=True).start()

    def run_single_backtest(self, cfg: dict, *, use_slope_acceleration: bool) -> None:
        """
        단일 종목 백테스트 전용 진입점. `universe.screener.enabled` 를 강제로 끈다.
        """
        c = copy.deepcopy(cfg)
        c.setdefault("universe", {}).setdefault("screener", {})["enabled"] = False
        c.setdefault("strategy", {})["use_slope_acceleration"] = bool(use_slope_acceleration)
        self._run_backtest(c)

    def _run_single_with_code(
        self,
        code: str,
        *,
        listing_market_override: str | None = None,
        silent_try_build: bool = False,
        search_keyword_override: str | None = None,
        selected_display_name: str | None = None,
    ) -> None:
        """
        검색 결과 더블클릭·「백테스트 실행」·이력 라우팅 공통 진입점.
        v3.88: render_stock_chart 단일 렌더러로 위임.
        """
        cdf = str(code or "").strip().zfill(6)
        if not cdf or cdf == "000000":
            return
        nm = str(selected_display_name or "").strip()
        if nm:
            self._register_ticker_name(cdf, nm)
        if search_keyword_override is not None:
            self.var_keyword.set(str(search_keyword_override))
        self.render_stock_chart(
            cdf,
            period_nav=False,
            listing_market_override=listing_market_override,
            silent_try_build=silent_try_build,
        )

    def _run_single_from_run_button(self) -> None:
        """버튼: 검색 결과 선택 우선, 없으면 이력에서 선택된 한 종목만 실행."""
        try:
            cs = self.list_codes.curselection()
        except tk.TclError:
            cs = ()
        if cs:
            try:
                raw = self.list_codes.get(cs[0])
            except (tk.TclError, IndexError):
                raw = ""
            cd, nm = self._split_codes_list_line(str(raw))
            self._run_single_with_code(
                cd,
                search_keyword_override="",
                selected_display_name=nm,
            )
            return

        try:
            hs = self.list_history.curselection()
        except tk.TclError:
            hs = ()
        if hs:
            try:
                idx_h = int(hs[0])
            except (TypeError, ValueError, IndexError):
                idx_h = -1
            if 0 <= idx_h < len(self._history_deque):
                c_h, _n_h, _mh, k_h = history_row_normalize(
                    list(self._history_deque)[idx_h]
                )
                mv = normalize_krx_listing_market(k_h)
                self.var_market.set(mv if mv is not None else k_h)
                self._run_single_with_code(
                    c_h,
                    listing_market_override=k_h,
                    silent_try_build=True,
                    search_keyword_override="",
                )
            return

        messagebox.showwarning(
            "알림",
            "검색 결과 또는 최근 실행 이력에서 종목 한 줄을 선택하세요.",
        )
        self.set_status_message("목록에서 종목을 선택하면 기간 차트가 표시됩니다.")

    @staticmethod
    def _disp_name_from_res(res: BacktestResult) -> str:
        for row in res.summary_rows:
            if row[0] != "종목":
                continue
            cell = str(row[1])
            lp = cell.rfind("(")
            rp = cell.rfind(")")
            if lp >= 0 and rp > lp:
                return cell[:lp].strip()
        return ""

    @staticmethod
    def _split_codes_list_line(line: str) -> tuple[str, str]:
        """티커 | 종목명 | 시총 검색·이력 공통 줄에서 코드·종목명 추출."""
        s = line.strip()
        if not s:
            return "", ""
        code = parse_gui_list_row_code(s)
        if "|" in s:
            parts_p = [p.strip() for p in s.split("|")]
            name = parts_p[1] if len(parts_p) >= 2 else ""
        else:
            sp = s.split(None, 1)
            name = sp[1].strip() if len(sp) > 1 else ""
        return code, name

    def _listing_cap_snapshot(self, code: str, listing_market: str) -> float | None:
        """이력 표시 보강: 해당 종목 상장 시장 기준 원화 시총(None 가능). 메인 드롭다운 무관."""
        mk = normalize_krx_listing_market(listing_market) or "KOSPI"
        cdf = str(code or "").strip().zfill(6)
        try:
            m = fetch_listing_market_cap_krw_by_code(mk)
        except Exception:
            m = {}
        if not isinstance(m, dict):
            return None
        v = m.get(cdf)
        if v is None:
            return None
        try:
            x = float(v)
        except (TypeError, ValueError):
            return None
        return x if (x == x and x > 0) else None

    def _sync_history_listbox(self) -> None:
        """v4.10: 저장된 스냅샷만 표시 — 시장 콤보 변경 시 금액 재조회 없음."""
        self.list_history.delete(0, tk.END)
        for tup in self._history_deque:
            c, nm, mc_stored, lm = history_row_normalize(tup)
            if not c:
                continue
            rise_s = ""
            mc_s = ""
            amt_s = ""
            if len(tup) >= 7:
                rise_s = str(tup[4] or "").strip()
                mc_s = str(tup[5] or "").strip()
                amt_s = str(tup[6] or "").strip()
            if rise_s and mc_s and amt_s:
                line = format_gui_list_hist_pullback_snapshot(
                    c, nm, lm, rise_s, mc_s, amt_s
                )
            else:
                line = format_gui_list_hist(c, nm, lm, mc_stored)
            self.list_history.insert(tk.END, line)

    def _load_backtest_history_from_disk(self) -> None:
        path = BACKTEST_HISTORY_FILE
        if not os.path.isfile(path):
            return
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            return
        seq: list | None = None
        if isinstance(raw, dict) and isinstance(raw.get("items"), list):
            seq = raw["items"]
        elif isinstance(raw, list):
            seq = raw
        if not seq:
            return
        pairs: list[tuple] = []
        for el in seq:
            if not isinstance(el, (list, tuple)) or len(el) < 2:
                continue
            c_norm, nm, mc_val, lm = history_row_normalize(list(el[:4]))
            if not c_norm:
                continue
            rise_s = ""
            mc_s = ""
            amt_s = ""
            if len(el) >= 7:
                rise_s = str(el[4] or "").strip()
                mc_s = str(el[5] or "").strip()
                amt_s = str(el[6] or "").strip()
            pairs.append((c_norm, nm, mc_val, lm, rise_s, mc_s, amt_s))
            self._register_ticker_name(c_norm, nm)
            if len(pairs) >= BACKTEST_HISTORY_MAX:
                break
        if not pairs:
            return
        nd = deque(maxlen=BACKTEST_HISTORY_MAX)
        for tup in reversed(pairs):
            nd.appendleft(tup)
        self._history_deque = deque(nd, maxlen=BACKTEST_HISTORY_MAX)

    def _save_backtest_history_to_disk(self) -> None:
        path = BACKTEST_HISTORY_FILE
        out_dir = os.path.dirname(path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        items: list[list[object]] = []
        for tup in self._history_deque:
            c, n, mc, lm = history_row_normalize(tup)
            rise_s = str(tup[4] if len(tup) >= 5 else "" or "").strip()
            mc_s = str(tup[5] if len(tup) >= 6 else "" or "").strip()
            amt_s = str(tup[6] if len(tup) >= 7 else "" or "").strip()
            row: list[object] = [
                c,
                n,
                None if mc is None else float(mc),
                lm,
                rise_s,
                mc_s,
                amt_s,
            ]
            items.append(row)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {"version": 3, "items": items},
                f,
                ensure_ascii=False,
                indent=2,
            )

    def _on_user_close(self) -> None:
        self._stop_operation_timer()
        try:
            dump_last_gui_session(self)
        except OSError:
            pass
        try:
            self._save_backtest_history_to_disk()
        except OSError:
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass

    def _selected_universe_limit(self) -> int:
        """상단 Top 콤보(100/300/500) → 스캔 시총 상위 N종."""
        ssot_ul = default_pullback_scan_params().universe_limit
        try:
            raw = self.combo_universe.get()
        except (tk.TclError, AttributeError):
            raw = universe_limit_combo_value(ssot_ul)
        return normalize_universe_limit_choice(raw, default=ssot_ul)

    def _format_operation_elapsed(self, seconds: float) -> str:
        mins = int(seconds // 60)
        secs = seconds - mins * 60
        return f"({mins:02d}:{secs:04.1f})"

    def _start_operation_timer(self, btn, base_text: str) -> None:
        self._stop_operation_timer(reset_button=False)
        self._op_timer_btn = btn
        self._op_timer_base = str(base_text)
        self._op_timer_start = time.perf_counter()
        self._tick_operation_timer()

    def _tick_operation_timer(self) -> None:
        if self._op_timer_start is None or self._op_timer_btn is None:
            return
        elapsed = time.perf_counter() - self._op_timer_start
        label = f"{self._op_timer_base} 중... {self._format_operation_elapsed(elapsed)}"
        try:
            self._op_timer_btn.configure(text=label)
        except tk.TclError:
            return
        self._op_timer_after_id = self.after(50, self._tick_operation_timer)

    def _stop_operation_timer(self, *, reset_button: bool = True) -> None:
        aid = getattr(self, "_op_timer_after_id", None)
        if aid is not None:
            try:
                self.after_cancel(aid)
            except (tk.TclError, ValueError):
                pass
        self._op_timer_after_id = None
        self._op_timer_start = None
        if reset_button and self._op_timer_btn is not None:
            try:
                self._op_timer_btn.configure(text=self._op_timer_base)
            except tk.TclError:
                pass
        self._op_timer_btn = None
        self._op_timer_base = ""

    def _push_history(
        self,
        code: str,
        display_name: str,
        *,
        market_cap_krw: float | None = None,
        listing_market: str | None = None,
        rise_text: str = "",
        marcap_text: str = "",
        trade_amount_text: str = "",
        skip_if_exists: bool = False,
    ) -> None:
        """최근 실행 이력 · v4.8: 종목별 상장 시장 필드 포함. 같은 종목 재실행 시 맨 위."""
        cd = str(code).strip().zfill(6)
        if not cd or cd == "000000":
            return
        nm = (display_name or "").strip() or cd
        mc = market_cap_krw
        lm_norm = normalize_krx_listing_market(listing_market or self.var_market.get())
        lm = lm_norm if lm_norm is not None else "KOSPI"
        rest_parts: list[tuple] = []
        exists = False
        for tup in self._history_deque:
            c0, n0, m0, k0 = history_row_normalize(tup)
            if c0 == cd:
                exists = True
                continue
            rise_0 = str(tup[4] if len(tup) >= 5 else "" or "").strip()
            mc_0 = str(tup[5] if len(tup) >= 6 else "" or "").strip()
            amt_0 = str(tup[6] if len(tup) >= 7 else "" or "").strip()
            rest_parts.append((c0, n0, m0, k0, rise_0, mc_0, amt_0))
        if skip_if_exists and exists:
            return
        nd = deque(maxlen=BACKTEST_HISTORY_MAX)
        nd.appendleft(
            (
                cd,
                nm,
                mc,
                lm,
                str(rise_text or "").strip(),
                str(marcap_text or "").strip(),
                str(trade_amount_text or "").strip(),
            )
        )
        nd.extend(rest_parts[: BACKTEST_HISTORY_MAX - 1])
        self._history_deque = deque(nd, maxlen=BACKTEST_HISTORY_MAX)
        self._register_ticker_name(cd, nm)
        self._sync_history_listbox()

    def _on_history_delete(self) -> None:
        sel = self.list_history.curselection()
        if not sel:
            messagebox.showinfo("안내", "삭제할 이력 줄을 선택하세요.")
            return
        code, _ = self._split_codes_list_line(self.list_history.get(sel[0]))
        if not code:
            return
        self._history_deque = deque(
            (
                history_row_normalize(x)
                for x in self._history_deque
                if history_row_normalize(x)[0] != code
            ),
            maxlen=BACKTEST_HISTORY_MAX,
        )
        self._sync_history_listbox()

    def _on_search_list_dbl_click(self, _evt: tk.Event | None = None) -> None:
        sel = self.list_codes.curselection()
        if not sel:
            return
        try:
            raw = self.list_codes.get(sel[0])
        except (tk.TclError, IndexError):
            return
        parts = [p.strip() for p in str(raw).split("|")]
        if len(parts) < 5:
            return
        cd = parse_gui_list_row_code(parts[0])
        nm = parts[1] if len(parts) >= 2 else cd
        rise_s = parts[2] if len(parts) >= 3 else ""
        mc_s = parts[3].replace("시총", "", 1).strip() if len(parts) >= 4 else ""
        amt_s = parts[4].replace("대금", "", 1).strip() if len(parts) >= 5 else ""
        if not cd:
            return
        listing_mk = (
            self._scan_ticker_market.get(cd)
            or listing_market_from_gui_badge(parts[0])
            or normalize_krx_listing_market(self.var_market.get())
            or "KOSPI"
        )
        self._register_ticker_name(cd, nm)
        self._push_history(
            cd,
            nm,
            market_cap_krw=None,
            listing_market=listing_mk,
            rise_text=rise_s,
            marcap_text=mc_s,
            trade_amount_text=amt_s,
            skip_if_exists=True,
        )
        self.render_stock_chart(cd)
        self.set_status_message(f"차트 표시 · 이력 추가: {nm} ({cd})")

    def _on_history_list_dbl_click(self, _evt: tk.Event | None = None) -> None:
        sel = self.list_history.curselection()
        if not sel:
            return
        try:
            idx_h = int(sel[0])
        except (TypeError, ValueError, IndexError):
            return
        if not (0 <= idx_h < len(self._history_deque)):
            return
        cd, nm_hist, _mc, mk = history_row_normalize(list(self._history_deque)[idx_h])
        if not cd:
            return
        k_h = mk
        mv = normalize_krx_listing_market(mk)
        self.var_market.set(mv if mv is not None else k_h)
        self._register_ticker_name(cd, nm_hist)
        self.render_stock_chart(
            cd,
            listing_market_override=k_h,
            silent_try_build=True,
        )

    def _search_screen_universe_params(self) -> dict[str, object] | None:
        """검색용 스크리너 호출에 필요한 종료일·YAML 병합 top_n 등 (백테스트 실행과 동일 규격)."""
        try:
            end_d = self._date_end.get_date().strftime("%Y-%m-%d")
        except (ValueError, tk.TclError):
            self.set_status_message(
                "자동 스캔 검색: 종료일을 캘린더에서 선택하세요."
            )
            return None

        base = load_config()
        yaml_uni = base.get("universe") or {}
        yaml_scr = (
            yaml_uni.get("screener")
            if isinstance(yaml_uni.get("screener"), dict)
            else {}
        )
        scr = {**default_screener_config(), **yaml_scr}
        scr["volatility_metric"] = "atr14"

        lk = max(5, min(120, int(scr.get("lookback_trading_days", 20))))
        tn = max(1, min(200, int(scr.get("top_n", 100))))
        ds = default_screener_config()
        try:
            min_cap_krw = float(
                scr.get("min_market_cap_krw", ds["min_market_cap_krw"])
            )
        except (TypeError, ValueError):
            min_cap_krw = float(ds["min_market_cap_krw"])
        pair_hf = bool(
            scr.get("hard_ma_pair_trend_filter", ds["hard_ma_pair_trend_filter"])
        )
        try:
            pb_cap = float(
                scr.get("pullback_rank_cap_pct", ds["pullback_rank_cap_pct"])
            )
        except (TypeError, ValueError):
            pb_cap = float(ds["pullback_rank_cap_pct"])
        return {
            "end_date": end_d,
            "lookback": lk,
            "top_n": tn,
            "min_market_cap_krw": min_cap_krw,
            "hard_ma_pair_trend_filter": pair_hf,
            "pullback_rank_cap_pct": pb_cap,
        }

    def _on_cash_format_trace(self, *_args) -> None:
        """가상 원금 천단위 쉼표 실시간 마스킹."""
        if self._cash_format_guard:
            return
        raw = str(self.var_cash.get())
        digits = "".join(ch for ch in raw if ch.isdigit())
        if not digits:
            formatted = ""
        else:
            try:
                formatted = f"{int(digits):,}"
            except ValueError:
                return
        if formatted == raw:
            return
        self._cash_format_guard = True
        try:
            self.var_cash.set(formatted)
        finally:
            self._cash_format_guard = False

    def _parse_cash_int(self) -> int | None:
        try:
            v = int("".join(ch for ch in str(self.var_cash.get()) if ch.isdigit()))
        except ValueError:
            return None
        if v <= 0:
            return None
        return v

    def _sell_timing_minutes(self) -> int:
        label = str(self.combo_sell_timing.get()).strip()
        mapping = {
            "0분(시가)": 0,
            "5분 후": 5,
            "10분 후": 10,
            "30분 후": 30,
            "1시간 후": 60,
        }
        return int(mapping.get(label, 0))

    def _active_stock_label_name(self) -> str:
        text = str(self.lbl_selected_stock.cget("text") or "")
        if ":" in text:
            tail = text.split(":", 1)[1].strip()
            if "|" in tail:
                return tail.split("|", 1)[1].strip()
            parts = tail.split(None, 1)
            if len(parts) >= 2:
                return parts[1].strip()
            return tail
        return ""

    def _set_backtest_report_text(self, text: str) -> None:
        try:
            self.txt_backtest_report.configure(state="normal")
            self.txt_backtest_report.delete("1.0", "end")
            self.txt_backtest_report.insert("1.0", text)
            self.txt_backtest_report.configure(state="disabled")
        except (tk.TclError, AttributeError):
            pass

    def _begin_backtest_loading_state(self) -> None:
        self._start_operation_timer(self.btn_pullback_backtest, "🚀 백테스트")
        try:
            self.btn_pullback_backtest.configure(state="disabled")
        except (tk.TclError, AttributeError):
            pass
        try:
            self.btn_backtest_cancel.configure(state="normal")
        except (tk.TclError, AttributeError):
            pass

    def _end_backtest_loading_state(self) -> None:
        self._stop_operation_timer()
        try:
            self.btn_pullback_backtest.configure(state="normal")
        except (tk.TclError, AttributeError):
            pass
        try:
            self.btn_backtest_cancel.configure(state="disabled")
        except (tk.TclError, AttributeError):
            pass

    def _on_backtest_cancel(self) -> None:
        self._backtest_cancel_event.set()
        self._stop_operation_timer()
        self._end_backtest_loading_state()
        self.set_status_message("백테스트 중단 요청…")

    def _on_pullback_backtest(self) -> None:
        """v3.40: 차트 활성 종목 단일 눌림목 타임라인 백테스트."""
        if self._backtest_busy or self._busy:
            self.set_status_message("다른 작업이 진행 중입니다.")
            return
        code = self.current_code
        if not code or code == "000000":
            messagebox.showinfo(
                "백테스트",
                "먼저 스캔 결과·이력에서 종목을 선택해 차트를 표시하세요.",
            )
            return
        cash = self._parse_cash_int()
        if cash is None:
            messagebox.showerror("오류", "가상 원금은 0보다 큰 숫자여야 합니다.")
            return
        params = self._parse_leader_pullback_scan_params()
        if params is None:
            messagebox.showerror(
                "오류",
                "세력 개입 배수·눌림 거래량 비율은 0보다 큰 숫자여야 합니다.",
            )
            return
        burst, shrink, use_momentum_filter = params
        try:
            start_s = self._date_start.get_date().strftime("%Y-%m-%d")
            end_s = self._date_end.get_date().strftime("%Y-%m-%d")
        except (ValueError, tk.TclError):
            messagebox.showerror("오류", "시작일·종료일을 확인하세요.")
            return
        sell_min = self._sell_timing_minutes()
        name = self._active_stock_label_name() or code

        self._backtest_busy = True
        self._backtest_cancel_event.clear()
        self._begin_backtest_loading_state()
        self.set_status_message(f"눌림목 백테스트 계산 중… ({code})")

        def work() -> None:
            try:
                if self._backtest_cancel_event.is_set():
                    self.after(0, self._finalize_pullback_backtest_cancelled)
                    return
                df = None
                cache = getattr(self, "_chart_ohlcv_cache_df", None)
                cache_code = str(getattr(self, "_chart_ohlcv_cache_code", "") or "")
                if cache is not None and not cache.empty and cache_code == code:
                    df = cache.copy()
                if df is None:
                    _prime_krx_env_from_dotenv()
                    df = load_ohlcv_with_dynamic_buffer(
                        code, start_s, end_s, user_slice=False
                    )
                if df is None or df.empty:
                    raise RuntimeError("선택 기간에 일봉 데이터가 없습니다.")
                df = df.sort_index()
                sim = slice_ohlcv_user_period(df, start_s, end_s)
                if sim is None or sim.empty:
                    raise RuntimeError("기간 필터 후 데이터가 없습니다.")
                if len(sim) < 1:
                    raise RuntimeError(
                        f"봉 수가 부족합니다(장기 대세 MA60·MA120 검증에 최소 {PULLBACK_MIN_OHLCV_BARS}봉 필요)."
                    )
                if self._backtest_cancel_event.is_set():
                    self.after(0, self._finalize_pullback_backtest_cancelled)
                    return
                res = run_pullback_timeline_backtest(
                    df,
                    initial_cash=float(cash),
                    volume_burst_multiple=burst,
                    vol_shrink_limit=shrink,
                    use_momentum_filter=use_momentum_filter,
                    sell_timing_minutes=sell_min,
                    code=code,
                    name=name,
                    period_start=start_s,
                    period_end=end_s,
                )
                self.after(0, lambda r=res: self._finalize_pullback_backtest(r))
            except Exception as ex:
                self.after(
                    0,
                    lambda m=str(ex): self._finalize_pullback_backtest_error(m),
                )

        threading.Thread(target=work, daemon=True).start()

    def _finalize_pullback_backtest_cancelled(self) -> None:
        self._backtest_busy = False
        self._end_backtest_loading_state()
        self.set_status_message("백테스트가 중단되었습니다.")

    def _finalize_pullback_backtest_error(self, msg: str) -> None:
        self._backtest_busy = False
        self._end_backtest_loading_state()
        self._set_backtest_report_text(f"오류: {msg}")
        self.set_status_message(f"백테스트 실패: {msg}")

    def _finalize_pullback_backtest(self, res) -> None:
        self._backtest_busy = False
        self._end_backtest_loading_state()
        if not res.ok:
            self._set_backtest_report_text(res.error or "알 수 없는 오류")
            self.set_status_message("백테스트 오류")
            return
        self._set_backtest_report_text(res.report_text)
        self.set_status_message(
            f"백테스트 완료 · 진입 {res.n_entries}회 · 최종 {res.final_equity:,.0f}원"
        )

    def _begin_search_loading_state(self) -> None:
        """검색 워커 시작 시 버튼 비활성화·마우스 대기 커서(창 단위)."""
        self._start_operation_timer(self.btn_run, "🔵 스캔")
        try:
            self.btn_run.configure(state="disabled")
        except (tk.TclError, AttributeError):
            pass
        try:
            self.btn_scan_cancel.configure(state="normal")
        except (tk.TclError, AttributeError):
            pass
        try:
            self.winfo_toplevel().configure(cursor="wait")
        except tk.TclError:
            pass
        try:
            self.update_idletasks()
        except tk.TclError:
            pass

    def _end_search_loading_state(self) -> None:
        """검색 종료 후 버튼·커서 원복."""
        self._stop_operation_timer()
        try:
            self.btn_run.configure(state="normal")
        except (tk.TclError, AttributeError):
            pass
        try:
            self.btn_scan_cancel.configure(state="disabled")
        except (tk.TclError, AttributeError):
            pass
        try:
            self.winfo_toplevel().configure(cursor="")
        except tk.TclError:
            pass

    def _finalize_search_pick_list(self, picks: list[object]) -> None:
        """검색 스레드 완료 후 메인 스레드에서 리스트 및 배치 픽 갱신."""
        self._busy = False
        self._end_search_loading_state()
        self.update_gui_with_screener_results(picks, announce=True)

    def _parse_leader_pullback_scan_params(self) -> tuple[float, float, bool] | None:
        """세력 개입 배수·눌림 거래량 비율 파싱. 실패 시 None."""
        try:
            burst = float(str(self.var_volume_burst_multiple.get()).strip().replace(",", ""))
            shrink = float(str(self.var_vol_shrink_limit.get()).strip().replace(",", ""))
        except ValueError:
            return None
        if burst <= 0 or shrink <= 0:
            return None
        return burst, shrink, bool(self.var_use_momentum_filter.get())

    def _run_leader_pullback_scan(
        self, market: str, end_date: str
    ) -> tuple[
        list[tuple[str, str, float, str, str, str]],
        dict[str, object],
    ]:
        """
        v3.30 주도주 눌림목 스캔.
        반환: (리스트 행, code→ScanEvidenceSnapshot) — 조회 시점 스냅샷.
        """
        from src.engine.exporter import ScanEvidenceSnapshot

        empty: tuple[list, dict] = ([], {})
        params = self._parse_leader_pullback_scan_params()
        if params is None:
            self.after(
                0,
                lambda: messagebox.showerror(
                    "스캔 파라미터",
                    "세력 개입 배수·눌림 거래량 비율은 0보다 큰 숫자여야 합니다.",
                ),
            )
            return empty
        volume_burst_multiple, vol_shrink_limit, use_momentum_filter = params
        from src.v3_scan_config import resolve_effective_pullback_scan_params

        scan_ssot = resolve_effective_pullback_scan_params()
        min_liq_cap = float(scan_ssot.min_liquidity_market_cap_krw)
        min_liq_trd = float(scan_ssot.min_liquidity_trade_amount_krw)

        _prime_krx_env_from_dotenv()
        scan_market = str(market or "KOSPI").strip().upper()
        if scan_market not in ("KOSPI", "KOSDAQ", "ALL"):
            scan_market = "KOSPI"
        parity_limit = self._selected_universe_limit()
        name_map: dict[str, str] = {}
        for mk in pullback_bulk_markets_for_scan(scan_market, parity_limit):
            try:
                name_map.update(fetch_filtered_universe(mk, ""))
            except Exception:
                pass

        # 1) pykrx 22영업일 벌크 + v3.30 눌림목 벡터 필터
        pass_burst = 0
        pass_price = 0
        pass_volume = 0
        pass_kim_long = 0
        pass_kim_short = 0
        pass_all = 0
        pass_liquidity = 0
        total_universe = 0
        total_loaded = 0
        diag_burst = ""
        diag_shrink = ""
        prev_1 = ""
        prev_2 = ""
        requested_scan_date = str(end_date).strip()[:10]
        ainfo_pre = resolve_overnight_scan_anchor(requested_scan_date)
        bulk_end_date = requested_scan_date
        bulk = scan_leader_pullback_candidates_bulk(
            bulk_end_date,
            market=scan_market,
            cancel_event=self._scan_cancel_event,
            universe_limit=parity_limit,
            volume_burst_multiple=volume_burst_multiple,
            vol_shrink_limit=vol_shrink_limit,
            use_momentum_filter=use_momentum_filter,
            min_liquidity_market_cap_krw=min_liq_cap,
            min_liquidity_trade_amount_krw=min_liq_trd,
        )

        qualifiers: list[
            tuple[str, str, float, float | None, float | None, str]
        ] = []
        evidence_by_code: dict[str, ScanEvidenceSnapshot] = {}
        bulk_evidence_raw: dict = {}
        diag_policy = ""
        effective_anchor = ainfo_pre.anchor_date.strftime("%Y-%m-%d")
        st: dict = {}
        if bool(bulk.get("ok")):
            rows = bulk.get("rows") or []
            st = bulk.get("stats") if isinstance(bulk.get("stats"), dict) else {}
            total_loaded = int((st or {}).get("total_loaded", len(rows)))
            total_universe = int((st or {}).get("total_universe", total_loaded))
            pass_liquidity = int(
                (st or {}).get("pass_liquidity", total_loaded)
            )
            pass_burst = int((st or {}).get("pass_burst", 0))
            pass_price = int((st or {}).get("pass_price", 0))
            pass_volume = int((st or {}).get("pass_volume", 0))
            pass_kim_long = int((st or {}).get("pass_kim_long", 0))
            pass_kim_short = int((st or {}).get("pass_kim_short", 0))
            pass_all = int((st or {}).get("pass_all", len(rows)))
            diag_burst = str((st or {}).get("volume_burst_multiple", volume_burst_multiple))
            diag_shrink = str((st or {}).get("vol_shrink_limit", vol_shrink_limit))
            prev_1 = str((st or {}).get("prev_1", ""))
            prev_2 = str((st or {}).get("prev_2", ""))
            eff_raw = str((st or {}).get("effective_anchor_date") or "").strip()
            if eff_raw:
                effective_anchor = eff_raw[:10]
            pol = (st or {}).get("anchor_policy_reason")
            if pol is not None:
                diag_policy = str(pol)
            bulk_evidence_raw = bulk.get("evidence") if isinstance(bulk.get("evidence"), dict) else {}
            for row in rows:
                if len(row) >= 5:
                    code, rise_pct, mar_krw, trd_krw, listing_mk = row[:5]
                else:
                    code, rise_pct, mar_krw, trd_krw = row[:4]
                    listing_mk = scan_market if scan_market in ("KOSPI", "KOSDAQ") else "KOSPI"
                c6 = str(code).zfill(6)
                name = str(name_map.get(c6, "")).strip() or c6
                qualifiers.append(
                    (
                        c6,
                        name,
                        float(rise_pct),
                        mar_krw,
                        trd_krw,
                        str(listing_mk or "KOSPI").strip().upper(),
                    )
                )
        else:
            reason = str(bulk.get("reason", "")).strip() or "unknown"
            if reason == "cancelled":
                return empty
            raise RuntimeError(_leader_pullback_bulk_fail_message(reason))

        qualifiers.sort(key=lambda z: (-float(z[2]), str(z[0]).zfill(6)))

        out: list[tuple[str, str, float, str, str, str]] = []
        for code, name, rise_pct, mar_seed, trd_seed, listing_mk in qualifiers:
            mar_krw: float | None = mar_seed
            trd_krw: float | None = trd_seed
            if trd_krw is not None and (
                not math.isfinite(float(trd_krw)) or float(trd_krw) <= 0
            ):
                trd_krw = None
            mc_s = _format_marcap_display_krw(mar_krw)
            amt_s = _format_round_eok_krw(trd_krw)
            lm = (
                normalize_krx_listing_market(listing_mk)
                or str(listing_mk or "KOSPI").strip().upper()
            )
            out.append((code, name, rise_pct, mc_s, amt_s, lm))

        for code, name, *_rest in qualifiers:
            c6 = str(code).zfill(6)
            raw_ev = bulk_evidence_raw.get(c6)
            if isinstance(raw_ev, ScanEvidenceSnapshot):
                evidence_by_code[c6] = raw_ev.with_display_name(name)

        if not total_universe:
            total_universe = total_loaded

        debug_lines = [
            "=====================================================",
            "⚙️ [DEBUG] v4.25 주도주 눌림목 스캐너 (OHLC Evidence Snapshot)",
            "=====================================================",
            f" - Requested End Date : {requested_scan_date}",
            f" - Effective OHLCV Anchor (t0) : {effective_anchor}",
            f" - Prev_1 Date : {prev_1 or '-'} | Prev_2 Date : {prev_2 or '-'}",
            f" - Anchor policy : {diag_policy or '-'}",
            f" - Scan Market : {scan_market}",
            f" - Top : {universe_limit_display_label(parity_limit)}",
            f" - Markets pipeline : {st.get('markets_pipeline', scan_market)}",
            f" - 세력 개입 배수 : {diag_burst or volume_burst_multiple}",
            f" - 눌림 거래량 비율 : {diag_shrink or vol_shrink_limit}",
            f" - 유동성 시총 하한 : {_format_round_eok_krw(min_liq_cap)} 이상",
            f" - 유동성 거래대금 하한 : {_format_round_eok_krw(min_liq_trd)} 이상",
            "-----------------------------------------------------",
            " [Applied Rules — 타임라인 격리]",
            "  0) v4.40 시총·거래대금 유동성 + 거래정지(Volume=0) 제거 (Pass 0)",
            "  1) t-1 vol > mean(t-2..t-21 vol) × burst_mult & t-1 양봉(종가>시가)",
            "  2) v4.15 MA20 OR 중심선 + v4.25 이격도5≤105%·20≤110%",
            "  3) t vol <= t-1 vol × shrink_limit",
            "  4) v3.95 종가>MA60·MA120 AND MA60>MA120 (Perfect Trend Lock)",
            (
                "  5) v3.50 MA5 >= MA10 (단기 모멘텀)"
                if use_momentum_filter
                else "  5) v3.50 MA5 >= MA10 (단기 모멘텀) [사용자 설정으로 스킵]"
            ),
            "-----------------------------------------------------",
            " [Pipeline Filtering Pass Count]",
            (
                f"  ▶ Total Top Tickers Loaded : {total_universe}개"
                + (
                    " (KOSPI+KOSDAQ 통합)"
                    if str((st or {}).get("markets_pipeline", "")).upper()
                    == "KOSPI+KOSDAQ"
                    else ""
                )
            ),
            f"  ▶ Pass 0 (+ 유동성) : {pass_liquidity}개 (스캔 유니버스 {total_loaded}개)",
            f"  ▶ Pass 1 (세력+전일양봉) : {pass_burst}개",
            f"  ▶ Pass 2 (+ MA20 OR 중심선) : {pass_price}개",
            f"  ▶ Pass 3 (+ 거래량 급감) : {pass_volume}개",
            f"  ▶ Pass 4 (+ Perfect Trend) : {pass_kim_long}개",
            (
                f"  ▶ Pass 5 (+ MA5≥MA10) : {pass_kim_short}개"
                if use_momentum_filter
                else "  ▶ Pass 5 (+ MA5≥MA10) : [사용자 설정에 의해 스킵됨]"
            ),
            f"  ▶ 최종 : {pass_all}개",
            "=====================================================",
        ]
        debug_text = "\n".join(debug_lines)
        print(debug_text)
        try:
            os.makedirs("output", exist_ok=True)
            with open("output/v330_leader_pullback_scan_debug.txt", "w", encoding="utf-8") as f:
                f.write(debug_text + "\n")
        except OSError:
            pass
        return out, evidence_by_code

    def _on_search(self) -> None:
        """v3.30 주도주 눌림목 스캔: 코드|종목명|당일 상승률|시총|거래대금."""
        if self._busy or self._backtest_busy:
            self.set_status_message(
                "이미 다른 작업이 진행 중입니다. 잠시만 기다려주세요."
            )
            return
        market = self.var_market.get().strip().upper() or "KOSPI"
        if market not in ("KOSPI", "KOSDAQ", "ALL"):
            market = "KOSPI"
        try:
            end_date = self._date_end.get_date().strftime("%Y-%m-%d")
        except (ValueError, tk.TclError):
            self.set_status_message("종료일을 확인하세요.")
            return
        self._busy = True
        self._scan_cancel_event.clear()
        self._begin_search_loading_state()
        self.set_status_message("주도주 눌림목 스캔 실행 중…")
        self._scan_thread = LeaderPullbackScanWorker(
            owner=self,
            market=market,
            end_date=end_date,
        )
        self._scan_thread.start()

    def _on_scan_cancel(self) -> None:
        """v3.11: 진행 중 스캔을 사용자가 즉시 중단."""
        if self._scan_thread is not None:
            self._scan_thread.cancel()
        else:
            self._scan_cancel_event.set()
        self._busy = False
        self._end_search_loading_state()
        self.set_status_message("스캔이 중단되었습니다. (사용자 취소)")

    def _finalize_v31_scan_cancelled(self) -> None:
        self._busy = False
        self._end_search_loading_state()
        self.set_status_message("스캔이 중단되었습니다. (사용자 취소)")

    def _render_scan_result_listbox(
        self, rows: list[tuple[str, str, float, str, str, str]]
    ) -> None:
        """v4.10: 스캔 결과 스냅샷만 렌더 — 시장 콤보 변경과 무관."""
        self.list_codes.delete(0, tk.END)
        self._candidates = []
        self._scan_ticker_market = {}
        for code, name, rise_pct, mc_s, amt_s, listing_mk in rows:
            lm = normalize_krx_listing_market(listing_mk) or str(listing_mk or "KOSPI")
            line = format_gui_list_leader_pullback(
                code, name, lm, rise_pct, mc_s, amt_s
            )
            self.list_codes.insert(tk.END, line)
            self._candidates.append((code, name, None))
            self._scan_ticker_market[str(code).zfill(6)] = lm
            self._register_ticker_name(code, name)

    def _finalize_v31_scan(
        self,
        rows: list[tuple[str, str, float, str, str, str]],
        evidence_by_code: dict[str, object] | None = None,
    ) -> None:
        self._busy = False
        self._end_search_loading_state()
        self._scan_result_snapshot = list(rows)
        self._scan_evidence_by_code = dict(evidence_by_code or {})
        if rows and self._scan_evidence_by_code:
            first = str(rows[0][0]).zfill(6)
            snap0 = self._scan_evidence_by_code.get(first)
            if snap0 is not None and hasattr(snap0, "anchor_date"):
                self._scan_evidence_anchor = str(getattr(snap0, "anchor_date", ""))[:10]
        self._render_scan_result_listbox(rows)
        if rows:
            try:
                self.list_codes.selection_clear(0, tk.END)
            except tk.TclError:
                pass
            self.set_status_message(
                f"🔥 총 {len(rows)}개 주도주 눌림목 포착 "
                f"(근거 스냅샷 {len(self._scan_evidence_by_code)}건)"
            )
        else:
            self.set_status_message("조건에 맞는 주도주 눌림목이 없습니다.")

    def _on_export_scan_evidence(self) -> None:
        """v4.20: 검출 전 종목 정량 근거 Excel — 스캔 시점 스냅샷 일괄 저장."""
        from src.engine.exporter import ScanEvidenceSnapshot, export_scan_evidence_snapshots

        if not self._scan_evidence_by_code:
            messagebox.showinfo(
                "근거 내보내기",
                "먼저 스캔을 실행해 검출 종목을 확보하세요.",
            )
            return

        targets: list[str] = []
        for row in self._scan_result_snapshot:
            cd = str(row[0]).strip().zfill(6)
            if cd and cd in self._scan_evidence_by_code:
                targets.append(cd)
        if not targets:
            targets = list(self._scan_evidence_by_code.keys())

        snaps: list[ScanEvidenceSnapshot] = []
        for cd in targets:
            ev = self._scan_evidence_by_code.get(cd)
            if isinstance(ev, ScanEvidenceSnapshot):
                snaps.append(ev)

        if not snaps:
            messagebox.showwarning("근거 내보내기", "내보낼 스냅샷 데이터가 없습니다.")
            return

        try:
            paths = export_scan_evidence_snapshots(snaps)
        except ModuleNotFoundError as ex:
            if ex.name == "openpyxl":
                messagebox.showerror(
                    "근거 내보내기 실패",
                    "openpyxl 패키지가 없습니다.\n\n"
                    "프로젝트 venv에서 실행:\n"
                    "  pip install openpyxl\n\n"
                    "또는:\n"
                    "  pip install -r requirements.txt",
                )
            else:
                messagebox.showerror("근거 내보내기 실패", str(ex))
            return
        except Exception as ex:
            messagebox.showerror("근거 내보내기 실패", str(ex))
            return

        if len(paths) == 1:
            msg = f"저장 완료:\n{paths[0]}"
        else:
            msg = f"{len(paths)}건 저장 (outputs/evidences/)\n예: {paths[0]}"
        self.set_status_message(f"근거 스냅샷 {len(paths)}건 Excel 저장")
        messagebox.showinfo("근거 내보내기", msg)

    def _exec_search_worker(
        self,
        *,
        keyword: str,
        market: str,
        screener_params: dict[str, object],
        strategy_st: dict[str, object],
        pf_mcap_top100: bool,
        pf_buy_rules: bool,
        pf_kim_candle: bool,
    ) -> None:
        """스크린 I/O 및 `execute_pipelined_screening` 단일 진입점."""
        picks: list[object] = []

        try:
            p = screener_params
            picks = execute_pipelined_screening(
                market=market,
                keyword=keyword,
                end_date=str(p["end_date"]),
                strategy_st=strategy_st,
                stage_mcap_top100=bool(pf_mcap_top100),
                stage_buy_rules=bool(pf_buy_rules),
                stage_kim_candle=bool(pf_kim_candle),
                top_display_n=int(p["top_n"]),
                min_market_cap_krw=float(p["min_market_cap_krw"]),
                progress_cb=None,
            )

        except Exception as ex:
            self.after(
                0,
                lambda m=str(ex): self._finalize_search_failure(m),
            )
            return

        plist_copy = list(picks)
        self.after(
            0,
            lambda pl=plist_copy: self._finalize_search_pick_list(pl),
        )

    def _finalize_search_failure(self, msg: str) -> None:
        """검색 워커 예외 처리(메인 스레드 전용)."""
        self._busy = False
        self._end_search_loading_state()
        self.set_status_message(f"스캔 실패: {msg.splitlines()[0]}")
        messagebox.showerror("스캔 실패", msg)

    def _on_run(self):
        self._on_search()

    def _finish_run(
        self,
        res,
        *,
        deferred_chart_px: tuple[int, int] | None = None,
    ) -> None:
        self._busy = False
        self.btn_run.configure(state="normal", text="🔵 스캔")
        try:
            self.btn_rules_refresh.configure(state="normal")
        except (tk.TclError, AttributeError):
            pass
        if not res.ok:
            self._last_chart_path = None
            self._last_chart_bytes = None
            self._img_flat_ref = None
            self._chart_canvas_image_item = None
            self._chart_flat_show_message(res.error or "오류")
            self._set_summary(res.error or "알 수 없는 오류")
            self.lbl_status.configure(text="오류로 종료됨.")
            messagebox.showerror("백테스트 실패", res.error or "알 수 없는 오류")
            return

        lur = str(getattr(self, "_pending_run_listing_market", "KOSPI") or "KOSPI").strip().upper()
        if lur not in ("KOSPI", "KOSDAQ", "ETF"):
            lur = "KOSPI"
        self._last_run_listing_market = lur

        self._set_summary("")

        code_hist = str(getattr(self, "_pending_run_code", "") or "").zfill(6)
        disp_name = ""
        for row in res.summary_rows:
            if row[0] == "종목":
                cell = str(row[1])
                lp = cell.rfind("(")
                rp = cell.rfind(")")
                if lp >= 0 and rp > lp:
                    disp_name = cell[:lp].strip()
                break
        if code_hist and code_hist != "000000":
            self._last_active_stock_code = code_hist
            try:
                shown_name = disp_name or self._resolve_stock_name(code_hist)
                self._sync_selected_stock_label(code_hist, shown_name)
            except (tk.TclError, AttributeError):
                pass
        mk_done = self._last_run_listing_market
        mc_hist = self._listing_cap_snapshot(code_hist, mk_done)
        self._push_history(
            code_hist,
            disp_name,
            market_cap_krw=mc_hist,
            listing_market=mk_done,
        )

        self._update_period_label()

        pending_chart = (
            getattr(res, "chart_render_pending", False) and res.replay_chart is not None
        )
        if pending_chart:
            self.lbl_status.configure(
                text="백테스트 완료 — 차트 PNG 생성 중…",
            )
            self._chart_materialize_ticket += 1
            ticket = self._chart_materialize_ticket
            self._start_chart_loading_spinner()

            replay_copy = dict(res.replay_chart)
            px = deferred_chart_px
            render_kw = self._chart_render_kw_from_px(px)
            tk_snap = ticket

            def _chart_paint_task() -> None:
                try:
                    png_blob, skipped = materialize_backtest_chart_png_bytes(
                        replay_copy,
                        chart_render_px=px,
                        write_signal_debug_log=False,
                    )
                    self.after(
                        0,
                        lambda b=png_blob,
                        sk=skipped,
                        tt=tk_snap,
                        rp=replay_copy,
                        rk=render_kw: self._materialized_chart_dispatch_bytes(
                            tt, b, sk, rp, rk
                        ),
                    )
                except Exception as chart_ex:
                    em = str(chart_ex)
                    self.after(
                        0,
                        lambda tt=tk_snap, mm=em: self._chart_materialize_failed_for_ticket(
                            tt, mm
                        ),
                    )

            threading.Thread(target=_chart_paint_task, daemon=True).start()
        else:
            self.update_idletasks()
            self.after_idle(lambda p=res.report_path: self._defer_chart_image_paint(p))
            self._finalize_run_status_stripes(res.trade_markers_skipped)

    def _materialized_chart_dispatch(
        self,
        ticket: int,
        report_path: str | None,
        trade_markers_skipped: int,
    ) -> None:
        """가장 마지막에 요청한 materialize 결과만 차트를 갱신(연타 안전)."""
        if ticket != self._chart_materialize_ticket:
            return
        self._stop_chart_loading_spinner()
        self._apply_materialized_chart(report_path, trade_markers_skipped)

    def _materialized_chart_dispatch_bytes(
        self,
        ticket: int,
        png_bytes: bytes | None,
        trade_markers_skipped: int,
        replay: dict | None = None,
        render_kw: dict | None = None,
    ) -> None:
        """연기 PNG를 디스크 없이 메모리 버퍼만으로 표시(v3.1 output/ I/O 차단)."""
        if ticket != self._chart_materialize_ticket:
            return
        self._stop_chart_loading_spinner()
        if replay is not None and render_kw is not None:
            try:
                self._chart_install_canvas_state(
                    self._chart_state_from_replay(replay, render_kw=render_kw)
                )
            except Exception:
                self._chart_canvas_state = None
        self.update_idletasks()
        self.after_idle(
            lambda b=png_bytes: self._update_chart_image_from_png_bytes(b)
        )
        self._finalize_run_status_stripes(trade_markers_skipped)

    def _chart_materialize_failed_for_ticket(self, ticket: int, msg: str) -> None:
        if ticket != self._chart_materialize_ticket:
            return
        self._stop_chart_loading_spinner()
        self._chart_materialize_failed(msg)

    def _apply_materialized_chart(
        self, report_path: str | None, trade_markers_skipped: int
    ) -> None:
        """v4.10 연기 차트 후처리(메인 스레드); 스피너 정지는 dispatch 에서 처리."""
        self.update_idletasks()
        self.after_idle(lambda p=report_path: self._defer_chart_image_paint(p))
        self._finalize_run_status_stripes(trade_markers_skipped)

    def _chart_materialize_failed(self, msg: str) -> None:
        self.set_status_message(f"차트 생성 실패: {msg}")
        self._chart_flat_show_message(f"차트 생성 실패: {msg}")

    def _finalize_run_status_stripes(self, trade_markers_skipped: int) -> None:
        """완료/타점 경고 라벨 + (필요 시) 한 번 모달."""
        if trade_markers_skipped > 0:
            self.lbl_status.configure(
                text=(
                    "완료 · 매칭 실패 오류 발생 — 차트 타점 "
                    f"{trade_markers_skipped}건 누락"
                )
            )
            messagebox.showwarning(
                "차트 타점 누락",
                f"{trade_markers_skipped}건의 매매가 차트 날짜 인덱스와 일치하지 않아 표시하지 못했습니다.\n"
                "요약 로그의 [CRITICAL] 항목과 터미널 메시지를 확인하고 데이터를 점검하세요.",
            )
        else:
            self.lbl_status.configure(text="완료")


def main():
    app = BacktestGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
