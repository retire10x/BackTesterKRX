"""
GUI 전용 헬퍼: YAML 반영·설정 dict 빌드·툴팁 등 (엔진 `metrics` 와 분리).
`BacktestGUI` 클래스는 `gui.py` 에만 둔다 (작업지시서 §10.6).
"""
from __future__ import annotations

import copy
import tkinter as tk
from collections.abc import Callable
from datetime import date, datetime
from tkinter import messagebox

import customtkinter as ctk

from src.backtest_constants import TREND_MA_PERIODS
from src.data_loader import default_backtest_period_range, load_config
from src.metrics import BacktestResult, trend_overlay_flags_from_strategy

TooltipTextFn = str | Callable[[], str]


def date_entry_theme_kw() -> dict[str, str]:
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


class HoverTooltip:
    """체크박스·입력칸 등에 마우스를 올렸을 때 잠시 후 노란 설명 팝업."""

    def __init__(
        self, widget: tk.Misc, text: TooltipTextFn, delay_ms: int = 420
    ) -> None:
        self._widget = widget
        self._text = text
        self._delay_ms = delay_ms
        self._tip: tk.Toplevel | None = None
        self._after_id: str | None = None
        widget.bind("<Enter>", self._on_enter)
        widget.bind("<Leave>", self._on_leave)

    def _cancel_scheduled(self) -> None:
        if self._after_id is not None:
            self._widget.after_cancel(self._after_id)
            self._after_id = None

    def _on_enter(self, _event: tk.Event | None = None) -> None:
        self._cancel_scheduled()
        self._after_id = self._widget.after(self._delay_ms, self._show_tip)

    def _on_leave(self, _event: tk.Event | None = None) -> None:
        self._cancel_scheduled()
        self._hide_tip()

    def _show_tip(self) -> None:
        self._after_id = None
        if self._tip is not None:
            return
        x = int(self._widget.winfo_rootx() + 14)
        y = int(self._widget.winfo_rooty() + self._widget.winfo_height() + 6)
        self._tip = tk.Toplevel(self._widget)
        self._tip.wm_overrideredirect(True)
        try:
            self._tip.attributes("-topmost", True)
        except tk.TclError:
            pass
        self._tip.wm_geometry(f"+{x}+{y}")
        body = self._text() if callable(self._text) else self._text
        lbl = tk.Label(
            self._tip,
            text=body,
            justify="left",
            background="#fffacd",
            relief="solid",
            borderwidth=1,
            font=("Segoe UI", 10),
            wraplength=440,
        )
        lbl.pack(ipadx=8, ipady=6)

    def _hide_tip(self) -> None:
        self._cancel_scheduled()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


