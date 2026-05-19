"""
데스크톱 GUI (CustomTkinter).
차트: output/backtest_report.png → CTkImage (v3.0+: 시작·종료일 캘린더·종목 선택 유지 등).
엔진: src.metrics.run_backtest_detailed
"""
from __future__ import annotations

import copy
import os
import threading
import tkinter as tk
from datetime import date, datetime
from tkinter import messagebox

import customtkinter as ctk
from PIL import Image
from tkcalendar import DateEntry

from src.data_loader import (
    default_backtest_period_range,
    fetch_filtered_universe,
    load_config,
)
from src.metrics import (
    BacktestResult,
    TREND_MA_PERIODS,
    run_backtest_detailed,
    trend_overlay_flags_from_strategy,
)

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")

# ==========================================
# [최상단 전역 변수 설정 구역] - 완벽히 정돈됨
# ==========================================
FIXED_PANEL_H = 780   # 좌/우 패널의 고정 세로 높이

FIXED_LEFT_W = 320    # 왼쪽 입력 패널의 고정 가로 폭
FIXED_RIGHT_W = 1050  # 오른쪽 차트 패널의 고정 가로 폭

FIXED_CHART_W = 1020  # 실제 캔들 차트 이미지의 고정 가로 폭
FIXED_CHART_H = 730   # 우측 하단 공백 청산 — 차트 세로 확장


def _date_entry_theme_kw() -> dict[str, str]:
    """tkcalendar DateEntry 색상을 CTk 라이트/다크에 맞춤(System 은 라이트 계열)."""
    dark = ctk.get_appearance_mode() == "Dark"
    if dark:
        return {
            "background": "#2b2b2b",
            "foreground": "#dce4ee",
            "bordercolor": "#565b5e",
            "headersbackground": "#1f538d",
            "headersforeground": "#ffffff",
            "selectbackground": "#144870",
            "selectforeground": "#ffffff",
            "weekendbackground": "#252526",
            "weekendforeground": "#9fa5ab",
        }
    return {
        "background": "#ffffff",
        "foreground": "#1a1a1a",
        "bordercolor": "#979da2",
        "headersbackground": "#36719f",
        "headersforeground": "#ffffff",
        "selectbackground": "#36719f",
        "selectforeground": "#ffffff",
        "weekendbackground": "#ebebeb",
        "weekendforeground": "#636363",
    }


