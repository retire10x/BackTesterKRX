"""
데스크톱 GUI (CustomTkinter).
차트: `output/backtest_report.png` → **tk.Canvas**/`PhotoImage`. CTk 라벨·CTkImage는 둥근 마스크로 비트맵이 잘리므로 차트 패널에 사용하지 않음.
YAML·설정 dict·툴팁: `gui_helpers`. 엔진: `src.metrics.run_backtest_detailed`.
본문·툴팁 폰트는 `gui_helpers.gui_body_font()`(13pt)로 통일, `set_widget_scaling`/`set_window_scaling` 1.0 고정.
메인 레이아웃은 grid weight 기반 반응형; 우측 패널은 `grid_propagate(False)`. **v4.10** 백테스트는 `defer_chart_render` 후 `materialize_backtest_chart_png` 로 PNG 생성. **v4.11** 차트 캔버스는 같은 image item 에 `PhotoImage` 를 `itemconfig` 로 원자 교체하여 선삭제 깜빡임 방지하며, PNG 생성 대기 동안 차트 줄 Braille 로딩 표시만 갱신한다. **v4.14** 검색은 `execute_pipelined_screening`(시총 Top·매수규칙·김직선 1봉 순차 AND) 단일 파이프라인이다. **v4.14_Fix** 파이프라인 시총 하한·표시 행 상한 보정·골든 OFF 바이패스는 `stock_screener` 에서 처리한다. **v4.15** 검색 2단계는 골든 OFF 여도 진입 필터를 종봉 AND 적용. 매매 패널 키는 `merge_live_trade_panel_into_strategy`/`extract_live_strategy_config` 로 백테·스크린 공통 반영.
"""
from __future__ import annotations

import copy
import json
import os
import threading
from collections import deque
import tkinter as tk
from datetime import date, timedelta
from tkinter import messagebox

import customtkinter as ctk
import pandas as pd
from pandas.tseries.offsets import BDay

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")
ctk.set_widget_scaling(1.0)
ctk.set_window_scaling(1.0)

from PIL import Image, ImageTk
from tkcalendar import DateEntry

from src.data_loader import (
    default_backtest_period_range,
    fetch_listing_market_cap_krw_by_code,
    load_config,
    load_ohlcv,
    normalize_krx_listing_market,
    ohlcv_warm_start_date,
)
from src.gui_helpers import (
    HoverTooltip,
    extract_live_strategy_config,
    apply_yaml_to_widgets,
    date_entry_theme_kw,
    format_gui_list_hist,
    format_gui_list_pipeline,
    format_gui_list_triple,
    gui_body_font,
    gui_summary_five_lines,
    history_row_normalize,
    parse_gui_list_row_code,
    trading_rules_static_text,
    try_build_config,
    GUI_FONT_FAMILY,
    GUI_FONT_SIZE,
)
from src.backtest_constants import TREND_MA_PERIODS
from src.metrics import (
    BacktestResult,
    materialize_backtest_chart_png,
    normalize_interval,
    run_backtest_detailed,
)
from src.stock_screener import (
    EntryEventTrackPick,
    KimLineOneBarPick,
    PipelineScreenerPick,
    RankedUniversePick,
    ScreenerEntry,
    default_screener_config,
    execute_pipelined_screening,
)

# ==========================================
# 스크리너 결과 → 리스트박스 표시용 정규화 (방어적 정렬·슬라이싱)
# ==========================================


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
        tier = 0.0 if item.pattern_label == "고가돌파" else 1.0
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
# 좌측 최소 가로폭. 우측·차트는 창 크기에 맞춰 가변; 차트 타깃은 `CHART_IMG_*` 및 `chart_overlay_host` 실측.
FIXED_LEFT_W = 278

# 날짜(DateEntry) 열 목표 픽셀 폭 — 가상 원금 열과 균형
DATE_GRID_MIN_W = 112

# 차트 패널: 영업일 기준(±7, ±1) 기간 평행 이동 시 라벨·자동 재실행과 연계
# 차트 이미지 위 좌·우 클릭 영역 (px, place)
CHART_NAV_STRIP_W = 50
DATE_CLAMP_MIN = date(1990, 1, 1)

# 차트 contain 타깃: 프레임 실측에서 여유를 크게 차감(저해상도·우측/하단축 미세 클립 방지)
CHART_IMG_INNER_MARGIN_X = 20
CHART_IMG_INNER_MARGIN_Y = 40
CHART_IMG_MIN_FW = 300
CHART_IMG_MIN_FH = 200

# 최근 실행 종목 이력: 메모리·디스크 모두 최대 이 개수 (FIFO)
BACKTEST_HISTORY_MAX = 30
BACKTEST_HISTORY_FILE = os.path.join("output", "backtest_history.json")

# 기본 메인 창 크기 및 최소 크기(노트북·외부 모니터 공통). 실제 배치는 화면에 맞게 클램프 후 중앙 정렬.
MAIN_WINDOW_INITIAL_W = 1400
MAIN_WINDOW_INITIAL_H = 850
MAIN_WINDOW_MIN_W = 1280
MAIN_WINDOW_MIN_H = 720


class BacktestGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        gui_body_font()  # CTkFont — Tk 루트 존재 후 캐시(모듈 import 시 생성 불가)

        self.title("BackTesterKRX v4.15")

        self._apply_initial_window_geometry()

        self._candidates: list[tuple[str, str, float | None]] = []
        self._last_batch_picks: list[object] = []
        self._busy = False
        # 마지막으로 성공한 단일/배치 차트 종목 코드 — 차트 기간 패닝 시 YAML·리스트 무관하게 유지
        self._last_active_stock_code = ""
        # v4.8: 패닝·Refresh 시 GUI 시장 드롭다운과 달라도 성공 실행 당시 상장 시장으로 try_build 고정
        self._last_run_listing_market: str | None = None
        self._chart_ohlcv_cache_df = None  # 타입: pd.DataFrame | None
        self._chart_ohlcv_cache_code = ""
        self._img_flat_ref: ImageTk.PhotoImage | None = None
        self._last_chart_path: str | None = None
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

        self.var_interval = ctk.StringVar(value="daily")
        self.var_ma_period = ctk.StringVar(value="20")
        self._trend_vars: dict[int, ctk.BooleanVar] = {
            p: ctk.BooleanVar(value=(p in (20, 120))) for p in TREND_MA_PERIODS
        }
        self.var_show_candle = ctk.BooleanVar(value=True)
        self.var_show_volume = ctk.BooleanVar(value=True)
        self.var_show_return_overlay = ctk.BooleanVar(value=False)
        self.var_buy_fee_pct = ctk.StringVar(value="0.015")
        self.var_sell_fee_pct = ctk.StringVar(value="0.18")
        self.var_cash = ctk.StringVar(value="5000000")
        self.var_pf_mcap_top100 = ctk.BooleanVar(value=False)
        self.var_pf_buy_rules = ctk.BooleanVar(value=False)
        self.var_pf_kim_candle = ctk.BooleanVar(value=False)
        # (코드, 표시명, 시총 스냅샷 또는 None, 상장 시장 KOSPI/KOSDAQ/ETF)
        self._history_deque: deque[tuple[str, str, float | None, str]] = deque(
            maxlen=BACKTEST_HISTORY_MAX
        )

        self.grid_columnconfigure(0, weight=0, minsize=FIXED_LEFT_W)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)

        left = ctk.CTkFrame(
            self, corner_radius=10, width=FIXED_LEFT_W
        )
        left.grid(
            row=0, column=0, sticky="nw", padx=(8, 4), pady=(8, 8)
        )  # 좌측: 고정 최소폭만 유지하고 세로는 내용 기준 (저해상도 대응)
        left.grid_propagate(True)

        row_search = ctk.CTkFrame(left, fg_color="transparent")
        row_search.pack(fill="x", padx=14, pady=(12, 6))
        row_search.grid_columnconfigure(0, weight=0)
        row_search.grid_columnconfigure(1, weight=1)

        sf_market = ctk.CTkFrame(row_search, fg_color="transparent")
        sf_market.grid(row=0, column=0, sticky="nw", padx=(0, 6))
        ctk.CTkLabel(sf_market, text="시장", font=gui_body_font()).pack(anchor="w", pady=(0, 2))
        self.var_market = ctk.StringVar(value="KOSPI")
        ctk.CTkOptionMenu(
            sf_market,
            values=["KOSPI", "KOSDAQ", "ETF"],
            variable=self.var_market,
            width=86,
            font=gui_body_font(),
        ).pack(anchor="w")

        sf_kw = ctk.CTkFrame(row_search, fg_color="transparent")
        sf_kw.grid(row=0, column=1, sticky="nsew")
        ctk.CTkLabel(sf_kw, text="종목", font=gui_body_font()).pack(anchor="w", pady=(0, 2))
        self.var_keyword = ctk.StringVar(value="")
        ctk.CTkEntry(
            sf_kw, textvariable=self.var_keyword, height=28, font=gui_body_font()
        ).pack(fill="x", expand=True)

        self.btn_search = ctk.CTkButton(
            row_search,
            text="검색",
            height=28,
            font=gui_body_font(),
            command=self._on_search,
        )
        self.btn_search.grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(5, 10)
        )

        row_mode = ctk.CTkFrame(left, fg_color="transparent")
        row_mode.pack(fill="x", padx=14, pady=(4, 6))
        ctk.CTkLabel(
            row_mode,
            text="퀀트 필터 파이프라인 (순차 AND)",
            font=gui_body_font(),
        ).pack(anchor="w", pady=(0, 2))

        def _mk_pipeline_cb(text: str, var: ctk.BooleanVar) -> ctk.CTkCheckBox:
            cb = ctk.CTkCheckBox(
                row_mode,
                text=text,
                variable=var,
                checkbox_width=14,
                checkbox_height=14,
                font=gui_body_font(),
            )
            cb.pack(anchor="w", pady=1)
            return cb

        _mk_pipeline_cb(
            "1단계: 시장별 시총 상위 Top 100 필터",
            self.var_pf_mcap_top100,
        )
        _mk_pipeline_cb(
            "2단계: 매수 규칙 — 우측 설정(골든·돌파·시간 버퍼 등)과 연동",
            self.var_pf_buy_rules,
        )
        _mk_pipeline_cb(
            "3단계: 김직선 1봉 캔들 (고가돌파 / 중심선지지)",
            self.var_pf_kim_candle,
        )
        HoverTooltip(
            row_mode,
            "위에서 아래로 체크된 단계만 순서대로 적용됩니다. "
            "2·3단계는 종료일까지의 일봉이 필요합니다.",
        )

        list_frame = ctk.CTkFrame(left, fg_color="transparent")
        list_frame.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        self.list_codes = tk.Listbox(
            list_frame,
            height=7,
            font=(GUI_FONT_FAMILY, GUI_FONT_SIZE),
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

        row_dt = ctk.CTkFrame(left, fg_color="transparent")
        row_dt.pack(fill="x", padx=14, pady=(0, 6))
        row_dt.grid_columnconfigure(0, weight=0, minsize=DATE_GRID_MIN_W)
        row_dt.grid_columnconfigure(1, weight=0, minsize=DATE_GRID_MIN_W)
        row_dt.grid_columnconfigure(2, weight=1)
        d0 = ctk.CTkFrame(row_dt, fg_color="transparent")
        d0.grid(row=0, column=0, sticky="nw", padx=(0, 6))
        d1 = ctk.CTkFrame(row_dt, fg_color="transparent")
        d1.grid(row=0, column=1, sticky="nw", padx=(0, 6))
        fx_cash = ctk.CTkFrame(row_dt, fg_color="transparent")
        fx_cash.grid(row=0, column=2, sticky="nsew")

        ctk.CTkLabel(d0, text="시작일", font=gui_body_font()).pack(
            anchor="w", pady=(0, 2)
        )
        self._date_start = DateEntry(
            d0,
            width=10,
            date_pattern="yyyy-mm-dd",
            font=(GUI_FONT_FAMILY, GUI_FONT_SIZE - 1),
            **date_entry_theme_kw(),
        )
        _ds, _de = default_backtest_period_range()
        self._date_start.set_date(_ds)
        self._date_start.pack(anchor="w")
        ctk.CTkLabel(d1, text="종료일", font=gui_body_font()).pack(
            anchor="w", pady=(0, 2)
        )
        self._date_end = DateEntry(
            d1,
            width=10,
            date_pattern="yyyy-mm-dd",
            font=(GUI_FONT_FAMILY, GUI_FONT_SIZE - 1),
            **date_entry_theme_kw(),
        )
        self._date_end.set_date(_de)
        self._date_end.pack(anchor="w")

        ctk.CTkLabel(fx_cash, text="가상 원금", font=gui_body_font()).pack(anchor="w")
        ctk.CTkEntry(
            fx_cash,
            textvariable=self.var_cash,
            height=28,
            font=gui_body_font(),
        ).pack(fill="x", pady=(2, 0), expand=True)

        row_fee = ctk.CTkFrame(left, fg_color="transparent")
        row_fee.pack(fill="x", padx=14, pady=(0, 6))
        row_fee.grid_columnconfigure(0, weight=1)
        row_fee.grid_columnconfigure(1, weight=1)
        fbuy = ctk.CTkFrame(row_fee, fg_color="transparent")
        fbuy.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        fsell = ctk.CTkFrame(row_fee, fg_color="transparent")
        fsell.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ctk.CTkLabel(fbuy, text="매수 수수료(%)", font=gui_body_font()).pack(anchor="w")
        ctk.CTkEntry(
            fbuy,
            textvariable=self.var_buy_fee_pct,
            height=28,
            font=gui_body_font(),
        ).pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(fsell, text="매도 수수료(세금 포함 %)", font=gui_body_font()).pack(
            anchor="w"
        )
        ctk.CTkEntry(
            fsell,
            textvariable=self.var_sell_fee_pct,
            height=28,
            font=gui_body_font(),
        ).pack(fill="x", pady=(2, 0))

        hist_block = ctk.CTkFrame(left, fg_color="transparent")
        hist_block.pack(fill="x", padx=14, pady=(4, 6))

        hist_toolbar = ctk.CTkFrame(hist_block, fg_color="transparent")
        hist_toolbar.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(
            hist_toolbar,
            text=f"최근 백테스트 이력 (FIFO {BACKTEST_HISTORY_MAX})",
            font=gui_body_font(),
        ).pack(side="left", anchor="w")
        self.btn_history_del = ctk.CTkButton(
            hist_toolbar,
            text="삭제",
            width=44,
            height=28,
            font=ctk.CTkFont(family=GUI_FONT_FAMILY, size=GUI_FONT_SIZE - 1),
            command=self._on_history_delete,
        )
        self.btn_history_del.pack(side="right", anchor="ne")

        hist_list_frame = ctk.CTkFrame(hist_block, fg_color="transparent")
        hist_list_frame.pack(fill="x")
        # 검색 결과 `list_codes` 와 동일 스펙(행 수·폰트·선택 모드·스크롤)
        self.list_history = tk.Listbox(
            hist_list_frame,
            height=7,
            font=(GUI_FONT_FAMILY, GUI_FONT_SIZE),
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

        row_run = ctk.CTkFrame(left, fg_color="transparent")
        row_run.pack(fill="x", padx=14, pady=(8, 8))
        self.btn_run = ctk.CTkButton(
            row_run,
            text="백테스트 실행",
            height=40,
            font=gui_body_font(),
            command=self._on_run,
        )
        self.btn_run.pack(fill="x")

        self.text_summary = ctk.CTkTextbox(
            left,
            height=128,
            font=gui_body_font(),
            wrap="word",
        )
        self.text_summary.pack(fill="both", expand=False, padx=14, pady=(0, 14))
        self.text_summary.configure(state="disabled")

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

        rules_panel = ctk.CTkFrame(
            right,
            corner_radius=8,
            border_width=1,
            border_color=("gray65", "gray45"),
            fg_color=("gray92", "gray18"),
        )
        rules_panel.grid(row=0, column=0, sticky="new", padx=8, pady=(4, 4))

        rules_head = ctk.CTkFrame(rules_panel, fg_color="transparent")
        rules_head.pack(fill="x", padx=8, pady=(8, 4))

        rules_head_row0 = ctk.CTkFrame(rules_head, fg_color="transparent")
        rules_head_row0.pack(fill="x")

        titles_left = ctk.CTkFrame(rules_head_row0, fg_color="transparent")
        titles_left.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            titles_left,
            text="매매 규칙 · v4.6",
            font=gui_body_font(),
            anchor="w",
        ).pack(anchor="w")

        self.btn_rules_refresh = ctk.CTkButton(
            rules_head_row0,
            text="Refresh",
            width=82,
            height=28,
            font=gui_body_font(),
            command=self._on_rules_refresh_chart,
        )
        self.btn_rules_refresh.pack(side="right", anchor="n", padx=(8, 0))
        HoverTooltip(
            self.btn_rules_refresh,
            "현재 활성 종목·조회 기간에 지금 패널의 매수·매도 조건을 반영해 차트(PNG)를 다시 계산합니다.",
        )

        def _bump_slope(delta: float) -> None:
            try:
                v = float(str(self.var_slope_threshold.get()).replace(",", "").strip())
            except ValueError:
                v = 0.01
            v = max(0.0001, min(1.0, v + delta))
            s = f"{v:.4f}".rstrip("0").rstrip(".")
            self.var_slope_threshold.set(s or "0")

        # 좌우 격자 레이아웃을 위한 컨테이너 프레임 생성
        grid_container = ctk.CTkFrame(rules_panel, fg_color="transparent")
        grid_container.pack(fill="x", padx=10, pady=(0, 10))
        grid_container.grid_columnconfigure(0, weight=1, uniform="rules_col")
        grid_container.grid_columnconfigure(1, weight=1, uniform="rules_col")

        # 매수 카드(좌) · 매도 카드(우) — 동일 행 2열
        buy_frame = ctk.CTkFrame(
            grid_container,
            corner_radius=6,
            border_width=1,
            border_color=("gray75", "gray30"),
            fg_color=("gray95", "gray20"),
        )
        buy_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=4)

        sell_frame = ctk.CTkFrame(
            grid_container,
            corner_radius=6,
            border_width=1,
            border_color=("gray75", "gray30"),
            fg_color=("gray95", "gray20"),
        )
        sell_frame.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=4)

        tt_golden = (
            " 골든크로스 신호\n"
            "위의 '추세' 조건이 충족된 상승장 안에서, 단기 주가가 이동평균선을 위로 돌파할 때 최종 매수 진입을 시도합니다."
        )
        tt_dead = (
            "종가가 매매 기준 이동평균선을 하향 돌파(데드크로스)할 때 기본 매도 신호 후보 발생"
        )
        tt_trend = (
            " 장기 추세 필터 (기본 활성화)\n"
            "지난 6개월간의 평균 주가(120일선)가 하루 0.01%(연 약 2.5%) 이상 완만하게 우상향하는지 검사합니다. "
            "하락장 분별을 위한 필수 안전장치입니다."
        )
        tt_breakout = (
            "거래량 > 직전 5봉 평균×1.5 또는 종가 > MA20×1.02 (골든 후보 봉 AND)"
        )
        tt_timebuf = (
            "골든 후보 봉(i) 즉시 매수 안 함 — i+1, i+2 종가까지 MA20 위 안착 후 다음 봉 시가 진입 시에도 활성 필터 AND"
        )
        tt_slope_accel = (
            "최근 5봉 MA20 상에 OLS 기울기가 0보다 큰 경우에만 매수 후보 통과(단기 우상향 유지·눌림·초입 필터)"
        )
        tt_trailing_short = (
            "매수 이후 최고가 대비 설정 % 하락 시, 데드크로스 매도 신호보다 앞선 시점에서 다음 봉 시가 조기 청산"
        )

        # 🟢 매수 조건 내용 배치
        lbl_buy_title = ctk.CTkLabel(
            buy_frame,
            text="🟢 매수 진입 조건 (AND 결합)",
            font=ctk.CTkFont(family=GUI_FONT_FAMILY, size=GUI_FONT_SIZE, weight="bold"),
            anchor="w",
        )
        lbl_buy_title.pack(anchor="w", padx=10, pady=(8, 6))

        buy_flow = ctk.CTkFrame(buy_frame, fg_color="transparent")
        buy_flow.pack(fill="x", padx=10, pady=(0, 10))
        buy_row0 = ctk.CTkFrame(buy_flow, fg_color="transparent")
        buy_row0.pack(fill="x", anchor="w")
        buy_row1 = ctk.CTkFrame(buy_flow, fg_color="transparent")
        buy_row1.pack(fill="x", anchor="w", pady=(6, 0))

        self.cb_trend = ctk.CTkCheckBox(
            buy_row0,
            text="추세",
            variable=self.var_filter_trend,
            font=gui_body_font(),
            checkbox_width=18,
            checkbox_height=18,
        )
        self.cb_trend.pack(side="left", padx=(0, 2))
        HoverTooltip(self.cb_trend, tt_trend)

        self.slope_spin = ctk.CTkFrame(buy_row0, fg_color="transparent")
        self.slope_spin.pack(side="left", padx=(0, 8))
        self.btn_slope_up = ctk.CTkButton(
            self.slope_spin,
            text="▴",
            width=20,
            height=20,
            font=gui_body_font(),
            corner_radius=3,
            command=lambda: _bump_slope(0.01),
        )
        self.btn_slope_up.pack(side="left", padx=(0, 1))
        self.entry_slope_threshold = ctk.CTkEntry(
            self.slope_spin,
            width=48,
            height=22,
            font=gui_body_font(),
            textvariable=self.var_slope_threshold,
        )
        self.entry_slope_threshold.pack(side="left")
        self.btn_slope_down = ctk.CTkButton(
            self.slope_spin,
            text="▾",
            width=20,
            height=20,
            font=gui_body_font(),
            corner_radius=3,
            command=lambda: _bump_slope(-0.01),
        )
        self.btn_slope_down.pack(side="left", padx=(1, 0))
        HoverTooltip(self.entry_slope_threshold, tt_trend)

        ctk.CTkLabel(buy_row0, text="|", font=gui_body_font(), text_color="gray50").pack(
            side="left", padx=(0, 8)
        )

        self.cb_golden = ctk.CTkCheckBox(
            buy_row0,
            text="골든 매수",
            variable=self.var_golden_buy,
            font=gui_body_font(),
            checkbox_width=18,
            checkbox_height=18,
        )
        self.cb_golden.pack(side="left", padx=(0, 8))
        HoverTooltip(self.cb_golden, tt_golden)

        self.cb_breakout = ctk.CTkCheckBox(
            buy_row1,
            text="돌파 강도",
            variable=self.var_filter_breakout,
            font=gui_body_font(),
            checkbox_width=18,
            checkbox_height=18,
        )
        self.cb_breakout.pack(side="left", padx=(0, 8))
        HoverTooltip(self.cb_breakout, tt_breakout)

        ctk.CTkLabel(buy_row1, text="|", font=gui_body_font(), text_color="gray50").pack(
            side="left", padx=(0, 8)
        )

        self.cb_timebuf = ctk.CTkCheckBox(
            buy_row1,
            text="시간 버퍼",
            variable=self.var_filter_timebuf,
            font=gui_body_font(),
            checkbox_width=18,
            checkbox_height=18,
        )
        self.cb_timebuf.pack(side="left")
        HoverTooltip(self.cb_timebuf, tt_timebuf)

        ctk.CTkLabel(buy_row1, text="|", font=gui_body_font(), text_color="gray50").pack(
            side="left", padx=(8, 8)
        )

        self.cb_slope_accel = ctk.CTkCheckBox(
            buy_row1,
            text="곡선 가속도",
            variable=self.check_slope_accel_var,
            font=gui_body_font(),
            checkbox_width=18,
            checkbox_height=18,
        )
        self.cb_slope_accel.pack(side="left")
        HoverTooltip(self.cb_slope_accel, tt_slope_accel)

        # 🔴 매도 조건 내용 배치
        lbl_sell_title = ctk.CTkLabel(
            sell_frame,
            text="🔴 매도 청산 조건 (OR 결합)",
            font=ctk.CTkFont(family=GUI_FONT_FAMILY, size=GUI_FONT_SIZE, weight="bold"),
            anchor="w",
        )
        lbl_sell_title.pack(anchor="w", padx=10, pady=(8, 6))

        sell_row0 = ctk.CTkFrame(sell_frame, fg_color="transparent")
        sell_row0.pack(fill="x", anchor="w", padx=10, pady=(0, 0))
        sell_row1 = ctk.CTkFrame(sell_frame, fg_color="transparent")
        sell_row1.pack(fill="x", anchor="w", padx=10, pady=(6, 10))

        cb_dead = ctk.CTkCheckBox(
            sell_row0,
            text="데드 매도",
            variable=self.var_dead_sell,
            font=gui_body_font(),
            checkbox_width=18,
            checkbox_height=18,
        )
        cb_dead.pack(side="left", padx=(0, 8))
        HoverTooltip(cb_dead, tt_dead)

        ctk.CTkLabel(sell_row0, text="|", font=gui_body_font(), text_color="gray50").pack(
            side="left", padx=(0, 8)
        )

        cb_trailing = ctk.CTkCheckBox(
            sell_row0,
            text="가변 낙폭",
            variable=self.var_trailing_stop,
            font=gui_body_font(),
            checkbox_width=18,
            checkbox_height=18,
        )
        cb_trailing.pack(side="left", padx=(0, 6))
        HoverTooltip(cb_trailing, tt_trailing_short)

        ctk.CTkLabel(sell_row1, text="기준", font=gui_body_font()).pack(
            side="left", padx=(0, 2)
        )
        self.entry_trailing_ref = ctk.CTkEntry(
            sell_row1,
            width=36,
            height=22,
            font=gui_body_font(),
            textvariable=self.var_trailing_reference_pct,
        )
        self.entry_trailing_ref.pack(side="left")
        ctk.CTkLabel(sell_row1, text="%", font=gui_body_font()).pack(
            side="left", padx=(2, 6)
        )

        ctk.CTkLabel(sell_row1, text="미달", font=gui_body_font()).pack(side="left")
        self.entry_trailing_below = ctk.CTkEntry(
            sell_row1,
            width=32,
            height=22,
            font=gui_body_font(),
            textvariable=self.var_trailing_drop_below_pct,
        )
        self.entry_trailing_below.pack(side="left", padx=(2, 2))
        ctk.CTkLabel(sell_row1, text="%", font=gui_body_font()).pack(
            side="left", padx=(0, 6)
        )

        ctk.CTkLabel(sell_row1, text="돌파", font=gui_body_font()).pack(side="left")
        self.entry_trailing_above = ctk.CTkEntry(
            sell_row1,
            width=32,
            height=22,
            font=gui_body_font(),
            textvariable=self.var_trailing_drop_above_pct,
        )
        self.entry_trailing_above.pack(side="left", padx=(2, 2))
        ctk.CTkLabel(sell_row1, text="%", font=gui_body_font()).pack(side="left")

        def _trailing_tooltip_detail() -> str:
            try:
                g = float(
                    str(self.var_trailing_reference_pct.get()).replace(",", "").strip()
                )
                b = float(
                    str(self.var_trailing_drop_below_pct.get())
                    .replace(",", "")
                    .strip()
                )
                a = float(
                    str(self.var_trailing_drop_above_pct.get())
                    .replace(",", "")
                    .strip()
                )
                g_s, b_s, a_s = f"{g:g}", f"{b:g}", f"{a:g}"
            except ValueError:
                g_s, b_s, a_s = "?", "?", "?"
            return (
                f"{tt_trailing_short} (피크 기준 수익률 {g_s}% 미만·이상 분기별로 고점 대비 {b_s}% / {a_s}% 하락)"
            )

        HoverTooltip(self.entry_trailing_ref, _trailing_tooltip_detail)
        HoverTooltip(self.entry_trailing_below, _trailing_tooltip_detail)
        HoverTooltip(self.entry_trailing_above, _trailing_tooltip_detail)

        # 차트 컨트롤 패널 (매매 규칙과 차트 사이)
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
            font=(GUI_FONT_FAMILY, 14),
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
            font=(GUI_FONT_FAMILY, 14),
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
            font=(GUI_FONT_FAMILY, 14),
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
            font=(GUI_FONT_FAMILY, 14),
            cursor="hand2",
            command=lambda: self._on_chart_pan_bdays(7),
        )
        self.btn_fast_forward.pack(side="left", padx=6)
        HoverTooltip(self.btn_fast_forward, "7영업일 후로 이동 (+7d)")

        # 현재 기간 표시 라벨 추가 (플레이 버튼 우측)
        self.lbl_current_period = ctk.CTkLabel(
            inner_btns,
            text="",
            font=ctk.CTkFont(family=GUI_FONT_FAMILY, size=GUI_FONT_SIZE, weight="bold"),
            text_color=("gray25", "gray75"),
        )
        self.lbl_current_period.pack(side="left", padx=(18, 6))

        self.lbl_chart_loading = ctk.CTkLabel(
            inner_btns,
            text="",
            width=18,
            font=ctk.CTkFont(family=GUI_FONT_FAMILY, size=GUI_FONT_SIZE - 2),
            text_color=("gray40", "gray60"),
        )
        self.lbl_chart_loading.pack(side="left", padx=(4, 0))

        self.cb_return_overlay = ctk.CTkCheckBox(
            btn_container,
            text="수익률",
            variable=self.var_show_return_overlay,
            font=gui_body_font(),
            width=28,
            checkbox_width=14,
            checkbox_height=14,
        )
        self.cb_return_overlay.pack(side="right", padx=(10, 2), pady=0)
        HoverTooltip(self.cb_return_overlay, "주가 차트 배경에 누적 수익률(%) 음영 오버레이")

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

        self.chart_overlay_host.bind("<Configure>", self._on_chart_frame_configure)
        self.chart_frame.bind("<Configure>", self._on_chart_frame_configure)

        apply_yaml_to_widgets(self)
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

        self.var_show_return_overlay.trace_add(
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

        self._set_summary(
            "「백테스트 실행」 후 이곳에 성과 요약(5줄)이 표시됩니다."
        )
        self._update_period_label()

        # 매수 필터 인터락 등록 (YAML 반영값 유지 — 예전처럼 추세를 True로 강제 덮어쓰지 않음)
        self.var_filter_trend.trace_add("write", self._sync_buy_filters_interlock)
        self._sync_buy_filters_interlock()

        self.var_market.trace_add(
            "write",
            lambda *_: self.after_idle(self._sync_history_listbox),
        )

        self._load_backtest_history_from_disk()
        self._sync_history_listbox()
        self._chart_flat_show_message("백테스트 실행 후 차트가 표시됩니다.")
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
        """PNG 없을 때·오류 메시지 — CTkImage 없이 순수 tk.Canvas 텍스트."""
        self._img_flat_ref = None
        self._chart_canvas_image_item = None
        c = getattr(self, "_chart_flat_canvas", None)
        if c is None:
            return
        try:
            c.delete("all")
            c.configure(bg=self._chart_canvas_bg_plain())
            self.chart_overlay_host.update_idletasks()
            c.update_idletasks()
            w = max(160, int(c.winfo_width()))
            h = max(100, int(c.winfo_height()))
        except tk.TclError:
            return
        fill = "#d0d0d0" if ctk.get_appearance_mode() == "Dark" else "#333333"
        try:
            c.create_text(
                w // 2,
                h // 2,
                text=str(message),
                justify=tk.CENTER,
                font=(GUI_FONT_FAMILY, GUI_FONT_SIZE),
                fill=fill,
                width=max(120, w - 48),
            )
        except tk.TclError:
            pass

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
        if not self._last_chart_path:
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
        if self._last_chart_path:
            self._update_chart_image(self._last_chart_path)

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

    def _update_chart_image(self, image_path: str | None) -> None:
        """엔진 PNG 표시 — tk.Canvas. v4.11: 같은 image item 에 itemconfig 스왑(선 delete 금지)."""
        if not image_path or not os.path.isfile(image_path):
            self._last_chart_path = None
            self._chart_canvas_image_item = None
            self._img_flat_ref = None
            self._chart_flat_show_message("그래프 파일을 찾을 수 없습니다.")
            return

        try:
            try:
                self.update_idletasks()
                self.chart_frame.update_idletasks()
                self.chart_overlay_host.update_idletasks()
            except tk.TclError:
                pass

            fw, fh = self._chart_overlay_host_inner_pixel_size()
            if fw <= 0 or fh <= 0:
                return

            with Image.open(image_path) as pil_img:
                rgb = pil_img.convert("RGB")
                resized = rgb.resize((fw, fh), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(resized)
                cvs_f = self._chart_flat_canvas

                swapped = False
                item_existing = getattr(self, "_chart_canvas_image_item", None)
                if item_existing is not None:
                    try:
                        cvs_f.itemconfigure(item_existing, image=photo)
                        swapped = True
                    except tk.TclError:
                        self._chart_canvas_image_item = None

                try:
                    cvs_f.configure(bg=self._chart_canvas_bg_plain())
                    if not swapped:
                        try:
                            cvs_f.delete("all")
                        except tk.TclError:
                            pass
                        self._chart_canvas_image_item = cvs_f.create_image(
                            0, 0, anchor=tk.NW, image=photo, tags=("chart_main",)
                        )
                except tk.TclError:
                    return

                self._img_flat_ref = photo
                self._last_chart_path = image_path
        except Exception as e:
            self._last_chart_path = None
            self._chart_canvas_image_item = None
            self._img_flat_ref = None
            self._chart_flat_show_message(f"이미지 로드 실패: {e}")

    def _set_summary(self, text: str):
        self.text_summary.configure(state="normal")
        self.text_summary.delete("1.0", "end")
        self.text_summary.insert("1.0", text)
        self.text_summary.configure(state="disabled")

    def _sync_buy_filters_interlock(self, *_args: object) -> None:
        """'추세' 필터 체크박스 상태에 따라 우측 매수 필터 및 OLS 임계값 스핀을 비활성화(인터락)."""
        trend_active = bool(self.var_filter_trend.get())
        target_state = "normal" if trend_active else "disabled"

        self.cb_golden.configure(state=target_state)
        self.cb_breakout.configure(state=target_state)
        self.cb_timebuf.configure(state=target_state)
        self.btn_slope_up.configure(state=target_state)
        self.btn_slope_down.configure(state=target_state)
        self.entry_slope_threshold.configure(state=target_state)

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

        packed: list[tuple[object, str, str, str, float, float | None]] = []
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

            packed.append((item, line, code, name, float(sc), mc))

        packed.sort(key=lambda z: (-z[4], z[2]))
        total_raw = len(packed)
        truncated = packed
        self._last_batch_picks = [r[0] for r in truncated]
        self._candidates = [(r[2], r[3], r[5]) for r in truncated]

        for _it, ln, *_rest in truncated:
            self.list_codes.insert(tk.END, ln)
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

    def _on_rules_refresh_chart(self) -> None:
        """우측 매매 규칙 변경 후, 현재 조회 중인 종목·기간으로 차트만 재생성한다."""
        if self._busy:
            self.set_status_message("다른 작업이 진행 중입니다. 완료 후 다시 시도하세요.")
            return
        code = self.current_code
        code = code.strip().zfill(6) if code else ""
        if not code or code == "000000":
            messagebox.showinfo(
                "차트 새로고침",
                "먼저 백테스트를 실행해 활성 종목이 있거나, 차트 조회 상태가 필요합니다.",
            )
            self.set_status_message(
                "활성 종목이 없습니다. 검색 결과·이력에서 선택 후 백테스트를 실행하세요."
            )
            return

        cfg = try_build_config(
            self,
            silent=True,
            selected_code_override=code,
            period_nav=True,
        )
        if cfg is None:
            self.set_status_message(
                "설정을 만들 수 없습니다. 기간·수수료·가상 원금 입력을 확인하세요."
            )
            return

        self.set_status_message(
            "매수·매도 조건 변경을 반영해 현재 종목 차트를 다시 계산합니다…"
        )
        self.run_single_backtest(
            cfg,
            use_slope_acceleration=bool(self.check_slope_accel_var.get()),
        )

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
        nav_ov = nav if nav else None
        cfg = try_build_config(
            self,
            silent=True,
            selected_code_override=nav_ov,
            period_nav=True,
        )
        if cfg is None:
            self.lbl_status.configure(
                text="기간 이동: 먼저 한 종목 백테스트를 완료하거나 검색 후 차트가 열린 뒤 패닝하세요.",
            )
            return
        self.run_single_backtest(
            cfg,
            use_slope_acceleration=bool(self.check_slope_accel_var.get()),
        )

    def _on_chart_pan_bdays(self, delta_bdays: int) -> None:
        """차트 좌·우 오버레이: 영업일 기준으로 기간 이동 후 자동 재실행."""
        self._shift_period_trading_days(delta_bdays)
        self._schedule_auto_run_after_shift()

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
    ) -> None:
        """
        검색 결과 더블클릭·「백테스트 실행」·이력 라우팅 공통 진입점.
        v4.8: 이력 실행 시 상장 시장을 인자로 넘겨 목록 검증·시총표를 GUI 드롭다운과 분리한다.
        """
        cdf = str(code or "").strip().zfill(6)
        if not cdf or cdf == "000000":
            return
        cfg = try_build_config(
            self,
            silent=silent_try_build,
            selected_code_override=cdf,
            market_override=listing_market_override,
            search_keyword_override=search_keyword_override,
        )
        if cfg is None:
            return
        self.run_single_backtest(
            cfg,
            use_slope_acceleration=bool(self.check_slope_accel_var.get()),
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
            cd, _nm = self._split_codes_list_line(str(raw))
            self._run_single_with_code(
                cd,
                search_keyword_override="",
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
        self.set_status_message("목록에서 종목을 선택한 뒤 백테스트를 실행하세요.")

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
        self.list_history.delete(0, tk.END)
        for tup in self._history_deque:
            c, nm, mc_stored, lm = history_row_normalize(tup)
            if not c:
                continue
            mc_disp = mc_stored
            if mc_disp is None:
                mc_disp = self._listing_cap_snapshot(c, lm)
            line = format_gui_list_hist(c, nm, lm, mc_disp)
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
        pairs: list[tuple[str, str, float | None, str]] = []
        for el in seq:
            if not isinstance(el, (list, tuple)) or len(el) < 2:
                continue
            c_norm, nm, mc_val, lm = history_row_normalize(list(el[:4]))
            if not c_norm:
                continue
            pairs.append((c_norm, nm, mc_val, lm))
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
            row: list[object] = [
                c,
                n,
                None if mc is None else float(mc),
                lm,
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
        try:
            self._save_backtest_history_to_disk()
        except OSError:
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass

    def _push_history(
        self,
        code: str,
        display_name: str,
        *,
        market_cap_krw: float | None = None,
        listing_market: str | None = None,
    ) -> None:
        """최근 실행 이력 · v4.8: 종목별 상장 시장 필드 포함. 같은 종목 재실행 시 맨 위."""
        cd = str(code).strip().zfill(6)
        if not cd or cd == "000000":
            return
        nm = (display_name or "").strip() or cd
        mc = market_cap_krw
        lm_norm = normalize_krx_listing_market(listing_market or self.var_market.get())
        lm = lm_norm if lm_norm is not None else "KOSPI"
        rest_parts: list[tuple[str, str, float | None, str]] = []
        for tup in self._history_deque:
            c0, n0, m0, k0 = history_row_normalize(tup)
            if c0 == cd:
                continue
            rest_parts.append((c0, n0, m0, k0))
        nd = deque(maxlen=BACKTEST_HISTORY_MAX)
        nd.appendleft((cd, nm, mc, lm))
        nd.extend(rest_parts[: BACKTEST_HISTORY_MAX - 1])
        self._history_deque = deque(nd, maxlen=BACKTEST_HISTORY_MAX)
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
        cd, _nm = self._split_codes_list_line(str(raw))
        self._run_single_with_code(cd, search_keyword_override="")

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
        cd, _nm_hist, _mc, mk = history_row_normalize(list(self._history_deque)[idx_h])
        if not cd:
            return
        # UI 동기화: 이력에 기록된 상장 시장·종목명을 메인 폼에 반영
        k_h = mk
        mv = normalize_krx_listing_market(mk)
        self.var_market.set(mv if mv is not None else k_h)
        self._run_single_with_code(
            cd,
            listing_market_override=k_h,
            silent_try_build=True,
            search_keyword_override="",
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

    def _begin_search_loading_state(self) -> None:
        """검색 워커 시작 시 버튼 비활성화·마우스 대기 커서(창 단위)."""
        try:
            self.btn_search.configure(state="disabled")
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
        try:
            self.btn_search.configure(state="normal")
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

    def _on_search(self) -> None:
        """파이프라인 AND 검색 · 단계 전부 미체크 시에는 키워드 검색 필수."""
        if self._busy:
            self.set_status_message(
                "이미 다른 작업이 진행 중입니다. 잠시만 기다려주세요."
            )
            return

        keyword = self.var_keyword.get().strip()
        market = self.var_market.get().strip().upper() or "KOSPI"
        if market not in ("KOSPI", "KOSDAQ", "ETF"):
            market = "KOSPI"

        try:
            pf_mcap = bool(self.var_pf_mcap_top100.get())
            pf_buy = bool(self.var_pf_buy_rules.get())
            pf_kim = bool(self.var_pf_kim_candle.get())
        except (tk.TclError, AttributeError):
            pf_mcap = pf_buy = pf_kim = False
        any_pf = pf_mcap or pf_buy or pf_kim

        self._clear_search_results_listbox()

        if not any_pf and not keyword:
            self.set_status_message(
                "종목 검색어를 입력하거나, 필터 단계 중 하나 이상을 켠 뒤 검색하세요."
            )
            messagebox.showwarning(
                "검색 조건",
                "필터를 모두 끈 상태에서는 상단 종목 검색창 입력이 필요합니다.\n"
                "또는 퀀트 필터 파이프라인 체크를 하나 이상 켜 주세요.",
            )
            return

        sp_cal = self._search_screen_universe_params()
        if sp_cal is None:
            return

        try:
            strategy_st_snap = extract_live_strategy_config(self)
        except RuntimeError as e:
            messagebox.showerror("매매 규칙 설정", str(e))
            return

        self._busy = True
        self._begin_search_loading_state()
        self.set_status_message(
            "유니버스·일봉 조회 및 퀀트 파이프라인 실행 중…"
        )

        threading.Thread(
            target=self._exec_search_worker,
            kwargs={
                "keyword": keyword,
                "market": market,
                "screener_params": sp_cal,
                "strategy_st": dict(strategy_st_snap),
                "pf_mcap_top100": pf_mcap,
                "pf_buy_rules": pf_buy,
                "pf_kim_candle": pf_kim,
            },
            daemon=True,
        ).start()

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
        self.set_status_message(f"검색 실패: {msg}")
        messagebox.showerror("검색 실패", msg)

    def _on_run(self):
        self._run_single_from_run_button()

    def _finish_run(
        self,
        res,
        *,
        deferred_chart_px: tuple[int, int] | None = None,
    ) -> None:
        self._busy = False
        self.btn_run.configure(state="normal", text="백테스트 실행")
        try:
            self.btn_rules_refresh.configure(state="normal")
        except (tk.TclError, AttributeError):
            pass
        if not res.ok:
            self._last_chart_path = None
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

        self._set_summary(gui_summary_five_lines(res))

        code_hist = str(getattr(self, "_pending_run_code", "") or "").zfill(6)
        if code_hist and code_hist != "000000":
            self._last_active_stock_code = code_hist
        disp_name = ""
        for row in res.summary_rows:
            if row[0] == "종목":
                cell = str(row[1])
                lp = cell.rfind("(")
                rp = cell.rfind(")")
                if lp >= 0 and rp > lp:
                    disp_name = cell[:lp].strip()
                break
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
            tk_snap = ticket

            def _chart_paint_task() -> None:
                try:
                    outp, skipped = materialize_backtest_chart_png(
                        replay_copy,
                        chart_render_px=px,
                        write_signal_debug_log=False,
                    )
                    self.after(
                        0,
                        lambda p=outp,
                        sk=skipped,
                        tt=tk_snap: self._materialized_chart_dispatch(tt, p, sk),
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