def parse_yaml_date(s: str) -> date | None:
    try:
        return datetime.strptime(str(s).strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def trading_rules_static_text(
    ma_n: int,
    interval: str,
    *,
    trailing_stop_enabled: bool = False,
    trailing_hinge_pct: float = 10.0,
    trailing_below_drop_pct: float = 3.0,
    trailing_above_drop_pct: float = 5.0,
) -> str:
    """우측 매매 규칙 패널용 안내 문구(엔진 strategy.add_signals 와 동일 전제)."""
    bar_kw = "주간 봉" if interval.strip().lower() == "weekly" else "일간 봉"
    body = (
        "※ 아래 체크 필터는 매수 진입에만 적용(매도 신호는 동일).\n\n"
        f"매매 기준 : 종가 기준 {ma_n}기간 단순 이동평균 ({bar_kw})\n\n"
        "1. 매매 기준 이평선 골든크로스 매수, 데드크로스 매도.\n"
        "   → 종가가 위 이평선을 상향 돌파하면 매수 신호, 하향 돌파하면 매도 신호 "
        "(전일·당일 종가와 당일 이평으로 판단).\n\n"
        "체결 시뮬 : 신호는 봉 종가에서 확정, 다음 봉 시가 체결로 반영됩니다.\n\n"
        "[v4.0] 활성화한 필터는 엔진에서 AND 로 결합됩니다."
    )
    if not trailing_stop_enabled:
        return body
    hinge = trailing_hinge_pct
    bd = trailing_below_drop_pct
    ad = trailing_above_drop_pct
    body += (
        "\n\n"
        "[v4.4] 가변 낙폭 매도: 보유 중 매수 체결가 대비 장중 최고가(워터마크) 기준 피크 "
        "수익률이 "
        f"기준 {hinge:g}% 미만이면 고점 대비 {bd:g}% 하락 종가 확정 시(다음 봉 시가 청산), "
        f"피크가 한 번이라도 기준 {hinge:g}% 이상이면 고점 대비 {ad:g}% 하락 시 청산합니다. "
        "이 조건은 데드크로스 전에도 우선 적용되며 차트 타점은 밝은 노란색 ▼ 로 표시됩니다."
    )
    return body


def gui_summary_five_lines(res: BacktestResult) -> str:
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


def apply_yaml_to_widgets(ui: "BacktestGUI") -> None:
    """config/settings.yaml 값으로 입력 기본값 채움."""
    try:
        cfg = load_config()
    except OSError:
        return
    per = cfg.get("period", {})
    ds_raw = str(per.get("start_date") or "").strip()
    de_raw = str(per.get("end_date") or "").strip()
    if ds_raw and de_raw:
        d0 = parse_yaml_date(ds_raw)
        d1 = parse_yaml_date(de_raw)
        if d0 is not None:
            ui._date_start.set_date(d0)
        if d1 is not None:
            ui._date_end.set_date(d1)
    else:
        s_d, e_d = default_backtest_period_range()
        ui._date_start.set_date(s_d)
        ui._date_end.set_date(e_d)
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
    if "filter_trend_slope" in st:
        ui.var_filter_trend.set(bool(st["filter_trend_slope"]))
    if "filter_breakout_strength" in st:
        ui.var_filter_breakout.set(bool(st["filter_breakout_strength"]))
    if "filter_time_buffer" in st:
        ui.var_filter_timebuf.set(bool(st["filter_time_buffer"]))
    if st.get("slope_threshold") is not None:
        ui.var_slope_threshold.set(str(st["slope_threshold"]))
    if "trailing_stop_enabled" in st:
        ui.var_trailing_stop.set(bool(st["trailing_stop_enabled"]))
    if st.get("trailing_reference_pct") is not None:
        ui.var_trailing_reference_pct.set(str(st["trailing_reference_pct"]))
    if st.get("trailing_drop_below_pct") is not None:
        ui.var_trailing_drop_below_pct.set(str(st["trailing_drop_below_pct"]))
    if st.get("trailing_drop_above_pct") is not None:
        ui.var_trailing_drop_above_pct.set(str(st["trailing_drop_above_pct"]))
    port = cfg.get("portfolio", {})
    if port.get("initial_cash") is not None:
        ui.var_cash.set(str(int(port["initial_cash"])))


def try_build_config(ui: "BacktestGUI", *, silent: bool = False) -> dict | None:
    base = load_config()
    cfg = copy.deepcopy(base)
    kw = ui.var_keyword.get().strip()
    cfg.setdefault("universe", {})["market"] = ui.var_market.get().strip() or "KOSPI"
    cfg["universe"]["search_keyword"] = kw

    sel = ui.list_codes.curselection()
    if not sel:
        if not silent:
            messagebox.showwarning(
                "알림", "종목 검색 후 리스트에서 종목 1개를 클릭해 선택하세요."
            )
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

    try:
        slope_thr = float(str(ui.var_slope_threshold.get()).replace(",", "").strip())
    except ValueError:
        slope_thr = 0.01
    cfg.setdefault("strategy", {})["slope_threshold"] = slope_thr
    cfg.setdefault("strategy", {})["filter_trend_slope"] = bool(ui.var_filter_trend.get())
    cfg.setdefault("strategy", {})["filter_breakout_strength"] = bool(
        ui.var_filter_breakout.get()
    )
    cfg.setdefault("strategy", {})["filter_time_buffer"] = bool(ui.var_filter_timebuf.get())

    try:
        t_ref = float(
            str(ui.var_trailing_reference_pct.get()).replace(",", "").strip()
        )
        t_below = float(
            str(ui.var_trailing_drop_below_pct.get()).replace(",", "").strip()
        )
        t_above = float(
            str(ui.var_trailing_drop_above_pct.get()).replace(",", "").strip()
        )
    except ValueError:
        messagebox.showerror(
            "오류",
            "가변 낙폭 매도 수치(기준·미달·돌파 %)는 숫자로 입력하세요.",
        )
        return None
    if t_ref <= 0 or t_below <= 0 or t_above <= 0:
        messagebox.showerror(
            "오류",
            "가변 낙폭 매도 기준 및 낙폭 값은 모두 양수여야 합니다.",
        )
        return None
    cfg.setdefault("strategy", {})["trailing_stop_enabled"] = bool(
        ui.var_trailing_stop.get()
    )
    cfg.setdefault("strategy", {})["trailing_reference_pct"] = t_ref
    cfg.setdefault("strategy", {})["trailing_drop_below_pct"] = t_below
    cfg.setdefault("strategy", {})["trailing_drop_above_pct"] = t_above

    return cfg