def _parse_yaml_date(s: str) -> date | None:
    try:
        return datetime.strptime(str(s).strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _gui_summary_five_lines(res: BacktestResult) -> str:
    """성공 시 좌측 패널 전용 5줄 성과 요약(지시서 v2.6)."""
    d = {row[0]: row[1] for row in res.summary_rows}
    final = d.get("최종 평가액", "-")
    tot = d.get("누적 수익률", "-")
    cagr = d.get("연평균 수익률", "-")
    mdd = d.get("최대 손실 낙폭", "-")
    return "\n".join(
        [
            f"■ 매매 횟수 : 매수 {res.n_buy}회 / 매도 {res.n_sell}회",
            f"■ 최종 평가액 : {final}",
            f"■ 누적 수익률 : {tot}",
            f"■ 연평균 수익률 : {cagr}",
            f"■ 최대 손실 낙폭 : {mdd}",
        ]
    )


def _apply_yaml_to_widgets(ui: "BacktestGUI") -> None:
    """config/settings.yaml 값으로 입력 기본값 채움."""
    try:
        cfg = load_config()
    except OSError:
        return
    per = cfg.get("period", {})
    if per.get("start_date"):
        d0 = _parse_yaml_date(str(per["start_date"]))
        if d0 is not None:
            ui._date_start.set_date(d0)
    if per.get("end_date"):
        d1 = _parse_yaml_date(str(per["end_date"]))
        if d1 is not None:
            ui._date_end.set_date(d1)
    uni = cfg.get("universe", {})
    if uni.get("market"):
        ui.var_market.set(str(uni["market"]).upper())
    if uni.get("search_keyword") is not None:
        ui.var_keyword.set(str(uni["search_keyword"]))
    st = cfg.get("strategy", {})
    if st.get("interval"):
        ui.var_interval.set(str(st["interval"]).lower())
    if st.get("ma_period") is not None:
        mp = int(st["ma_period"])
        ui.var_ma_period.set(str(mp) if mp in (5, 10, 20) else "20")
    tf = trend_overlay_flags_from_strategy(st)
    for p in TREND_MA_PERIODS:
        ui._trend_vars[p].set(tf[p])
    if "show_chart_candle" in st:
        ui.var_show_candle.set(bool(st["show_chart_candle"]))
    if "show_chart_volume" in st:
        ui.var_show_volume.set(bool(st["show_chart_volume"]))
    if "show_chart_return" in st:
        ui.var_show_revenue.set(bool(st["show_chart_return"]))
    port = cfg.get("portfolio", {})
    if port.get("initial_cash") is not None:
        ui.var_cash.set(str(int(port["initial_cash"])))


def _try_build_config(ui: "BacktestGUI") -> dict | None:
    base = load_config()
    cfg = copy.deepcopy(base)
    kw = ui.var_keyword.get().strip()
    cfg.setdefault("universe", {})["market"] = ui.var_market.get().strip() or "KOSPI"
    cfg["universe"]["search_keyword"] = kw

    sel = ui.list_codes.curselection()
    if not sel:
        messagebox.showwarning("알림", "종목 검색 후 리스트에서 종목 1개를 클릭해 선택하세요.")
        return None
    line = ui.list_codes.get(sel[0])
    code = line.split()[0].strip()
    cfg["universe"]["selected_code"] = code

    interval = ui.var_interval.get()
    cfg.setdefault("strategy", {})["interval"] = interval
    try:
        ma_n = int(ui.var_ma_period.get())
    except ValueError:
        ma_n = 20
    if ma_n not in (5, 10, 20):
        messagebox.showerror("오류", "매매 기준 이평은 5·10·20일선 중 하나여야 합니다.")
        return None
    cfg.setdefault("strategy", {})["ma_period"] = ma_n

    try:
        sd = ui._date_start.get_date()
        ed = ui._date_end.get_date()
    except (ValueError, tk.TclError):
        messagebox.showerror(
            "오류",
            "시작일·종료일을 캘린더에서 올바르게 선택했는지 확인하세요.",
        )
        return None
    start = sd.strftime("%Y-%m-%d")
    end = ed.strftime("%Y-%m-%d")
    try:
        if datetime.strptime(start, "%Y-%m-%d").date() > datetime.strptime(
            end, "%Y-%m-%d"
        ).date():
            messagebox.showerror("오류", "시작일이 종료일보다 늦을 수 없습니다.")
            return None
    except ValueError:
        messagebox.showerror("오류", "시작일·종료일이 올바르지 않습니다.")
        return None
    cfg.setdefault("period", {})["start_date"] = start
    cfg.setdefault("period", {})["end_date"] = end

    try:
        cash = float(str(ui.var_cash.get()).replace(",", "").strip())
        if cash <= 0:
            raise ValueError
    except ValueError:
        messagebox.showerror("오류", "가상 원금은 0보다 큰 숫자여야 합니다.")
        return None
    cfg.setdefault("portfolio", {})["initial_cash"] = cash

    for p in TREND_MA_PERIODS:
        cfg.setdefault("strategy", {})[f"show_trend_ma{p}"] = bool(ui._trend_vars[p].get())
    for legacy in ("show_ma120", "show_ma200"):
        cfg.get("strategy", {}).pop(legacy, None)

    cfg.setdefault("strategy", {})["show_chart_candle"] = bool(ui.var_show_candle.get())
    cfg.setdefault("strategy", {})["show_chart_volume"] = bool(ui.var_show_volume.get())
    cfg.setdefault("strategy", {})["show_chart_return"] = bool(ui.var_show_revenue.get())

    return cfg


class BacktestGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("BackTesterKRX v3.0")

        self._candidates: list[tuple[str, str]] = []
        self._busy = False
        self._img_ref: ctk.CTkImage | None = None
        self._last_chart_path: str | None = None
        self._chart_resize_after_id: str | None = None

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
        row_dt.grid_columnconfigure(0, weight=1, uniform="dt")
        row_dt.grid_columnconfigure(1, weight=1, uniform="dt")
        d0 = ctk.CTkFrame(row_dt, fg_color="transparent")
        d0.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        d1 = ctk.CTkFrame(row_dt, fg_color="transparent")
        d1.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ctk.CTkLabel(d0, text="시작일").pack(anchor="w")
        self._date_start = DateEntry(
            d0,
            width=11,
            date_pattern="yyyy-mm-dd",
            **_date_entry_theme_kw(),
        )
        _ds, _de = default_backtest_period_range()
        self._date_start.set_date(_ds)
        self._date_start.pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(d1, text="종료일").pack(anchor="w")
        self._date_end = DateEntry(
            d1,
            width=11,
            date_pattern="yyyy-mm-dd",
            **_date_entry_theme_kw(),
        )
        self._date_end.set_date(_de)
        self._date_end.pack(fill="x", pady=(2, 0))

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
        row_run.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(row_run, text="가상 원금(원)").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        self.var_cash = ctk.StringVar(value="5000000")
        ctk.CTkEntry(row_run, textvariable=self.var_cash, width=120, height=36).grid(
            row=0, column=1, sticky="w"
        )
        self.btn_run = ctk.CTkButton(
            row_run,
            text="백테스트 실행",
            height=40,
            font=ctk.CTkFont(size=15, weight="bold"),
            command=self._on_run,
        )
        self.btn_run.grid(row=0, column=2, sticky="e", padx=(12, 0))

        self.text_summary = ctk.CTkTextbox(
            left,
            height=128,
            font=ctk.CTkFont(size=13),
            wrap="word",
        )
        self.text_summary.pack(fill="both", expand=False, padx=14, pady=(0, 14))
        self.text_summary.configure(state="disabled")

        right = ctk.CTkFrame(self, corner_radius=10, width=FIXED_RIGHT_W, height=FIXED_PANEL_H)
        right.grid(row=0, column=1, sticky="nw", padx=(6, 12), pady=(12, 6))
        right.grid_propagate(False)
        right.grid_rowconfigure(0, weight=1)
        right.grid_columnconfigure(0, weight=1)

        self.chart_frame = ctk.CTkFrame(
            right, fg_color=("gray95", "gray17"), width=FIXED_CHART_W, height=FIXED_CHART_H
        )  # 가로 1020 × 세로 FIXED_CHART_H
        self.chart_frame.grid(
            row=0, column=0, sticky="nw", padx=14, pady=(14, 14)
        )
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

        _apply_yaml_to_widgets(self)

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
        if self._busy:
            return
        cfg = _try_build_config(self)
        if cfg is None:
            return
        self._busy = True
        self.btn_run.configure(state="disabled", text="계산 중…")
        self.lbl_status.configure(text="백테스트 계산 중…")

        def work():
            res = run_backtest_detailed(cfg)
            self.after(0, lambda: self._finish_run(res))

        threading.Thread(target=work, daemon=True).start()

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

        self._set_summary(_gui_summary_five_lines(res))

        self.update_idletasks()
        self._update_chart_image(res.report_path)
        self.lbl_status.configure(text="완료")


def main():
    app = BacktestGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
