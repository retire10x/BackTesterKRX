"""
데스크톱 GUI (CustomTkinter).
차트: output/backtest_report.png → CTkImage (매매 규칙 패널 + 차트; 추세 이평 범례는 PNG 내장).
YAML·설정 dict·툴팁: `gui_helpers`. 엔진: `src.metrics.run_backtest_detailed`.
본문·툴팁 폰트는 `gui_helpers.gui_body_font()`(13pt)로 통일, `set_widget_scaling`/`set_window_scaling` 1.0 고정.
메인 레이아웃은 grid weight·`sticky="nsew"` 기반 반응형(노트북 등 저해상도); 차트는 `chart_overlay_host` 실측 픽셀로 PNG 리사이즈.
"""
from __future__ import annotations

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

from PIL import Image, ImageOps
from tkcalendar import DateEntry

from src.data_loader import (
    default_backtest_period_range,
    fetch_filtered_universe,
    load_config,
    load_ohlcv,
    ohlcv_warm_start_date,
)
from src.gui_helpers import (
    HoverTooltip,
    gui_body_font,
    GUI_FONT_FAMILY,
    GUI_FONT_SIZE,
    apply_yaml_to_widgets,
    date_entry_theme_kw,
    gui_summary_five_lines,
    refresh_search_listbox_from_screener_entries,
    trading_rules_static_text,
    try_build_config,
)
from src.backtest_constants import TREND_MA_PERIODS
from src.metrics import BacktestResult, normalize_interval, run_backtest_detailed
from src.stock_screener import (
    ScreenerEntry,
    default_screener_config,
    screen_universe,
    summary_line_for_entry,
)

# ==========================================
# 스크리너 결과 → 리스트박스 표시용 정규화 (방어적 정렬·슬라이싱)
# ==========================================


def _screener_gui_item_to_code_name_score(item: object) -> tuple[str, str, float] | None:
    """임의 객체/딕셔너리/시퀀스에서 (종목코드, 종목명, 정렬용 점수) 추출."""
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
# 좌측 최소 가로폭. 우측·차트는 창 크기에 맞춰 가변(CHART_IMG_* 는 이미지 첫 레이아웃 전 추정 크기용).
FIXED_LEFT_W = 290

# 차트 패널: 영업일 기준(±7, ±1) 기간 평행 이동 시 라벨·자동 재실행과 연계
# 차트 이미지 위 좌·우 클릭 영역 (px, place)
CHART_NAV_STRIP_W = 50
DATE_CLAMP_MIN = date(1990, 1, 1)

# 차트 패널이 아직 레이아웃 측정 전일 때 PIL 리사이즈 추정 크기 (노트북 저해상도 대응)
CHART_IMG_FALLBACK_W = 800
CHART_IMG_FALLBACK_H = 500

# 최근 실행 종목 이력: 메모리·디스크 모두 최대 이 개수 (FIFO)
BACKTEST_HISTORY_MAX = 30
BACKTEST_HISTORY_FILE = os.path.join("output", "backtest_history.json")


class BacktestGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        gui_body_font()  # CTkFont — Tk 루트 존재 후 캐시(모듈 import 시 생성 불가)

        self.title("BackTesterKRX v4.6")

        self._candidates: list[tuple[str, str]] = []
        self._busy = False
        self._screener_display_cap = 30
        # 마지막으로 성공한 단일/배치 차트 종목 코드 — 차트 기간 패닝 시 YAML·리스트 무관하게 유지
        self._last_active_stock_code = ""
        self._chart_ohlcv_cache_df = None  # 타입: pd.DataFrame | None
        self._chart_ohlcv_cache_code = ""
        self._img_ref: ctk.CTkImage | None = None
        self._last_chart_path: str | None = None
        self._chart_resize_after_id: str | None = None
        self._shift_auto_run_after_id: str | None = None

        self.var_interval = ctk.StringVar(value="daily")
        self.var_ma_period = ctk.StringVar(value="20")
        self._trend_vars: dict[int, ctk.BooleanVar] = {
            p: ctk.BooleanVar(value=(p in (20, 120))) for p in TREND_MA_PERIODS
        }
        self.var_show_candle = ctk.BooleanVar(value=True)
        self.var_show_volume = ctk.BooleanVar(value=True)
        self.var_show_revenue = ctk.BooleanVar(value=True)
        self.var_buy_fee_pct = ctk.StringVar(value="0.015")
        self.var_sell_fee_pct = ctk.StringVar(value="0.18")
        self.var_screener_enabled = ctk.BooleanVar(value=True)
        self.var_screener_metric = ctk.StringVar(value="atr14")
        self._history_deque = deque(maxlen=BACKTEST_HISTORY_MAX)

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
        row_search.grid_columnconfigure(1, weight=1)

        sf_market = ctk.CTkFrame(row_search, fg_color="transparent")
        sf_market.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(sf_market, text="시장", font=gui_body_font()).pack(side="left", padx=(0, 4))
        self.var_market = ctk.StringVar(value="KOSPI")
        ctk.CTkOptionMenu(
            sf_market,
            values=["KOSPI", "KOSDAQ", "ETF"],
            variable=self.var_market,
            width=86,
            font=gui_body_font(),
        ).pack(side="left")

        sf_kw = ctk.CTkFrame(row_search, fg_color="transparent")
        sf_kw.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ctk.CTkLabel(sf_kw, text="종목", font=gui_body_font()).pack(side="left", padx=(0, 4))
        self.var_keyword = ctk.StringVar(value="")
        ctk.CTkEntry(sf_kw, textvariable=self.var_keyword, height=28, font=gui_body_font()).pack(
            side="left", fill="x", expand=True
        )

        ctk.CTkButton(
            row_search,
            text="검색",
            width=72,
            height=28,
            font=gui_body_font(),
            command=self._on_search,
        ).grid(row=0, column=2, sticky="e")

        row_scr = ctk.CTkFrame(left, fg_color="transparent")
        row_scr.pack(fill="x", padx=14, pady=(4, 2))
        self.cb_screener = ctk.CTkCheckBox(
            row_scr,
            text="종목 스크리너",
            variable=self.var_screener_enabled,
            font=gui_body_font(),
            checkbox_width=18,
            checkbox_height=18,
        )
        self.cb_screener.pack(side="left", padx=(0, 10))
        tt_scr = (
            "백테스트 시작 전 실행됩니다.\n마지막 영업일(종료일) 기준 최근 거래일 N일 구간만 사용해 "
            "변동성·거래대금(Σ 거래량×종가)이 모두 높은 종목 순으로 상위 M개만 골라 M번 연속 백테스트합니다.\n"
            "**종가 < MA120 역배열 종목 사전 제외 기능 포함** — 일봉 기준 종가가 120일선 미만인 종목은 랭킹 연산 전 탈락합니다.\n"
            "시점 왜곡을 피하기 위해 스크린은 종료일까지의 과거 확정 분만 사용합니다(YAML universe.screener)."
        )
        HoverTooltip(self.cb_screener, tt_scr)

        scr_metric_wrap = ctk.CTkFrame(row_scr, fg_color="transparent")
        scr_metric_wrap.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            scr_metric_wrap, text="변동성 지표", font=gui_body_font(), width=74
        ).pack(side="left", padx=(0, 4))
        ctk.CTkOptionMenu(
            scr_metric_wrap,
            variable=self.var_screener_metric,
            values=["atr14", "std_return"],
            width=134,
            font=gui_body_font(),
        ).pack(side="left")

        ctk.CTkLabel(left, text="검색 결과 (1개만 선택)", font=gui_body_font()).pack(anchor="w", padx=14)
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
        row_dt.grid_columnconfigure(0, weight=1)
        row_dt.grid_columnconfigure(1, weight=1)
        d0 = ctk.CTkFrame(row_dt, fg_color="transparent")
        d0.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        d1 = ctk.CTkFrame(row_dt, fg_color="transparent")
        d1.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        ctk.CTkLabel(d0, text="시작일", font=gui_body_font()).pack(anchor="w")
        self._date_start = DateEntry(
            d0,
            width=9,
            date_pattern="yyyy-mm-dd",
            font=(GUI_FONT_FAMILY, GUI_FONT_SIZE),
            **date_entry_theme_kw(),
        )
        _ds, _de = default_backtest_period_range()
        self._date_start.set_date(_ds)
        self._date_start.pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(d1, text="종료일", font=gui_body_font()).pack(anchor="w")
        self._date_end = DateEntry(
            d1,
            width=9,
            date_pattern="yyyy-mm-dd",
            font=(GUI_FONT_FAMILY, GUI_FONT_SIZE),
            **date_entry_theme_kw(),
        )
        self._date_end.set_date(_de)
        self._date_end.pack(fill="x", pady=(2, 0))

        row_money_fee = ctk.CTkFrame(left, fg_color="transparent")
        row_money_fee.pack(fill="x", padx=14, pady=(0, 6))
        row_money_fee.grid_columnconfigure(0, weight=1)
        row_money_fee.grid_columnconfigure(1, weight=1)
        row_money_fee.grid_columnconfigure(2, weight=1)
        fc = ctk.CTkFrame(row_money_fee, fg_color="transparent")
        fc.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        f0 = ctk.CTkFrame(row_money_fee, fg_color="transparent")
        f0.grid(row=0, column=1, sticky="ew", padx=(0, 6))
        f1 = ctk.CTkFrame(row_money_fee, fg_color="transparent")
        f1.grid(row=0, column=2, sticky="ew", padx=(0, 0))
        ctk.CTkLabel(fc, text="가상 원금", font=gui_body_font()).pack(anchor="w")
        self.var_cash = ctk.StringVar(value="5000000")
        ctk.CTkEntry(
            fc,
            textvariable=self.var_cash,
            height=28,
            font=gui_body_font(),
        ).pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(f0, text="매수 수수료(%)", font=gui_body_font()).pack(
            anchor="w"
        )
        ctk.CTkEntry(
            f0,
            textvariable=self.var_buy_fee_pct,
            height=28,
            font=gui_body_font(),
        ).pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(f1, text="매도 수수료(%)", font=gui_body_font()).pack(
            anchor="w"
        )
        ctk.CTkEntry(
            f1,
            textvariable=self.var_sell_fee_pct,
            height=28,
            font=gui_body_font(),
        ).pack(fill="x", pady=(2, 0))

        ctk.CTkLabel(
            left,
            text=f"최근 백테스트 이력 (FIFO {BACKTEST_HISTORY_MAX})",
            font=gui_body_font(),
        ).pack(anchor="w", padx=14, pady=(4, 2))
        hist_wrap = ctk.CTkFrame(left, fg_color="transparent")
        hist_wrap.pack(fill="x", padx=14, pady=(0, 6))
        hist_wrap.grid_columnconfigure(0, weight=1)
        hist_list_frame = ctk.CTkFrame(hist_wrap, fg_color="transparent")
        hist_list_frame.grid(row=0, column=0, sticky="nsew")
        self.list_history = tk.Listbox(
            hist_list_frame,
            height=5,
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
        self.btn_history_del = ctk.CTkButton(
            hist_wrap,
            text="삭제",
            width=44,
            height=28,
            font=ctk.CTkFont(family=GUI_FONT_FAMILY, size=GUI_FONT_SIZE - 1),
            command=self._on_history_delete,
        )
        self.btn_history_del.grid(row=0, column=1, sticky="ne", padx=(8, 0))

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

        self.var_filter_trend = ctk.BooleanVar(value=True)
        self.var_slope_threshold = ctk.StringVar(value="0.01")
        self.var_filter_breakout = ctk.BooleanVar(value=True)
        self.var_filter_timebuf = ctk.BooleanVar(value=True)

        self.var_golden_buy = ctk.BooleanVar(value=True)
        self.var_dead_sell = ctk.BooleanVar(value=True)

        self.var_trailing_stop = ctk.BooleanVar(value=False)
        self.var_trailing_reference_pct = ctk.StringVar(value="10")
        self.var_trailing_drop_below_pct = ctk.StringVar(value="3.0")
        self.var_trailing_drop_above_pct = ctk.StringVar(value="5.0")

        right = ctk.CTkFrame(self, corner_radius=10)
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 8), pady=(8, 8))
        right.grid_propagate(True)
        right.grid_rowconfigure(0, weight=0)
        right.grid_rowconfigure(1, weight=0)
        right.grid_rowconfigure(2, weight=1)
        right.grid_columnconfigure(0, weight=1)

        rules_panel = ctk.CTkFrame(
            right,
            corner_radius=8,
            border_width=1,
            border_color=("gray65", "gray45"),
            fg_color=("gray92", "gray18"),
        )
        rules_panel.grid(row=0, column=0, sticky="ew", padx=8, pady=(4, 4))

        rules_head = ctk.CTkFrame(rules_panel, fg_color="transparent")
        rules_head.pack(fill="x", padx=8, pady=(8, 4))
        ctk.CTkLabel(
            rules_head,
            text="매매 규칙 · v4.6",
            font=gui_body_font(),
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            rules_head,
            text="기본 크로스(앞) │ 옵션 필터는 골든 매수 후보에 AND 적용 · 매도는 트레일 우선 또는 데드(OR)",
            font=gui_body_font(),
            text_color=("gray35", "gray60"),
            anchor="w",
        ).pack(anchor="w", pady=(2, 0))

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

        # 🟢 좌측: 매수 조건 카드 프레임
        buy_frame = ctk.CTkFrame(
            grid_container,
            corner_radius=6,
            border_width=1,
            border_color=("gray75", "gray30"),
            fg_color=("gray95", "gray20"),
        )
        buy_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=4)

        # 🔴 우측: 매도 조건 카드 프레임
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

        buy_strip = ctk.CTkFrame(buy_frame, fg_color="transparent")
        buy_strip.pack(fill="x", padx=10, pady=(0, 10))

        # 1. 추세 (맨 왼쪽 배치)
        self.cb_trend = ctk.CTkCheckBox(
            buy_strip,
            text="추세",
            variable=self.var_filter_trend,
            font=gui_body_font(),
            checkbox_width=18,
            checkbox_height=18,
        )
        self.cb_trend.pack(side="left", padx=(0, 2))
        HoverTooltip(self.cb_trend, tt_trend)

        self.slope_spin = ctk.CTkFrame(buy_strip, fg_color="transparent")
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

        ctk.CTkLabel(buy_strip, text="|", font=gui_body_font(), text_color="gray50").pack(
            side="left", padx=(0, 8)
        )

        # 2. 골든 매수
        self.cb_golden = ctk.CTkCheckBox(
            buy_strip,
            text="골든 매수",
            variable=self.var_golden_buy,
            font=gui_body_font(),
            checkbox_width=18,
            checkbox_height=18,
        )
        self.cb_golden.pack(side="left", padx=(0, 8))
        HoverTooltip(self.cb_golden, tt_golden)

        ctk.CTkLabel(buy_strip, text="|", font=gui_body_font(), text_color="gray50").pack(
            side="left", padx=(0, 8)
        )

        # 3. 돌파 강도
        self.cb_breakout = ctk.CTkCheckBox(
            buy_strip,
            text="돌파 강도",
            variable=self.var_filter_breakout,
            font=gui_body_font(),
            checkbox_width=18,
            checkbox_height=18,
        )
        self.cb_breakout.pack(side="left", padx=(0, 8))
        HoverTooltip(self.cb_breakout, tt_breakout)

        ctk.CTkLabel(buy_strip, text="|", font=gui_body_font(), text_color="gray50").pack(
            side="left", padx=(0, 8)
        )

        # 4. 시간 버퍼
        self.cb_timebuf = ctk.CTkCheckBox(
            buy_strip,
            text="시간 버퍼",
            variable=self.var_filter_timebuf,
            font=gui_body_font(),
            checkbox_width=18,
            checkbox_height=18,
        )
        self.cb_timebuf.pack(side="left")
        HoverTooltip(self.cb_timebuf, tt_timebuf)

        # 🔴 매도 조건 내용 배치
        lbl_sell_title = ctk.CTkLabel(
            sell_frame,
            text="🔴 매도 청산 조건 (OR 결합)",
            font=ctk.CTkFont(family=GUI_FONT_FAMILY, size=GUI_FONT_SIZE, weight="bold"),
            anchor="w",
        )
        lbl_sell_title.pack(anchor="w", padx=10, pady=(8, 6))

        sell_strip = ctk.CTkFrame(sell_frame, fg_color="transparent")
        sell_strip.pack(fill="x", padx=10, pady=(0, 10))

        cb_dead = ctk.CTkCheckBox(
            sell_strip,
            text="데드 매도",
            variable=self.var_dead_sell,
            font=gui_body_font(),
            checkbox_width=18,
            checkbox_height=18,
        )
        cb_dead.pack(side="left", padx=(0, 8))
        HoverTooltip(cb_dead, tt_dead)

        ctk.CTkLabel(sell_strip, text="|", font=gui_body_font(), text_color="gray50").pack(
            side="left", padx=(0, 8)
        )

        cb_trailing = ctk.CTkCheckBox(
            sell_strip,
            text="가변 낙폭",
            variable=self.var_trailing_stop,
            font=gui_body_font(),
            checkbox_width=18,
            checkbox_height=18,
        )
        cb_trailing.pack(side="left", padx=(0, 6))
        HoverTooltip(cb_trailing, tt_trailing_short)

        ctk.CTkLabel(sell_strip, text="기준", font=gui_body_font()).pack(
            side="left", padx=(0, 2)
        )
        self.entry_trailing_ref = ctk.CTkEntry(
            sell_strip,
            width=36,
            height=22,
            font=gui_body_font(),
            textvariable=self.var_trailing_reference_pct,
        )
        self.entry_trailing_ref.pack(side="left")
        ctk.CTkLabel(sell_strip, text="%", font=gui_body_font()).pack(
            side="left", padx=(2, 6)
        )

        ctk.CTkLabel(sell_strip, text="미달", font=gui_body_font()).pack(side="left")
        self.entry_trailing_below = ctk.CTkEntry(
            sell_strip,
            width=32,
            height=22,
            font=gui_body_font(),
            textvariable=self.var_trailing_drop_below_pct,
        )
        self.entry_trailing_below.pack(side="left", padx=(2, 2))
        ctk.CTkLabel(sell_strip, text="%", font=gui_body_font()).pack(
            side="left", padx=(0, 6)
        )

        ctk.CTkLabel(sell_strip, text="돌파", font=gui_body_font()).pack(side="left")
        self.entry_trailing_above = ctk.CTkEntry(
            sell_strip,
            width=32,
            height=22,
            font=gui_body_font(),
            textvariable=self.var_trailing_drop_above_pct,
        )
        self.entry_trailing_above.pack(side="left", padx=(2, 2))
        ctk.CTkLabel(sell_strip, text="%", font=gui_body_font()).pack(side="left")

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
        self.chart_control_panel.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))

        btn_container = ctk.CTkFrame(self.chart_control_panel, fg_color="transparent")
        btn_container.pack(anchor="center")

        self.btn_fast_rewind = ctk.CTkButton(
            btn_container,
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
            btn_container,
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
            btn_container,
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
            btn_container,
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
            btn_container,
            text="",
            font=ctk.CTkFont(family=GUI_FONT_FAMILY, size=GUI_FONT_SIZE, weight="bold"),
            text_color=("gray25", "gray75"),
        )
        self.lbl_current_period.pack(side="left", padx=(18, 6))

        self.chart_frame = ctk.CTkFrame(
            right, fg_color=("gray95", "gray17")
        )
        self.chart_frame.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 4))

        self.chart_frame.grid_propagate(True)
        self.chart_frame.grid_rowconfigure(0, weight=1)
        self.chart_frame.grid_columnconfigure(0, weight=1)

        # 차트 컨테이너: 이미지 전체
        self.chart_overlay_host = ctk.CTkFrame(
            self.chart_frame, fg_color="transparent"
        )
        self.chart_overlay_host.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)

        self.lbl_chart = ctk.CTkLabel(
            self.chart_overlay_host,
            text="백테스트 실행 후 차트가 표시됩니다.",
            fg_color="transparent",
            font=gui_body_font(),
        )
        self.lbl_chart.pack(fill="both", expand=True)

        self.chart_overlay_host.bind("<Configure>", self._on_chart_frame_configure)

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
        self._apply_maximized_geometry()
        self._update_period_label()

        # 매수 필터 인터락 등록
        self.var_filter_trend.trace_add("write", self._sync_buy_filters_interlock)
        self.var_filter_trend.set(True)
        self._sync_buy_filters_interlock()

        self._load_backtest_history_from_disk()
        self._sync_history_listbox()
        self.protocol("WM_DELETE_WINDOW", self._on_user_close)

    def _refresh_trading_rules_display(self, *_args: object) -> None:
        """우측 매매 규칙 패널(읽기 전용 텍스트) - 제거됨."""
        pass

    def _apply_maximized_geometry(self) -> None:
        try:
            self.minsize(960, 540)
            self.state("zoomed")
        except tk.TclError:
            try:
                self.attributes("-zoomed", True)
            except tk.TclError:
                self.geometry("1280x840")

    def _on_chart_frame_configure(self, _event: tk.Event) -> None:
        if not self._last_chart_path:
            return
        if self._chart_resize_after_id is not None:
            self.after_cancel(self._chart_resize_after_id)
        self._chart_resize_after_id = self.after(120, self._deferred_repaint_chart)

    def _deferred_repaint_chart(self) -> None:
        self._chart_resize_after_id = None
        if self._last_chart_path:
            self._update_chart_image(self._last_chart_path)

    def _chart_overlay_host_inner_pixel_size(self) -> tuple[int, int]:
        """
        chart_overlay_host 실측 폭·높이로 PNG contain 타깃 크기를 정한다.
        배치 직후 winfo 가 0~1px 인 경우가 있어 update_idletasks 후에도 비정상이면 폴백한다.
        """
        self.chart_overlay_host.update_idletasks()
        measured_width = int(self.chart_overlay_host.winfo_width())
        measured_height = int(self.chart_overlay_host.winfo_height())
        if measured_width <= 10 or measured_height <= 10:
            measured_width = CHART_IMG_FALLBACK_W
            measured_height = CHART_IMG_FALLBACK_H
        fw = max(240, measured_width - 6)
        fh = max(200, measured_height - 6)
        return fw, fh

    def _update_chart_image(self, image_path: str | None) -> None:
        """엔진이 저장한 PNG 를 패널 크기에 맞춰 표시(비율 유지, 찌그러짐 없음)."""
        if not image_path or not os.path.isfile(image_path):
            self._last_chart_path = None
            self._img_ref = None
            self.lbl_chart.configure(
                image=None, text="그래프 파일을 찾을 수 없습니다."
            )
            return

        self._last_chart_path = image_path
        try:
            fw, fh = self._chart_overlay_host_inner_pixel_size()

            with Image.open(image_path) as pil_img:
                # 비율 유지 피팅(잘린 것처럼 보이는 강제 stretch 방지)
                resized = ImageOps.contain(
                    pil_img, (fw, fh), method=Image.Resampling.LANCZOS
                )

            self._img_ref = ctk.CTkImage(
                light_image=resized,
                dark_image=resized,
                size=(fw, fh),
            )
            self.lbl_chart.configure(image=self._img_ref, text="")
        except Exception as e:
            self._img_ref = None
            self.lbl_chart.configure(image=None, text=f"이미지 로드 실패: {e}")

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
        """검색 결과 리스트박스·후보 캐시를 비운다(스크리너 재실행 시 이전 종목 잔상 방지)."""
        self.list_codes.delete(0, tk.END)
        self._candidates = []

    def update_gui_with_screener_results(
        self,
        final_top_n_list: list[object],
        *,
        announce: bool = True,
    ) -> None:
        """
        스크리너 최종 결과를 리스트박스에 반영. 전 종목이 넘어오는 경우에 대비해 점수순 정렬 후
        설정 상위 N(`_screener_display_cap`, 기본 30)만 표시한다.
        """
        self._clear_search_results_listbox()
        if not final_top_n_list:
            if announce:
                self.set_status_message("스크리너 조건에 부합하는 종목이 없습니다.")
            return

        cap = getattr(self, "_screener_display_cap", 30)
        try:
            limit = max(1, min(200, int(cap)))
        except (TypeError, ValueError):
            limit = 30

        rows: list[tuple[str, str, float]] = []
        for item in final_top_n_list:
            row = _screener_gui_item_to_code_name_score(item)
            if row is not None:
                rows.append(row)

        rows.sort(key=lambda r: (-r[2], r[0]))
        truncated = rows[:limit]
        total_raw = len(final_top_n_list)

        self._candidates = [(c, n) for c, n, _s in truncated]
        for code, name, _sc in truncated:
            self.list_codes.insert(tk.END, f"{code}  {name}")
        if truncated:
            try:
                self.list_codes.selection_set(0)
            except tk.TclError:
                pass
        if announce:
            self.set_status_message(
                f"스크리너 검색 완료: {len(truncated)}건 표시 (총 {total_raw}건 중)"
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
                text="기간 이동: 먼저 한 종목 백테스트를 완료하거나 종목 스크린 후 차트가 열린 뒤 패닝하세요.",
            )
            return
        # 차트 기간 평행 이동 시 전 유니버스 일괄 스크린을 피하기 위해 단일 종목 재실행만 수행합니다.
        cfg.setdefault("universe", {}).setdefault("screener", {})["enabled"] = False
        self._run_backtest(cfg)

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

        self._busy = True
        self._update_period_label()
        self.btn_run.configure(state="disabled", text="계산 중…")
        self.lbl_status.configure(text="백테스트 계산 중…")

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
                )
                self.after(0, lambda r=res: self._finish_run(r))
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

    def _run_screener_batch(self, cfg: dict) -> None:
        """스크린 → 선정 종목 일괄 백테스트(차트·요약은 마지막 성공 건 또는 집계)."""
        if self._busy:
            return
        # 백엔드 스레드 돌기 전에 이전 검색/스크린 리스트 잔상 제거(탈락 종목이 남아 보이는 현상 방지)
        self._clear_search_results_listbox()
        self._busy = True
        uni = cfg.get("universe") or {}
        scr = uni.get("screener") or {}
        try:
            self._screener_display_cap = max(1, min(200, int(scr.get("top_n", 30))))
        except (TypeError, ValueError):
            self._screener_display_cap = 30
        self._pending_run_code = str(uni.get("selected_code") or "").zfill(6)
        self._update_period_label()
        self.btn_run.configure(state="disabled", text="스크린·일괄 계산 중…")
        self.lbl_status.configure(text="종목 스크리너 실행 중…")
        period = cfg.get("period") or {}
        end_d = str(period.get("end_date") or "").strip()

        def worker() -> None:
            agg_err: list[str] = []
            try:
                lk = max(5, min(120, int(scr.get("lookback_trading_days", 20))))
                tn = max(1, min(200, int(scr.get("top_n", 30))))
                metric = str(scr.get("volatility_metric") or "atr14").strip().lower()

                def prog(done: int, total: int, code: str) -> None:
                    self.after(
                        0,
                        lambda d=done, tot=total, c=code: self.lbl_status.configure(
                            text=(
                                f"스크리너 진행 {d}/{tot} "
                                f"· 종료일까지 일봉만 사용 · 최근 심볼 {c}"
                            )
                        ),
                    )

                ds = default_screener_config()
                try:
                    mc_kw = float(
                        scr.get("min_market_cap_krw", ds["min_market_cap_krw"])
                    )
                except (TypeError, ValueError):
                    mc_kw = float(ds["min_market_cap_krw"])
                hf_pair = bool(
                    scr.get(
                        "hard_ma_pair_trend_filter",
                        ds["hard_ma_pair_trend_filter"],
                    )
                )
                try:
                    pb_cap = float(
                        scr.get("pullback_rank_cap_pct", ds["pullback_rank_cap_pct"])
                    )
                except (TypeError, ValueError):
                    pb_cap = float(ds["pullback_rank_cap_pct"])

                picks = screen_universe(
                    market=str(uni.get("market") or "KOSPI"),
                    keyword=str(uni.get("search_keyword") or ""),
                    end_date=end_d,
                    lookback_trading_days=lk,
                    top_n=tn,
                    volatility_metric=metric,
                    progress_cb=prog,
                    min_market_cap_krw=mc_kw,
                    hard_ma_pair_trend_filter=hf_pair,
                    pullback_rank_cap_pct=pb_cap,
                )
                if not picks:
                    self.after(
                        0,
                        lambda: self._finish_screener_batch_error(
                            "스크리너 후보가 없습니다. 종료일·시장·키워드·데이터를 확인하세요."
                        ),
                    )
                    return

                out_dir = os.path.join("output")
                os.makedirs(out_dir, exist_ok=True)
                tsv_path = os.path.join(out_dir, "screener_last.tsv")
                try:
                    with open(tsv_path, "w", encoding="utf-8") as fh:
                        fh.write(
                            "rank\tcode\tname\tvol_metric\tamount_krw_sum\tpullback_hi_pct\tvol_contract_pct\tscore_pct_mean\n"
                        )
                        for i, ent in enumerate(picks, start=1):
                            fh.write(
                                f"{i}\t{ent.code}\t{ent.name}\t{ent.volatility_raw:.12g}"
                                f"\t{int(round(ent.turnover_krw_sum))}\t"
                                f"{ent.pullback_from_high_pct:.12g}\t{ent.volume_contract_pct:.12g}\t"
                                f"{ent.combined_score:.6g}\n"
                            )
                except OSError:
                    agg_err.append(f"[경고] 스크리너 TSV 저장 실패 ({tsv_path})")

                results: list[tuple[ScreenerEntry, BacktestResult]] = []
                for i, ent in enumerate(picks):
                    self.after(
                        0,
                        lambda ix=i + 1, tot=len(picks), c=str(ent.code): self.lbl_status.configure(
                            text=f"백테스트 {ix}/{tot} 진행 중… ({c})"
                        ),
                    )
                    r = run_backtest_detailed(cfg, override_code=ent.code)
                    results.append((ent, r))

                self.after(
                    0,
                    lambda plist=list(picks), rlst=list(results): self._finish_screener_batch(
                        plist,
                        rlst,
                        agg_err,
                        tsv_path,
                    ),
                )
            except Exception as e:
                import traceback

                traceback.print_exc()
                self.after(
                    0,
                    lambda msg=str(e): self._finish_screener_batch_error(
                        f"스크리너·일괄 백테스트 오류: {msg}"
                    ),
                )

        threading.Thread(target=worker, daemon=True).start()

    def _finish_screener_batch_error(self, msg: str) -> None:
        self._busy = False
        self._clear_search_results_listbox()
        self.btn_run.configure(state="normal", text="백테스트 실행")
        self.lbl_status.configure(text="오류로 종료됨.")
        messagebox.showerror("스크리너 실패", msg)

    def _finish_screener_batch(
        self,
        picks: list[ScreenerEntry],
        results: list[tuple[ScreenerEntry, BacktestResult]],
        agg_err: list[str],
        screener_tsv_path: str,
    ) -> None:
        self._busy = False
        self.btn_run.configure(state="normal", text="백테스트 실행")

        ok_runs = [(e, r) for e, r in results if r.ok]
        if not ok_runs:
            worst = results[0][1] if results else None
            err_txt = worst.error if worst and worst.error else "모든 종목에서 백테스트가 실패했습니다."
            self._last_chart_path = None
            self._img_ref = None
            self.lbl_chart.configure(image=None, text=err_txt)
            lines = [*agg_err, f"[안내] 스크리너 TSV → {screener_tsv_path}"]
            for e, r in results:
                if not r.ok and r.error:
                    lines.append(f"[실패] {e.code} {e.name}: {r.error}")
            self._set_summary("\n".join(lines[:28]))
            self.lbl_status.configure(text="실패 종료.")
            messagebox.showerror("일괄 백테스트 실패", err_txt)
            return

        refresh_search_listbox_from_screener_entries(self, picks, announce=False)

        rows_out: list[list[str]] = []
        for e, r in results:
            if not r.ok:
                rows_out.append(
                    [e.code, e.name, "-", "-", str(r.error or "실패")[:40]]
                )
                continue
            m = self._metrics_from_summary(r)
            rows_out.append(
                [
                    e.code,
                    e.name,
                    f"{m['total']:.2f}"
                    if m["total"] is not None
                    else "-",
                    f"{m['mdd']:.2f}" if m["mdd"] is not None else "-",
                    "",
                ]
            )

        agg_lines: list[str] = [
            f"종목 스크리너: 선정 {len(picks)}개 · 성공 {len(ok_runs)}개 백테스트",
            f"스크린 결과 파일: {screener_tsv_path}",
        ]
        agg_lines.extend(agg_err)
        agg_lines.extend(
            [
                "",
                "--- 스크린 상위 (일부) ---",
                *[summary_line_for_entry(x) for x in picks[: min(8, len(picks))]],
            ]
        )
        if len(picks) > 8:
            agg_lines.append(f"... 외 {len(picks) - 8}개 생략")
        agg_lines.extend(
            [
                "",
                "코드 · 종목 · 누적% · MDD%",
            ]
        )
        for row in rows_out[:20]:
            agg_lines.append(" · ".join(str(x) for x in row))
        if len(rows_out) > 20:
            agg_lines.append(f"... 외 {len(rows_out) - 20}행 생략")

        self._set_summary("\n".join(agg_lines))

        _, last_ok = ok_runs[-1]
        self._pending_run_code = ok_runs[-1][0].code.zfill(6)
        self._last_active_stock_code = self._pending_run_code
        for ent, rr in ok_runs:
            self._push_history(
                ent.code.zfill(6),
                self._disp_name_from_res(rr),
            )

        self.update_idletasks()
        self._update_chart_image(last_ok.report_path)
        self._update_period_label()

        warn_skip = False
        for _e, rr in ok_runs:
            if rr.trade_markers_skipped > 0:
                warn_skip = True
                break
        if warn_skip:
            self.lbl_status.configure(text="완료(일부 타점 확인 필요)")
            messagebox.showwarning(
                "차트 타점 확인",
                "일부 종목에서 차트 타점 매칭 경고가 있었습니다. 터미널 로그의 [CRITICAL] 을 참고하세요.",
            )
        else:
            self.lbl_status.configure(text="완료 (스크리너 배치)")


    @staticmethod
    def _metrics_from_summary(res: BacktestResult) -> dict[str, float | None]:
        keys = {row[0]: row[1] for row in res.summary_rows}

        def grab_pct(label: str) -> float | None:
            raw = str(keys.get(label, "")).replace(",", "").strip().replace("%", "")
            if not raw:
                return None
            try:
                return float(raw)
            except ValueError:
                return None

        return {
            "total": grab_pct("누적 수익률"),
            "cagr": grab_pct("연평균 수익률"),
            "mdd": grab_pct("최대 손실 낙폭"),
        }

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
        s = line.strip()
        if not s:
            return "", ""
        parts = s.split(None, 1)
        code = parts[0].strip().zfill(6)
        name = parts[1].strip() if len(parts) > 1 else ""
        return code, name

    def _sync_history_listbox(self) -> None:
        self.list_history.delete(0, tk.END)
        for c, nm in self._history_deque:
            self.list_history.insert(tk.END, f"{c}  {nm}")

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
        pairs: list[tuple[str, str]] = []
        for el in seq:
            if not isinstance(el, (list, tuple)) or len(el) < 2:
                continue
            cd = str(el[0]).strip().zfill(6)
            if not cd or cd == "000000":
                continue
            nm_el = el[1]
            nm = str(nm_el).strip() if nm_el is not None else ""
            pairs.append((cd, nm or cd))
            if len(pairs) >= BACKTEST_HISTORY_MAX:
                break
        if not pairs:
            return
        nd = deque(maxlen=BACKTEST_HISTORY_MAX)
        for cd, nm in reversed(pairs):
            nd.appendleft((cd, nm))
        self._history_deque = nd

    def _save_backtest_history_to_disk(self) -> None:
        path = BACKTEST_HISTORY_FILE
        out_dir = os.path.dirname(path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        items = [[c, n] for c, n in self._history_deque]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {"version": 1, "items": items},
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

    def _push_history(self, code: str, display_name: str) -> None:
        """최근 실행 이력을 갱신한다(맨 위 최신 · 동일 종목 재실행 시 순서 재배치)."""
        cd = str(code).strip().zfill(6)
        if not cd or cd == "000000":
            return
        nm = (display_name or "").strip() or cd
        rest = [(c, n) for c, n in self._history_deque if c != cd][
            : BACKTEST_HISTORY_MAX - 1
        ]
        nd = deque(maxlen=BACKTEST_HISTORY_MAX)
        nd.appendleft((cd, nm))
        nd.extend(rest)
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
            ((c, n) for c, n in self._history_deque if c != code),
            maxlen=BACKTEST_HISTORY_MAX,
        )
        self._sync_history_listbox()

    def _on_search_list_dbl_click(self, _evt: tk.Event | None = None) -> None:
        sel = self.list_codes.curselection()
        if not sel:
            return
        code, name = self._split_codes_list_line(self.list_codes.get(sel[0]))
        if not code or code == "000000":
            return
        if name:
            self.var_keyword.set(name)
        cfg = try_build_config(self, silent=False, selected_code_override=code)
        if cfg is None:
            return
        self._run_backtest(cfg)

    def _on_history_list_dbl_click(self, _evt: tk.Event | None = None) -> None:
        sel = self.list_history.curselection()
        if not sel:
            return
        code, _name = self._split_codes_list_line(self.list_history.get(sel[0]))
        if not code or code == "000000":
            return
        cfg = try_build_config(self, silent=False, selected_code_override=code)
        if cfg is None:
            return
        self._run_backtest(cfg)

    def _search_screen_universe_params(self) -> dict[str, object] | None:
        """검색용 스크리너 호출에 필요한 종료일·YAML 병합 top_n 등 (백테스트 실행과 동일 규격)."""
        try:
            end_d = self._date_end.get_date().strftime("%Y-%m-%d")
        except (ValueError, tk.TclError):
            self.set_status_message(
                "스크리너 검색: 종료일을 캘린더에서 선택하세요."
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
        if getattr(self, "var_screener_metric", None) is not None:
            mv = str(self.var_screener_metric.get()).strip().lower()
            if mv in ("atr14", "std_return"):
                scr["volatility_metric"] = mv

        lk = max(5, min(120, int(scr.get("lookback_trading_days", 20))))
        tn = max(1, min(200, int(scr.get("top_n", 30))))
        metric = str(scr.get("volatility_metric") or "atr14").strip().lower()
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
        self._screener_display_cap = tn
        return {
            "end_date": end_d,
            "lookback": lk,
            "top_n": tn,
            "metric": metric,
            "min_market_cap_krw": min_cap_krw,
            "hard_ma_pair_trend_filter": pair_hf,
            "pullback_rank_cap_pct": pb_cap,
        }

    def _on_search(self) -> None:
        """
        검색 버튼 분기 —
        검색어 O + 스크리너 ON: 시장 전체 스크린 상위 N → 그 안에서 검색어 부분 일치 필터.
        검색어 O + 스크리너 OFF: 유니버스 전체 중 검색어 부분 일치.
        검색어 X + 스크리너 ON: 스크린 상위 N 목록 그대로(랭킹 순서 유지).
        검색어 X + 스크리너 OFF: 안내만 하고 종료.

        스크리너·유니버스 조회가 메인 스레드를 길게 블로킹하지 않도록 백그라운드 스레드에서 실행한다.
        """
        if self._busy:
            self.set_status_message(
                "이미 다른 작업이 진행 중입니다. 잠시만 기다려주세요."
            )
            return

        is_screener_on = bool(self.var_screener_enabled.get())
        keyword = self.var_keyword.get().strip()
        market = self.var_market.get().strip().upper() or "KOSPI"
        if market not in ("KOSPI", "KOSDAQ", "ETF"):
            market = "KOSPI"

        self._clear_search_results_listbox()

        if not keyword and not is_screener_on:
            self.set_status_message(
                "검색어 입력 또는 스크리너 선택하세요."
            )
            return

        sp: dict[str, object] | None = None
        if is_screener_on:
            sp = self._search_screen_universe_params()
            if sp is None:
                return

        self._busy = True
        self.set_status_message(
            "퀀트 스크리너 및 유니버스 분석 중… (메인 창 멈춤 방지)"
        )

        threading.Thread(
            target=self._exec_search_worker,
            kwargs={
                "is_screener_on": is_screener_on,
                "keyword": keyword,
                "market": market,
                "screener_params": sp,
            },
            daemon=True,
        ).start()

    def _exec_search_worker(
        self,
        *,
        is_screener_on: bool,
        keyword: str,
        market: str,
        screener_params: dict[str, object] | None,
    ) -> None:
        """검색·스크린에 필요한 무거운 I/O 및 screen_universe 를 백그라운드에서 수행한다."""
        rows: list[tuple[str, str]] = []

        try:
            if keyword:
                if is_screener_on:
                    if screener_params is None:
                        raise ValueError(
                            "스크리너 검색 설정을 읽지 못했습니다. 종료일·설정을 확인하세요."
                        )
                    p = screener_params
                    picks = screen_universe(
                        market=market,
                        keyword="",
                        end_date=str(p["end_date"]),
                        lookback_trading_days=int(p["lookback"]),
                        top_n=int(p["top_n"]),
                        volatility_metric=str(p["metric"]),
                        progress_cb=None,
                        min_market_cap_krw=float(p["min_market_cap_krw"]),
                        hard_ma_pair_trend_filter=bool(
                            p["hard_ma_pair_trend_filter"]
                        ),
                        pullback_rank_cap_pct=float(p["pullback_rank_cap_pct"]),
                    )
                    kl = keyword.lower()
                    filt: list[tuple[str, str]] = []
                    for ent in picks:
                        c = str(ent.code).strip().zfill(6)
                        n = str(ent.name)
                        if kl in c.lower() or kl in n.lower():
                            filt.append((c, n))
                    rows = sorted(filt, key=lambda x: x[0])
                else:
                    d = fetch_filtered_universe(market, keyword)
                    rows = sorted(d.items(), key=lambda x: x[0])
            else:
                if is_screener_on:
                    if screener_params is None:
                        raise ValueError(
                            "스크리너 검색 설정을 읽지 못했습니다. 종료일·설정을 확인하세요."
                        )
                    p = screener_params
                    picks = screen_universe(
                        market=market,
                        keyword="",
                        end_date=str(p["end_date"]),
                        lookback_trading_days=int(p["lookback"]),
                        top_n=int(p["top_n"]),
                        volatility_metric=str(p["metric"]),
                        progress_cb=None,
                        min_market_cap_krw=float(p["min_market_cap_krw"]),
                        hard_ma_pair_trend_filter=bool(
                            p["hard_ma_pair_trend_filter"]
                        ),
                        pullback_rank_cap_pct=float(p["pullback_rank_cap_pct"]),
                    )
                    rows = [
                        (str(e.code).strip().zfill(6), str(e.name)) for e in picks
                    ]

        except Exception as ex:
            self.after(
                0,
                lambda m=str(ex): self._finalize_search_failure(m),
            )
            return

        copy_rows = list(rows)
        self.after(0, lambda r=copy_rows: self._finalize_search_ui(r))

    def _finalize_search_ui(self, candidates: list[tuple[str, str]]) -> None:
        """워커 완료 후 메인 스레드에서 검색 결과 리스트만 갱신한다."""
        self._busy = False
        self._candidates = list(candidates)

        if not self._candidates:
            self.set_status_message("조건에 부합하는 종목이 없습니다.")
            return

        for code, name in self._candidates:
            self.list_codes.insert(tk.END, f"{code}  {name}")
        try:
            self.list_codes.selection_set(0)
        except tk.TclError:
            pass
        self.set_status_message(
            f"조회 완료: {len(self._candidates)}건이 리스트업되었습니다."
        )

    def _finalize_search_failure(self, msg: str) -> None:
        """검색 워커 예외 처리(메인 스레드 전용)."""
        self._busy = False
        self.set_status_message(f"검색 실패: {msg}")
        messagebox.showerror("검색 실패", msg)

    def _on_run(self):
        cfg = try_build_config(self, silent=False)
        if cfg is None:
            return
        scr = (
            cfg.get("universe", {}).get("screener")
            if isinstance(cfg.get("universe", {}).get("screener"), dict)
            else {}
        )
        if scr.get("enabled"):
            self._run_screener_batch(cfg)
        else:
            self._run_backtest(cfg)

    def _finish_run(self, res):
        self._busy = False
        self.btn_run.configure(state="normal", text="백테스트 실행")
        if not res.ok:
            self._last_chart_path = None
            self._img_ref = None
            self.lbl_chart.configure(image=None, text=res.error or "오류")
            self._set_summary(res.error or "알 수 없는 오류")
            self.lbl_status.configure(text="오류로 종료됨.")
            messagebox.showerror("백테스트 실패", res.error or "알 수 없는 오류")
            return

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
        self._push_history(code_hist, disp_name)

        self.update_idletasks()
        self._update_chart_image(res.report_path)
        self._update_period_label()
        if res.trade_markers_skipped > 0:
            self.lbl_status.configure(
                text=f"완료 · 매칭 실패 오류 발생 — 차트 타점 {res.trade_markers_skipped}건 누락"
            )
            messagebox.showwarning(
                "차트 타점 누락",
                f"{res.trade_markers_skipped}건의 매매가 차트 날짜 인덱스와 일치하지 않아 표시하지 못했습니다.\n"
                "요약 로그의 [CRITICAL] 항목과 터미널 메시지를 확인하고 데이터를 점검하세요.",
            )
        else:
            self.lbl_status.configure(text="완료")


def main():
    app = BacktestGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
