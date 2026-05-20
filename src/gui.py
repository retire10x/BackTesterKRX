"""
데스크톱 GUI (CustomTkinter).
차트: output/backtest_report.png → CTkImage (매매 규칙 패널 + 차트; 추세 이평 범례는 PNG 내장).
YAML·설정 dict·툴팁: `gui_helpers`. 엔진: `src.metrics.run_backtest_detailed`.
본문·툴팁 폰트는 `gui_helpers.gui_body_font()`(13pt)로 통일, `set_widget_scaling`/`set_window_scaling` 1.0 고정.
"""
from __future__ import annotations

import os
import threading
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

from PIL import Image
from tkcalendar import DateEntry

from src.data_loader import (
    default_backtest_period_range,
    fetch_filtered_universe,
)
from src.gui_helpers import (
    HoverTooltip,
    gui_body_font,
    GUI_FONT_FAMILY,
    GUI_FONT_SIZE,
    apply_yaml_to_widgets,
    date_entry_theme_kw,
    gui_summary_five_lines,
    trading_rules_static_text,
    try_build_config,
)
from src.backtest_constants import TREND_MA_PERIODS
from src.metrics import BacktestResult, run_backtest_detailed

# ==========================================
# [최상단 전역 변수 설정 구역] - 완벽히 정돈됨
# ==========================================
FIXED_PANEL_H = 780   # 좌측 입력 패널 고정 세로 높이
FIXED_RIGHT_PANEL_H = 780  # 우측 패널 고정 세로 높이를 좌측과 통일

FIXED_LEFT_W = 290    # 왼쪽 입력 패널의 고정 가로 폭
FIXED_RIGHT_W = 1050  # 오른쪽 차트 패널의 고정 가로 폭

FIXED_RULES_TEXT_H = 100  # 우측 하단 참고 문구(읽기 전용) 높이

FIXED_CHART_W = 1020  # 실제 캔들 차트 이미지의 고정 가로 폭
FIXED_CHART_H = 490   # 우측 차트 높이를 줄여서 좌측 입력 섹션 하단을 넘지 않도록 조절 (수익률 그래프 잘림 방지)

# 시간축: 좌패널 ±30일(달력) · 차트 오버레이 투명 버튼 ±7영업일
TIME_AXIS_SHIFT_DAYS = 30
TIME_AXIS_PAN_BDAY = 7
# 차트 이미지 위 좌·우 클릭 영역 (px, place)
CHART_NAV_STRIP_W = 50
DATE_CLAMP_MIN = date(1990, 1, 1)


class BacktestGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        gui_body_font()  # CTkFont — Tk 루트 존재 후 캐시(모듈 import 시 생성 불가)

        self.title("BackTesterKRX v4.6")

        self._candidates: list[tuple[str, str]] = []
        self._busy = False
        self._img_ref: ctk.CTkImage | None = None
        self._last_chart_path: str | None = None
        self._chart_resize_after_id: str | None = None
        self._shift_auto_run_after_id: str | None = None

        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=0)
        self.grid_rowconfigure(0, weight=0)

        left = ctk.CTkFrame(
            self, corner_radius=10, width=FIXED_LEFT_W, height=FIXED_PANEL_H
        )  # 🎯 가로 320, 세로 780 고정
        left.grid(
            row=0, column=0, sticky="nw", padx=(12, 6), pady=(12, 6)
        )  # sticky에서 nsew를 빼고 nw(좌측상단 정렬)로 변경
        left.grid_propagate(False)  # 중요: 내부 컴포넌트 때문에 프레임 크기가 변하는 걸 막음

        row_search = ctk.CTkFrame(left, fg_color="transparent")
        row_search.pack(fill="x", padx=14, pady=(12, 6))
        row_search.grid_columnconfigure(1, weight=1)

        sf_market = ctk.CTkFrame(row_search, fg_color="transparent")
        sf_market.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(sf_market, text="시장", font=gui_body_font()).pack(side="left", padx=(0, 4))
        self.var_market = ctk.StringVar(value="KOSPI")
        ctk.CTkOptionMenu(
            sf_market,
            values=["KOSPI", "KOSDAQ"],
            variable=self.var_market,
            width=76,
            font=gui_body_font(),
        ).pack(side="left")

        sf_kw = ctk.CTkFrame(row_search, fg_color="transparent")
        sf_kw.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ctk.CTkLabel(sf_kw, text="종목", font=gui_body_font()).pack(side="left", padx=(0, 4))
        self.var_keyword = ctk.StringVar(value="삼성")
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

        ctk.CTkLabel(left, text="조회 주기", font=gui_body_font()).pack(anchor="w", padx=14)
        self.var_interval = ctk.StringVar(value="daily")
        rf = ctk.CTkFrame(left, fg_color="transparent")
        rf.pack(anchor="w", padx=14, pady=(0, 6))
        ctk.CTkRadioButton(
            rf, text="일봉", variable=self.var_interval, value="daily", font=gui_body_font()
        ).pack(side="left", padx=(0, 12))
        ctk.CTkRadioButton(
            rf, text="주봉", variable=self.var_interval, value="weekly", font=gui_body_font()
        ).pack(side="left")

        row_dt = ctk.CTkFrame(left, fg_color="transparent")
        row_dt.pack(fill="x", padx=14, pady=(0, 6))
        row_dt.grid_columnconfigure(0, weight=92)
        row_dt.grid_columnconfigure(1, weight=92)
        row_dt.grid_columnconfigure(2, weight=78)
        d0 = ctk.CTkFrame(row_dt, fg_color="transparent")
        d0.grid(row=0, column=0, sticky="ew", padx=(0, 2))
        d1 = ctk.CTkFrame(row_dt, fg_color="transparent")
        d1.grid(row=0, column=1, sticky="ew", padx=(2, 2))
        d2 = ctk.CTkFrame(row_dt, fg_color="transparent")
        d2.grid(row=0, column=2, sticky="ew", padx=(2, 0))
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
        ctk.CTkLabel(d2, text="가상 원금(원)", font=gui_body_font()).pack(anchor="w")
        self.var_cash = ctk.StringVar(value="5000000")
        ctk.CTkEntry(
            d2,
            textvariable=self.var_cash,
            width=70,
            height=28,
            font=gui_body_font(),
        ).pack(fill="x", pady=(2, 0))

        row_axis = ctk.CTkFrame(left, fg_color="transparent")
        row_axis.pack(fill="x", padx=14, pady=(0, 8))
        row_axis.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkLabel(
            row_axis,
            text="시간축 이동 (±30일)",
            font=gui_body_font(),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
        ctk.CTkButton(
            row_axis,
            text="◀ 1달 전",
            height=30,
            font=gui_body_font(),
            command=lambda: self._on_shift_period_days(-TIME_AXIS_SHIFT_DAYS),
        ).grid(row=1, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(
            row_axis,
            text="1달 후 ▶",
            height=30,
            font=gui_body_font(),
            command=lambda: self._on_shift_period_days(TIME_AXIS_SHIFT_DAYS),
        ).grid(row=1, column=1, sticky="ew", padx=(6, 0))

        self._trend_vars: dict[int, ctk.BooleanVar] = {
            p: ctk.BooleanVar(value=(p in (20, 120))) for p in TREND_MA_PERIODS
        }

        row_ma = ctk.CTkFrame(left, fg_color="transparent")
        row_ma.pack(fill="x", padx=14, pady=(0, 6))
        ctk.CTkLabel(row_ma, text="매매 기준 이평선", font=gui_body_font()).pack(anchor="w")
        rf_ma = ctk.CTkFrame(row_ma, fg_color="transparent")
        rf_ma.pack(fill="x", pady=(4, 0))
        self.var_ma_period = ctk.StringVar(value="20")
        for val in ("5", "10", "20"):
            ctk.CTkRadioButton(
                rf_ma,
                text=f"{val}일선",
                variable=self.var_ma_period,
                value=val,
                font=gui_body_font(),
            ).pack(side="left", padx=(0, 14))

        ctk.CTkLabel(
            left,
            text="추세선 표시 (차트 오버레이)",
            font=gui_body_font(),
        ).pack(anchor="w", padx=14, pady=(4, 2))
        trend_grid = ctk.CTkFrame(left, fg_color="transparent")
        trend_grid.pack(fill="x", padx=14, pady=(0, 6))
        trend_grid.grid_columnconfigure((0, 1, 2), weight=1)
        trend_positions = [
            (5, 0, 0),
            (10, 0, 1),
            (20, 0, 2),
            (60, 1, 0),
            (120, 1, 1),
            (200, 1, 2),
        ]
        for p, r, c in trend_positions:
            ctk.CTkCheckBox(
                trend_grid,
                text=f"{p}일선",
                variable=self._trend_vars[p],
                font=gui_body_font(),
            ).grid(row=r, column=c, sticky="w", padx=4, pady=2)

        ctk.CTkLabel(
            left,
            text="차트 표시 지표 선택 (중복 가능)",
            font=gui_body_font(),
        ).pack(anchor="w", padx=14, pady=(4, 4))
        row_ind = ctk.CTkFrame(left, fg_color="transparent")
        row_ind.pack(fill="x", padx=14, pady=(0, 4))
        self.var_show_candle = ctk.BooleanVar(value=True)
        self.var_show_volume = ctk.BooleanVar(value=True)
        self.var_show_revenue = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            row_ind,
            text="캔들 차트",
            variable=self.var_show_candle,
            font=gui_body_font(),
        ).pack(side="left", padx=(0, 10))
        ctk.CTkCheckBox(
            row_ind,
            text="거래량",
            variable=self.var_show_volume,
            font=gui_body_font(),
        ).pack(side="left", padx=(0, 10))
        ctk.CTkCheckBox(
            row_ind,
            text="수익률",
            variable=self.var_show_revenue,
            font=gui_body_font(),
        ).pack(side="left")

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
        self.var_filter_breakout = ctk.BooleanVar(value=False)
        self.var_filter_timebuf = ctk.BooleanVar(value=False)

        self.var_golden_buy = ctk.BooleanVar(value=True)
        self.var_dead_sell = ctk.BooleanVar(value=True)

        self.var_trailing_stop = ctk.BooleanVar(value=False)
        self.var_trailing_reference_pct = ctk.StringVar(value="10")
        self.var_trailing_drop_below_pct = ctk.StringVar(value="3.0")
        self.var_trailing_drop_above_pct = ctk.StringVar(value="5.0")

        right = ctk.CTkFrame(
            self, corner_radius=10, width=FIXED_RIGHT_W, height=FIXED_RIGHT_PANEL_H
        )
        right.grid(row=0, column=1, sticky="nw", padx=(6, 12), pady=(12, 6))
        right.grid_propagate(False)
        right.grid_rowconfigure(0, weight=0)  # 매매 규칙(전략 옵션)
        right.grid_rowconfigure(1, weight=0)  # 차트 컨트롤 패널
        right.grid_rowconfigure(2, weight=0)  # 차트
        right.grid_rowconfigure(3, weight=0)  # 설명 텍스트
        right.grid_columnconfigure(0, weight=1)

        rules_panel = ctk.CTkFrame(
            right,
            corner_radius=8,
            border_width=1,
            border_color=("gray65", "gray45"),
            fg_color=("gray92", "gray18"),
        )
        rules_panel.grid(row=0, column=0, sticky="ew", padx=14, pady=(8, 8))

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
        self.chart_control_panel.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 6))

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
            command=lambda: self._on_chart_pan_bdays(-30),
        )
        self.btn_fast_rewind.pack(side="left", padx=6)
        HoverTooltip(self.btn_fast_rewind, "30영업일 전으로 이동 (-30d)")

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
            command=lambda: self._on_chart_pan_bdays(-7),
        )
        self.btn_prev_7.pack(side="left", padx=6)
        HoverTooltip(self.btn_prev_7, "7영업일 전으로 이동 (-7d)")

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
            command=lambda: self._on_chart_pan_bdays(7),
        )
        self.btn_next_7.pack(side="left", padx=6)
        HoverTooltip(self.btn_next_7, "7영업일 후로 이동 (+7d)")

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
            command=lambda: self._on_chart_pan_bdays(30),
        )
        self.btn_fast_forward.pack(side="left", padx=6)
        HoverTooltip(self.btn_fast_forward, "30영업일 후로 이동 (+30d)")

        # 현재 기간 표시 라벨 추가 (플레이 버튼 우측)
        self.lbl_current_period = ctk.CTkLabel(
            btn_container,
            text="",
            font=ctk.CTkFont(family=GUI_FONT_FAMILY, size=GUI_FONT_SIZE, weight="bold"),
            text_color=("gray25", "gray75"),
        )
        self.lbl_current_period.pack(side="left", padx=(18, 6))

        self.chart_frame = ctk.CTkFrame(
            right, fg_color=("gray95", "gray17"), width=FIXED_CHART_W, height=FIXED_CHART_H
        )
        self.chart_frame.grid(row=2, column=0, sticky="nw", padx=14, pady=(0, 8))

        self.chart_frame.grid_propagate(False)
        self.chart_frame.grid_rowconfigure(0, weight=1)
        self.chart_frame.grid_columnconfigure(0, weight=1)

        # 차트 컨테이너: 이미지 전체
        self.chart_overlay_host = ctk.CTkFrame(
            self.chart_frame, fg_color="transparent"
        )
        self.chart_overlay_host.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

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
        self._sync_buy_filters_interlock()

    def _refresh_trading_rules_display(self, *_args: object) -> None:
        """우측 매매 규칙 패널(읽기 전용 텍스트) - 제거됨."""
        pass

    def _apply_maximized_geometry(self) -> None:
        try:
            self.state("zoomed")
        except tk.TclError:
            try:
                self.attributes("-zoomed", True)
            except tk.TclError:
                self.geometry("1400x900")

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
            self.chart_frame.update_idletasks()
            self.lbl_chart.update_idletasks()
            fw = int(self.lbl_chart.winfo_width())
            fh = int(self.lbl_chart.winfo_height())
            # 그리드 직후 등 유효 크기 없을 때: 차트 패딩만 반영한 추정
            if fw <= 10 or fh <= 10:
                fw = max(400, FIXED_CHART_W - 16)
                fh = FIXED_CHART_H - 12

            with Image.open(image_path) as pil_img:
                resized = pil_img.resize((fw, fh), Image.Resampling.LANCZOS)

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

    def _update_period_label(self) -> None:
        try:
            sd = self._date_start.get_date()
            ed = self._date_end.get_date()
            self.lbl_current_period.configure(
                text=f"조회 기간: {sd.strftime('%Y-%m-%d')} ~ {ed.strftime('%Y-%m-%d')}"
            )
        except Exception:
            pass

    def _shift_period_calendar_days(self, delta_days: int) -> None:
        """시작·종료를 같은 일수만큼 평행 이동. 종료가 오늘을 넘으면 창 길이 유지하며 오늘에 맞춤."""
        try:
            sd = self._date_start.get_date()
            ed = self._date_end.get_date()
        except (ValueError, tk.TclError):
            return
        span = max(0, (ed - sd).days)
        today = date.today()
        ns = sd + timedelta(days=delta_days)
        ne = ed + timedelta(days=delta_days)
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

    def _on_shift_period_days(self, delta_days: int) -> None:
        self._shift_period_calendar_days(delta_days)
        self._schedule_auto_run_after_shift()

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
        cfg = try_build_config(self, silent=True)
        if cfg is None:
            self.lbl_status.configure(
                text="시간축 이동됨 · 리스트에서 종목 선택 또는 settings.yaml 의 universe.selected_code 를 확인하세요.",
            )
            return
        self._run_backtest(cfg)

    def _on_chart_pan_bdays(self, delta_bdays: int) -> None:
        """차트 좌·우 오버레이: 영업일 기준으로 기간 이동 후 자동 재실행."""
        self._shift_period_trading_days(delta_bdays)
        self._schedule_auto_run_after_shift()

    def _run_backtest(self, cfg: dict | None) -> None:
        if cfg is None or self._busy:
            return
        self._busy = True
        self._update_period_label()
        self.btn_run.configure(state="disabled", text="계산 중…")
        self.lbl_status.configure(text="백테스트 계산 중…")

        def work():
            try:
                res = run_backtest_detailed(cfg)
                self.after(0, lambda: self._finish_run(res))
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
                self.after(0, lambda: self._finish_run(err_res))

        threading.Thread(target=work, daemon=True).start()

    def _on_search(self) -> None:
        m = self.var_market.get().strip() or "KOSPI"
        kw = self.var_keyword.get().strip()
        try:
            d = fetch_filtered_universe(m, kw)
        except Exception as e:
            messagebox.showerror("검색 실패", str(e))
            return
        self.list_codes.delete(0, tk.END)
        self._candidates = sorted(d.items(), key=lambda x: x[0])
        for code, name in self._candidates:
            self.list_codes.insert(tk.END, f"{code}  {name}")

    def _on_run(self):
        self._run_backtest(try_build_config(self, silent=False))

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
