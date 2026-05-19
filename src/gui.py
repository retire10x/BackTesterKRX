"""
데스크톱 GUI (CustomTkinter).
차트: output/backtest_report.png → CTkImage (범례·매매 규칙 패널 + 차트).
YAML·설정 dict·툴팁: `gui_helpers`. 엔진: `src.metrics.run_backtest_detailed`.
"""
from __future__ import annotations

import os
import threading
import tkinter as tk
from datetime import date, timedelta
from tkinter import messagebox

import customtkinter as ctk
from PIL import Image
from tkcalendar import DateEntry

from src.data_loader import (
    default_backtest_period_range,
    fetch_filtered_universe,
)
from src.gui_helpers import (
    HoverTooltip,
    apply_yaml_to_widgets,
    date_entry_theme_kw,
    gui_summary_five_lines,
    trading_rules_static_text,
    try_build_config,
)
from src.backtest_constants import TREND_MA_COLORS, TREND_MA_PERIODS
from src.metrics import BacktestResult, run_backtest_detailed

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")

# ==========================================
# [최상단 전역 변수 설정 구역] - 완벽히 정돈됨
# ==========================================
FIXED_PANEL_H = 780   # 좌측 입력 패널 고정 세로 높이
FIXED_RIGHT_PANEL_H = 1170  # 우측: 범례 + 매매 규칙 섹션 + 차트·설명

FIXED_LEFT_W = 320    # 왼쪽 입력 패널의 고정 가로 폭
FIXED_RIGHT_W = 1050  # 오른쪽 차트 패널의 고정 가로 폭

FIXED_RULES_TEXT_H = 100  # 우측 하단 참고 문구(읽기 전용) 높이

FIXED_CHART_W = 1020  # 실제 캔들 차트 이미지의 고정 가로 폭
FIXED_CHART_H = 730   # 우측 하단 공백 청산 — 차트 세로 확장

# 시간축 버튼: ±30일 · 차트 휠만 7일 스텝
TIME_AXIS_SHIFT_DAYS = 30
TIME_AXIS_WHEEL_DAYS = 7
DATE_CLAMP_MIN = date(1990, 1, 1)


class BacktestGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("BackTesterKRX v4.4")

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

        ctk.CTkLabel(
            left, text="입력", font=ctk.CTkFont(size=18, weight="bold")
        ).pack(anchor="w", padx=14, pady=(12, 6))

        row_search = ctk.CTkFrame(left, fg_color="transparent")
        row_search.pack(fill="x", padx=14, pady=(0, 6))
        row_search.grid_columnconfigure(1, weight=1)

        sf_market = ctk.CTkFrame(row_search, fg_color="transparent")
        sf_market.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(sf_market, text="시장").pack(side="left", padx=(0, 4))
        self.var_market = ctk.StringVar(value="KOSPI")
        ctk.CTkOptionMenu(
            sf_market,
            values=["KOSPI", "KOSDAQ"],
            variable=self.var_market,
            width=92,
        ).pack(side="left")

        sf_kw = ctk.CTkFrame(row_search, fg_color="transparent")
        sf_kw.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ctk.CTkLabel(sf_kw, text="종목").pack(side="left", padx=(0, 4))
        self.var_keyword = ctk.StringVar(value="삼성")
        ctk.CTkEntry(sf_kw, textvariable=self.var_keyword, height=28).pack(
            side="left", fill="x", expand=True
        )

        ctk.CTkButton(
            row_search,
            text="검색",
            width=72,
            height=28,
            command=self._on_search,
        ).grid(row=0, column=2, sticky="e")

        ctk.CTkLabel(left, text="검색 결과 (1개만 선택)").pack(anchor="w", padx=14)
        list_frame = ctk.CTkFrame(left, fg_color="transparent")
        list_frame.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        self.list_codes = tk.Listbox(
            list_frame,
            height=7,
            font=("Segoe UI", 11),
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

        ctk.CTkLabel(left, text="조회 주기").pack(anchor="w", padx=14)
        self.var_interval = ctk.StringVar(value="daily")
        rf = ctk.CTkFrame(left, fg_color="transparent")
        rf.pack(anchor="w", padx=14, pady=(0, 6))
        ctk.CTkRadioButton(
            rf, text="일봉", variable=self.var_interval, value="daily"
        ).pack(side="left", padx=(0, 12))
        ctk.CTkRadioButton(
            rf, text="주봉", variable=self.var_interval, value="weekly"
        ).pack(side="left")

        row_dt = ctk.CTkFrame(left, fg_color="transparent")
        row_dt.pack(fill="x", padx=14, pady=(0, 6))
        row_dt.grid_columnconfigure((0, 1, 2), weight=1, uniform="dt")
        d0 = ctk.CTkFrame(row_dt, fg_color="transparent")
        d0.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        d1 = ctk.CTkFrame(row_dt, fg_color="transparent")
        d1.grid(row=0, column=1, sticky="ew", padx=(4, 4))
        d2 = ctk.CTkFrame(row_dt, fg_color="transparent")
        d2.grid(row=0, column=2, sticky="ew", padx=(4, 0))
        ctk.CTkLabel(d0, text="시작일").pack(anchor="w")
        self._date_start = DateEntry(
            d0,
            width=10,
            date_pattern="yyyy-mm-dd",
            **date_entry_theme_kw(),
        )
        _ds, _de = default_backtest_period_range()
        self._date_start.set_date(_ds)
        self._date_start.pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(d1, text="종료일").pack(anchor="w")
        self._date_end = DateEntry(
            d1,
            width=10,
            date_pattern="yyyy-mm-dd",
            **date_entry_theme_kw(),
        )
        self._date_end.set_date(_de)
        self._date_end.pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(d2, text="가상 원금(원)").pack(anchor="w")
        self.var_cash = ctk.StringVar(value="5000000")
        ctk.CTkEntry(d2, textvariable=self.var_cash, height=28).pack(
            fill="x", pady=(2, 0)
        )

        row_axis = ctk.CTkFrame(left, fg_color="transparent")
        row_axis.pack(fill="x", padx=14, pady=(0, 8))
        row_axis.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkLabel(row_axis, text="시간축 이동 (±30일)", font=ctk.CTkFont(size=12)).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 4)
        )
        ctk.CTkButton(
            row_axis,
            text="◀ 1달 전",
            height=30,
            command=lambda: self._on_shift_period_days(-TIME_AXIS_SHIFT_DAYS),
        ).grid(row=1, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(
            row_axis,
            text="1달 후 ▶",
            height=30,
            command=lambda: self._on_shift_period_days(TIME_AXIS_SHIFT_DAYS),
        ).grid(row=1, column=1, sticky="ew", padx=(6, 0))

        self._trend_vars: dict[int, ctk.BooleanVar] = {
            p: ctk.BooleanVar(value=(p in (20, 120))) for p in TREND_MA_PERIODS
        }

        row_ma = ctk.CTkFrame(left, fg_color="transparent")
        row_ma.pack(fill="x", padx=14, pady=(0, 6))
        ctk.CTkLabel(row_ma, text="매매 기준 이평선").pack(anchor="w")
        rf_ma = ctk.CTkFrame(row_ma, fg_color="transparent")
        rf_ma.pack(fill="x", pady=(4, 0))
        self.var_ma_period = ctk.StringVar(value="20")
        for val in ("5", "10", "20"):
            ctk.CTkRadioButton(
                rf_ma,
                text=f"{val}일선",
                variable=self.var_ma_period,
                value=val,
            ).pack(side="left", padx=(0, 14))

        ctk.CTkLabel(
            left,
            text="추세선 표시 (차트 오버레이)",
            font=ctk.CTkFont(size=13, weight="bold"),
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
            ).grid(row=r, column=c, sticky="w", padx=4, pady=2)

        ctk.CTkLabel(
            left,
            text="차트 표시 지표 선택 (중복 가능)",
            font=ctk.CTkFont(size=13, weight="bold"),
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
        ).pack(side="left", padx=(0, 10))
        ctk.CTkCheckBox(
            row_ind,
            text="거래량",
            variable=self.var_show_volume,
        ).pack(side="left", padx=(0, 10))
        ctk.CTkCheckBox(
            row_ind,
            text="수익률",
            variable=self.var_show_revenue,
        ).pack(side="left")

        row_run = ctk.CTkFrame(left, fg_color="transparent")
        row_run.pack(fill="x", padx=14, pady=(8, 8))
        self.btn_run = ctk.CTkButton(
            row_run,
            text="백테스트 실행",
            height=40,
            font=ctk.CTkFont(size=15, weight="bold"),
            command=self._on_run,
        )
        self.btn_run.pack(fill="x")

        self.text_summary = ctk.CTkTextbox(
            left,
            height=128,
            font=ctk.CTkFont(size=13),
            wrap="word",
        )
        self.text_summary.pack(fill="both", expand=False, padx=14, pady=(0, 14))
        self.text_summary.configure(state="disabled")

        self.var_filter_trend = ctk.BooleanVar(value=False)
        self.var_slope_threshold = ctk.StringVar(value="0.01")
        self.var_filter_breakout = ctk.BooleanVar(value=False)
        self.var_filter_timebuf = ctk.BooleanVar(value=False)

        self.var_trailing_stop = ctk.BooleanVar(value=False)
        self.var_trailing_reference_pct = ctk.StringVar(value="10")
        self.var_trailing_drop_below_pct = ctk.StringVar(value="3.0")
        self.var_trailing_drop_above_pct = ctk.StringVar(value="5.0")

        right = ctk.CTkFrame(
            self, corner_radius=10, width=FIXED_RIGHT_W, height=FIXED_RIGHT_PANEL_H
        )
        right.grid(row=0, column=1, sticky="nw", padx=(6, 12), pady=(12, 6))
        right.grid_propagate(False)
        right.grid_rowconfigure(0, weight=0)  # 이평 범례
        right.grid_rowconfigure(1, weight=0)  # 매매 규칙(전략 옵션)
        right.grid_rowconfigure(2, weight=0)  # 차트
        right.grid_rowconfigure(3, weight=0)  # 설명 텍스트
        right.grid_columnconfigure(0, weight=1)

        # ── 차트 위: 추세 이평 범례만 (한 줄·차트 폭 활용)
        legend_bar = ctk.CTkFrame(right, fg_color="transparent")
        legend_bar.grid(row=0, column=0, sticky="ew", padx=14, pady=(8, 6))
        ctk.CTkLabel(
            legend_bar,
            text="차트 범례 · 추세 이평",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).pack(anchor="w", pady=(0, 4))
        legend_row = ctk.CTkFrame(legend_bar, fg_color="transparent")
        legend_row.pack(fill="x", anchor="w")
        for p in TREND_MA_PERIODS:
            cell = ctk.CTkFrame(legend_row, fg_color="transparent")
            cell.pack(side="left", padx=(0, 14), pady=2)
            ctk.CTkLabel(
                cell,
                text="",
                width=18,
                height=5,
                fg_color=TREND_MA_COLORS[p],
                corner_radius=2,
            ).pack(side="left", padx=(0, 6))
            ctk.CTkLabel(
                cell,
                text=f"{p}일선",
                font=ctk.CTkFont(size=11),
            ).pack(side="left")

        tt_trend = (
            "당일 종가가 120일선 위에 있고, 최근 5거래일간 120일선의 선형 회귀 기울기(Slope)가 "
            "설정된 임계값(Threshold) 이상인 양수(+)일 때만 매수 진입"
        )
        tt_breakout = (
            "당일 거래량 > 직전 5봉 평균 거래량 × 1.5 또는 종가 > 당일 20일선 × 1.02"
        )
        tt_timebuf = (
            "돌파 당일(i) 바로 진입하지 말고, i+1, i+2 봉의 종가까지 20일선 위에 안착 확인 후 진입"
        )
        tt_slope = (
            "대세 상승 필터 전용: 120일선 선형회귀 기울기(최근 5봉·OLS β₁) 최소값."
        )

        rules_panel = ctk.CTkFrame(
            right,
            corner_radius=8,
            border_width=1,
            border_color=("gray65", "gray45"),
            fg_color=("gray92", "gray18"),
        )
        rules_panel.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 8))

        rules_head = ctk.CTkFrame(rules_panel, fg_color="transparent")
        rules_head.pack(fill="x", padx=10, pady=(10, 6))
        ctk.CTkLabel(
            rules_head,
            text="매매 규칙 · 전략 옵션",
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            rules_head,
            text="아래 설정은 YAML·CLI와 동기화되며, 규칙 추가 시 이 영역을 확장하면 됩니다.",
            font=ctk.CTkFont(size=11),
            text_color=("gray35", "gray60"),
            anchor="w",
        ).pack(anchor="w", pady=(2, 0))

        row_entry_filters = ctk.CTkFrame(rules_panel, fg_color="transparent")
        row_entry_filters.pack(fill="x", padx=10, pady=(4, 6))
        ctk.CTkLabel(
            row_entry_filters,
            text="매수 진입 필터",
            font=ctk.CTkFont(size=12, weight="bold"),
            width=118,
            anchor="w",
        ).pack(side="left", padx=(0, 14))
        rf_inner = ctk.CTkFrame(row_entry_filters, fg_color="transparent")
        rf_inner.pack(side="left", fill="x", expand=True)

        cb_trend = ctk.CTkCheckBox(
            rf_inner,
            text="대세 상승 필터",
            variable=self.var_filter_trend,
            font=ctk.CTkFont(size=11),
            checkbox_width=18,
            checkbox_height=18,
        )
        cb_trend.pack(side="left")
        cb_breakout = ctk.CTkCheckBox(
            rf_inner,
            text="돌파 강도 필터",
            variable=self.var_filter_breakout,
            font=ctk.CTkFont(size=11),
            checkbox_width=18,
            checkbox_height=18,
        )
        cb_breakout.pack(side="left", padx=(12, 0))
        cb_timebuf = ctk.CTkCheckBox(
            rf_inner,
            text="시간 버퍼 필터",
            variable=self.var_filter_timebuf,
            font=ctk.CTkFont(size=11),
            checkbox_width=18,
            checkbox_height=18,
        )
        cb_timebuf.pack(side="left", padx=(12, 0))

        ctk.CTkLabel(
            rf_inner,
            text="Slope 임계값:",
            font=ctk.CTkFont(size=11),
        ).pack(side="left", padx=(18, 4))

        def _bump_slope(delta: float) -> None:
            try:
                v = float(str(self.var_slope_threshold.get()).replace(",", "").strip())
            except ValueError:
                v = 0.01
            v = max(0.0001, min(1.0, v + delta))
            s = f"{v:.4f}".rstrip("0").rstrip(".")
            self.var_slope_threshold.set(s or "0")

        slope_spin = ctk.CTkFrame(rf_inner, fg_color="transparent")
        slope_spin.pack(side="left")
        ctk.CTkButton(
            slope_spin,
            text="▴",
            width=22,
            height=24,
            font=ctk.CTkFont(size=10),
            corner_radius=3,
            command=lambda: _bump_slope(0.01),
        ).pack(side="left", padx=(0, 2))
        self.entry_slope_threshold = ctk.CTkEntry(
            slope_spin,
            width=56,
            height=24,
            font=ctk.CTkFont(size=11),
            textvariable=self.var_slope_threshold,
        )
        self.entry_slope_threshold.pack(side="left")
        ctk.CTkButton(
            slope_spin,
            text="▾",
            width=22,
            height=24,
            font=ctk.CTkFont(size=10),
            corner_radius=3,
            command=lambda: _bump_slope(-0.01),
        ).pack(side="left", padx=(2, 0))

        row_exit_rules = ctk.CTkFrame(rules_panel, fg_color="transparent")
        row_exit_rules.pack(fill="x", padx=10, pady=(6, 10))
        ctk.CTkLabel(
            row_exit_rules,
            text="청산 보조",
            font=ctk.CTkFont(size=12, weight="bold"),
            width=118,
            anchor="w",
        ).pack(side="left", padx=(0, 14), anchor="n", pady=(2, 0))
        trailing_inner = ctk.CTkFrame(row_exit_rules, fg_color="transparent")
        trailing_inner.pack(side="left", fill="x", expand=True)

        trailing_line1 = ctk.CTkFrame(trailing_inner, fg_color="transparent")
        trailing_line1.pack(fill="x")
        cb_trailing = ctk.CTkCheckBox(
            trailing_line1,
            text="가변 낙폭 매도 (v4.4 고점 대비 트레일)",
            variable=self.var_trailing_stop,
            font=ctk.CTkFont(size=11),
            checkbox_width=18,
            checkbox_height=18,
        )
        cb_trailing.pack(side="left")
        trailing_line2 = ctk.CTkFrame(trailing_inner, fg_color="transparent")
        trailing_line2.pack(fill="x", pady=(8, 0))
        indent = ctk.CTkFrame(trailing_line2, fg_color="transparent")
        indent.pack(fill="x", padx=(22, 0))
        ctk.CTkLabel(indent, text="기준", font=ctk.CTkFont(size=11)).pack(
            side="left", padx=(0, 4)
        )
        self.entry_trailing_ref = ctk.CTkEntry(
            indent,
            width=48,
            height=26,
            font=ctk.CTkFont(size=11),
            textvariable=self.var_trailing_reference_pct,
        )
        self.entry_trailing_ref.pack(side="left", padx=(0, 4))
        ctk.CTkLabel(indent, text="% 피크 수익률", font=ctk.CTkFont(size=11)).pack(
            side="left", padx=(0, 16)
        )
        ctk.CTkLabel(indent, text="미달 시 고점 대비", font=ctk.CTkFont(size=11)).pack(
            side="left", padx=(0, 4)
        )
        self.entry_trailing_below = ctk.CTkEntry(
            indent,
            width=48,
            height=26,
            font=ctk.CTkFont(size=11),
            textvariable=self.var_trailing_drop_below_pct,
        )
        self.entry_trailing_below.pack(side="left", padx=(0, 4))
        ctk.CTkLabel(indent, text="% 하락 청산", font=ctk.CTkFont(size=11)).pack(
            side="left", padx=(0, 16)
        )
        ctk.CTkLabel(indent, text="돌파 시 고점 대비", font=ctk.CTkFont(size=11)).pack(
            side="left", padx=(0, 4)
        )
        self.entry_trailing_above = ctk.CTkEntry(
            indent,
            width=48,
            height=26,
            font=ctk.CTkFont(size=11),
            textvariable=self.var_trailing_drop_above_pct,
        )
        self.entry_trailing_above.pack(side="left", padx=(0, 4))
        ctk.CTkLabel(indent, text="% 하락 청산", font=ctk.CTkFont(size=11)).pack(
            side="left"
        )

        def _trailing_tooltip_body() -> str:
            try:
                g = float(
                    str(self.var_trailing_reference_pct.get())
                    .replace(",", "")
                    .strip()
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
                f"매수 이후 최고가 기준 수익률이 {g_s}% 미만일 때는 최고가 대비 {b_s}% 하락 시 조기 청산, "
                f"{g_s}% 이상 도달했었을 때는 {a_s}% 하락 시 청산하여 대시세 수익을 보존함."
            )

        HoverTooltip(cb_trend, tt_trend)
        HoverTooltip(self.entry_slope_threshold, tt_slope)
        HoverTooltip(cb_breakout, tt_breakout)
        HoverTooltip(cb_timebuf, tt_timebuf)
        HoverTooltip(cb_trailing, _trailing_tooltip_body)
        HoverTooltip(self.entry_trailing_ref, _trailing_tooltip_body)
        HoverTooltip(self.entry_trailing_below, _trailing_tooltip_body)
        HoverTooltip(self.entry_trailing_above, _trailing_tooltip_body)

        self.chart_frame = ctk.CTkFrame(
            right, fg_color=("gray95", "gray17"), width=FIXED_CHART_W, height=FIXED_CHART_H
        )
        self.chart_frame.grid(row=2, column=0, sticky="nw", padx=14, pady=(0, 8))

        self.text_trading_rules = ctk.CTkTextbox(
            right,
            height=FIXED_RULES_TEXT_H,
            font=ctk.CTkFont(size=13),
            wrap="word",
        )
        self.text_trading_rules.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 14))

        self.chart_frame.grid_propagate(False)
        self.chart_frame.grid_rowconfigure(0, weight=1)
        self.chart_frame.grid_columnconfigure(0, weight=1)

        self.lbl_chart = ctk.CTkLabel(
            self.chart_frame,
            text="백테스트 실행 후 차트가 표시됩니다.",
            fg_color="transparent",
        )
        self.lbl_chart.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        self.chart_frame.bind("<Configure>", self._on_chart_frame_configure)
        self._bind_chart_mousewheel()

        apply_yaml_to_widgets(self)
        self._refresh_trading_rules_display()
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
            font=ctk.CTkFont(size=12),
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

    def _refresh_trading_rules_display(self, *_args: object) -> None:
        """우측 매매 규칙 패널(읽기 전용 텍스트). 매매 이평·조회 주기 변경 시 갱신."""
        try:
            ma_n = int(self.var_ma_period.get())
        except ValueError:
            ma_n = 20
        if ma_n not in (5, 10, 20):
            ma_n = 20
        interval = (self.var_interval.get() or "daily").strip().lower()
        ts_en = bool(self.var_trailing_stop.get())
        try:
            t_ref = float(
                str(self.var_trailing_reference_pct.get()).replace(",", "").strip()
            )
            t_bel = float(
                str(self.var_trailing_drop_below_pct.get()).replace(",", "").strip()
            )
            t_abv = float(
                str(self.var_trailing_drop_above_pct.get()).replace(",", "").strip()
            )
        except ValueError:
            t_ref, t_bel, t_abv = 10.0, 3.0, 5.0
        body = trading_rules_static_text(
            ma_n,
            interval,
            trailing_stop_enabled=ts_en,
            trailing_hinge_pct=t_ref,
            trailing_below_drop_pct=t_bel,
            trailing_above_drop_pct=t_abv,
        )
        tb = self.text_trading_rules
        tb.configure(state="normal")
        tb.delete("1.0", "end")
        tb.insert("1.0", body)
        tb.configure(state="disabled")

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
            # 💡 실시간 계산 식을 모두 지우고, 최상단 전역 변수 값으로 강제 고정합니다.
            fw = FIXED_CHART_W
            fh = FIXED_CHART_H

            pil_img = Image.open(image_path)

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
                text="시간축 이동됨 · 종목을 선택한 뒤 갱신됩니다."
            )
            return
        self._run_backtest(cfg)

    def _on_chart_mousewheel(self, event: tk.Event) -> None:
        """차트 영역 휠: 위=과거, 아래=미래 (PNG 차트이므로 mpl scroll_event 대신 Tk 바인딩)."""
        delta_days: int
        if getattr(event, "delta", 0):
            steps = max(1, abs(int(event.delta)) // 120)
            chunk = TIME_AXIS_WHEEL_DAYS * steps
            delta_days = -chunk if int(event.delta) > 0 else chunk
        elif getattr(event, "num", None) == 4:
            delta_days = -TIME_AXIS_WHEEL_DAYS
        elif getattr(event, "num", None) == 5:
            delta_days = TIME_AXIS_WHEEL_DAYS
        else:
            return
        self._shift_period_calendar_days(delta_days)
        self._schedule_auto_run_after_shift()

    def _bind_chart_mousewheel(self) -> None:
        for w in (self.chart_frame, self.lbl_chart):
            w.bind("<MouseWheel>", self._on_chart_mousewheel)
            w.bind("<Button-4>", self._on_chart_mousewheel)
            w.bind("<Button-5>", self._on_chart_mousewheel)

    def _run_backtest(self, cfg: dict | None) -> None:
        if cfg is None or self._busy:
            return
        self._busy = True
        self.btn_run.configure(state="disabled", text="계산 중…")
        self.lbl_status.configure(text="백테스트 계산 중…")

        def work():
            res = run_backtest_detailed(cfg)
            self.after(0, lambda: self._finish_run(res))

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
