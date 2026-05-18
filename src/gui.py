"""
데스크톱 GUI (CustomTkinter).
차트: output/backtest_report.png → CTkImage (v2.6: 우측 전체 차트 전용, 성과 요약은 좌측 하단).
엔진: src.metrics.run_backtest_detailed
"""
from __future__ import annotations

import copy
import os
import threading
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk
from PIL import Image

from src.data_loader import fetch_filtered_universe, load_config
from src.metrics import BacktestResult, run_backtest_detailed

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")

# --- [유저 조정] 좌우 패널 가로 비율 (왼쪽 : 오른쪽) ---
COL_WEIGHT_LEFT = 1
COL_WEIGHT_RIGHT = 2

CHART_INNER_PAD = 16


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
        ui.var_start.set(str(per["start_date"]))
    if per.get("end_date"):
        ui.var_end.set(str(per["end_date"]))
    uni = cfg.get("universe", {})
    if uni.get("market"):
        ui.var_market.set(str(uni["market"]).upper())
    if uni.get("search_keyword") is not None:
        ui.var_keyword.set(str(uni["search_keyword"]))
    st = cfg.get("strategy", {})
    if st.get("interval"):
        ui.var_interval.set(str(st["interval"]).lower())
    if st.get("ma_period") is not None:
        ui.var_ma.set(str(int(st["ma_period"])))
    if "show_ma120" in st:
        ui.var_show_ma120.set(bool(st["show_ma120"]))
    if "show_ma200" in st:
        ui.var_show_ma200.set(bool(st["show_ma200"]))
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
        cfg.setdefault("strategy", {})["ma_period"] = int(ui.var_ma.get())
    except ValueError:
        messagebox.showerror("오류", "이평선 N 은 정수여야 합니다.")
        return None

    start = ui.var_start.get().strip()
    end = ui.var_end.get().strip()
    if len(start) != 10 or len(end) != 10:
        messagebox.showerror("오류", "시작일·종료일은 YYYY-MM-DD 형식이어야 합니다.")
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

    cfg.setdefault("strategy", {})["show_ma120"] = bool(ui.var_show_ma120.get())
    cfg.setdefault("strategy", {})["show_ma200"] = bool(ui.var_show_ma200.get())

    return cfg


class BacktestGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("BackTesterKRX v2.6")

        self._candidates: list[tuple[str, str]] = []
        self._busy = False
        self._img_ref: ctk.CTkImage | None = None
        self._last_chart_path: str | None = None
        self._chart_resize_after_id: str | None = None

        self.grid_columnconfigure(0, weight=COL_WEIGHT_LEFT)
        self.grid_columnconfigure(1, weight=COL_WEIGHT_RIGHT)
        self.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(self, corner_radius=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(12, 6), pady=(12, 6))

        ctk.CTkLabel(
            left, text="입력", font=ctk.CTkFont(size=18, weight="bold")
        ).pack(anchor="w", padx=14, pady=(12, 6))

        row_mk = ctk.CTkFrame(left, fg_color="transparent")
        row_mk.pack(fill="x", padx=14, pady=(0, 6))
        row_mk.grid_columnconfigure(0, weight=1, uniform="mk")
        row_mk.grid_columnconfigure(1, weight=1, uniform="mk")
        mk_l = ctk.CTkFrame(row_mk, fg_color="transparent")
        mk_l.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        mk_r = ctk.CTkFrame(row_mk, fg_color="transparent")
        mk_r.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ctk.CTkLabel(mk_l, text="시장").pack(anchor="w")
        self.var_market = ctk.StringVar(value="KOSPI")
        ctk.CTkOptionMenu(
            mk_l,
            values=["KOSPI", "KOSDAQ"],
            variable=self.var_market,
        ).pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(mk_r, text="종목명 키워드").pack(anchor="w")
        self.var_keyword = ctk.StringVar(value="삼성")
        ctk.CTkEntry(mk_r, textvariable=self.var_keyword).pack(
            fill="x", pady=(2, 0)
        )

        ctk.CTkButton(left, text="종목 검색", command=self._on_search).pack(
            fill="x", padx=14, pady=(0, 6)
        )

        ctk.CTkLabel(left, text="검색 결과 (1개만 선택)").pack(anchor="w", padx=14)
        list_frame = ctk.CTkFrame(left, fg_color="transparent")
        list_frame.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        self.list_codes = tk.Listbox(
            list_frame,
            height=7,
            font=("Segoe UI", 11),
            selectmode=tk.SINGLE,
            activestyle="dotbox",
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
        ctk.CTkLabel(d0, text="시작일 (YYYY-MM-DD)").pack(anchor="w")
        self.var_start = ctk.StringVar(value="2021-01-01")
        ctk.CTkEntry(d0, textvariable=self.var_start).pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(d1, text="종료일 (YYYY-MM-DD)").pack(anchor="w")
        self.var_end = ctk.StringVar(value="2025-12-31")
        ctk.CTkEntry(d1, textvariable=self.var_end).pack(fill="x", pady=(2, 0))

        ctk.CTkLabel(left, text="가상 원금 (원)").pack(anchor="w", padx=14)
        self.var_cash = ctk.StringVar(value="5000000")
        ctk.CTkEntry(left, textvariable=self.var_cash).pack(
            fill="x", padx=14, pady=(0, 6)
        )

        row_ma = ctk.CTkFrame(left, fg_color="transparent")
        row_ma.pack(fill="x", padx=14, pady=(0, 6))
        row_ma.grid_columnconfigure(0, weight=0)
        row_ma.grid_columnconfigure(1, weight=1)
        ma_cell = ctk.CTkFrame(row_ma, fg_color="transparent")
        ma_cell.grid(row=0, column=0, sticky="nw", padx=(0, 8))
        ctk.CTkLabel(ma_cell, text="이평선 N").pack(anchor="w")
        self.var_ma = ctk.StringVar(value="20")
        ctk.CTkEntry(ma_cell, width=72, textvariable=self.var_ma).pack(
            anchor="w", pady=(2, 0)
        )
        trend_cell = ctk.CTkFrame(row_ma, fg_color="transparent")
        trend_cell.grid(row=0, column=1, sticky="e")
        self.var_show_ma120 = ctk.BooleanVar(value=False)
        self.var_show_ma200 = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            trend_cell,
            text="120일선",
            variable=self.var_show_ma120,
        ).pack(side="left", padx=(0, 10))
        ctk.CTkCheckBox(
            trend_cell,
            text="200일선",
            variable=self.var_show_ma200,
        ).pack(side="left")

        self.btn_run = ctk.CTkButton(
            left,
            text="백테스트 실행",
            height=40,
            font=ctk.CTkFont(size=15, weight="bold"),
            command=self._on_run,
        )
        self.btn_run.pack(fill="x", padx=14, pady=(8, 8))

        self.text_summary = ctk.CTkTextbox(
            left,
            height=128,
            font=ctk.CTkFont(size=13),
            wrap="word",
        )
        self.text_summary.pack(fill="both", expand=False, padx=14, pady=(0, 14))
        self.text_summary.configure(state="disabled")

        right = ctk.CTkFrame(self, corner_radius=10)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 12), pady=(12, 6))
        right.grid_rowconfigure(0, weight=1)
        right.grid_columnconfigure(0, weight=1)

        self.chart_frame = ctk.CTkFrame(right, fg_color=("gray95", "gray17"))
        self.chart_frame.grid(row=0, column=0, sticky="nsew", padx=14, pady=14)
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
            fw = self.chart_frame.winfo_width() - CHART_INNER_PAD
            fh = self.chart_frame.winfo_height() - CHART_INNER_PAD
            if fw < 80:
                fw = 800
            if fh < 80:
                fh = 500

            pil_img = Image.open(image_path)
            iw, ih = pil_img.size
            scale = min(fw / max(iw, 1), fh / max(ih, 1))
            nw = max(1, int(iw * scale))
            nh = max(1, int(ih * scale))
            resized = pil_img.resize((nw, nh), Image.Resampling.LANCZOS)

            self._img_ref = ctk.CTkImage(
                light_image=resized,
                dark_image=resized,
                size=(nw, nh),
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
