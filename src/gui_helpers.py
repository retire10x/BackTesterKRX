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

# GUI 본문·툴팁 고정 크기 («조회 주기» 줄과 통일). 자동 DPI 확대는 `gui.py`에서 `set_*_scaling(1.0)` 으로 차단.
# CTkFont 는 import 시점에 만들 수 없음(기본 Tk 루트 없음) — `gui_body_font()` 로 창 생성 후 사용.
GUI_FONT_FAMILY = "Segoe UI"
GUI_FONT_SIZE = 13

_gui_body_font_cached: ctk.CTkFont | None = None


def gui_body_font() -> ctk.CTkFont:
    """메인 CTk 창 `super().__init__()` 이후에만 호출. 싱글턴 캐시."""
    global _gui_body_font_cached
    if _gui_body_font_cached is None:
        _gui_body_font_cached = ctk.CTkFont(
            family=GUI_FONT_FAMILY, size=GUI_FONT_SIZE
        )
    return _gui_body_font_cached

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
    """마우스 오버 시 다크 네이비(#1e293b) 배경, 흰색 글자, 둥근 모서리(8px) 및 하단 중앙 화살표를 갖춘 모던 말풍선 툴팁."""

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

        body = self._text() if callable(self._text) else self._text
        if not body:
            return

        # 1. 텍스트 바운딩 박스 정밀 측정
        tmp_lbl = tk.Label(
            self._widget,
            text=body,
            font=(GUI_FONT_FAMILY, GUI_FONT_SIZE),
            justify="left",
            wraplength=350,
        )
        tmp_lbl.update_idletasks()
        text_w = tmp_lbl.winfo_reqwidth()
        text_h = tmp_lbl.winfo_reqheight()
        tmp_lbl.destroy()

        # 2. 패딩 및 캔버스 크기 계산 (코너 깎임 방지를 위해 여유 4px 확보)
        w = text_w + 24
        h = text_h + 20
        arrow_h = 8
        margin = 4
        W = w + margin * 2
        H = h + arrow_h + margin * 2

        # 3. 팝업 창 생성 및 투명 마스킹 설정
        self._tip = tk.Toplevel(self._widget)
        self._tip.wm_overrideredirect(True)
        try:
            self._tip.attributes("-topmost", True)
        except tk.TclError:
            pass
        self._tip.wm_attributes("-transparentcolor", "#fe00fe")
        self._tip.configure(bg="#fe00fe")

        # 4. 좌표 계산: 하단 중앙 화살표 끝이 대상 위젯의 상단 중앙을 향하도록 함
        w_x = self._widget.winfo_rootx()
        w_y = self._widget.winfo_rooty()
        w_w = self._widget.winfo_width()
        target_x = w_x + w_w / 2
        target_y = w_y

        tip_x = target_x - W / 2
        tip_y = target_y - H + margin  # 약간의 겹침 처리

        self._tip.wm_geometry(f"+{int(tip_x)}+{int(tip_y)}")

        # 5. Canvas 생성 및 말풍선 그리기
        canvas = tk.Canvas(
            self._tip,
            width=W,
            height=H,
            bg="#fe00fe",
            highlightthickness=0,
        )
        canvas.pack()

        r = 8
        x1, y1, x2, y2 = margin, margin, W - margin, H - arrow_h - margin
        color = "#1e293b"  # 다크 네이비

        # 둥근 코너 호(Arc) 그리기
        canvas.create_arc(x1, y1, x1 + r * 2, y1 + r * 2, start=90, extent=90, fill=color, outline=color, style="pieslice")
        canvas.create_arc(x2 - r * 2, y1, x2, y1 + r * 2, start=0, extent=90, fill=color, outline=color, style="pieslice")
        canvas.create_arc(x2 - r * 2, y2 - r * 2, x2, y2, start=270, extent=90, fill=color, outline=color, style="pieslice")
        canvas.create_arc(x1, y2 - r * 2, x1 + r * 2, y2, start=180, extent=90, fill=color, outline=color, style="pieslice")

        # 사각형 내부 채우기
        canvas.create_rectangle(x1 + r, y1, x2 - r, y2, fill=color, outline=color)
        canvas.create_rectangle(x1, y1 + r, x2, y2 - r, fill=color, outline=color)

        # 하단 중앙 아래방향 삼각형 그리기
        arrow_pts = [W / 2 - 6, y2, W / 2, H - margin, W / 2 + 6, y2]
        canvas.create_polygon(arrow_pts, fill=color, outline=color)

        # 텍스트 그리기
        canvas.create_text(
            W / 2,
            (y1 + y2) / 2,
            text=body,
            fill="#f8fafc",  # 흰색 글씨
            font=(GUI_FONT_FAMILY, GUI_FONT_SIZE),
            justify="left",
            width=350,
            anchor="center",
        )

        # 6. 부드러운 Fade-in 애니메이션 (200ms)
        self._tip.attributes("-alpha", 0.0)

        def _fade_in(current_alpha: float = 0.0) -> None:
            if self._tip is None:
                return
            try:
                val = float(self._tip.attributes("-alpha"))
            except tk.TclError:
                return
            if val < 1.0:
                next_alpha = min(1.0, val + 0.1)
                self._tip.attributes("-alpha", next_alpha)
                self._tip.after(20, lambda: _fade_in(next_alpha))

        _fade_in(0.0)

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
    golden_buy_enabled: bool = True,
    dead_cross_sell_enabled: bool = True,
    trailing_stop_enabled: bool = False,
    trailing_hinge_pct: float = 10.0,
    trailing_below_drop_pct: float = 3.0,
    trailing_above_drop_pct: float = 5.0,
) -> str:
    """우측 매매 규칙 패널용 안내(엔진 `strategy.add_signals`·`simulator.simulate_single` 과 v4.6 동기)."""
    bar_kw = "주간 봉" if interval.strip().lower() == "weekly" else "일간 봉"
    g_on = golden_buy_enabled
    d_on = dead_cross_sell_enabled
    body = (
        f"[매매 기준] 종가 기준 {ma_n}일 이동평균 ({bar_kw})\n\n"
        f"[기본] 골든 매수: {'사용' if g_on else '끔'} · "
        f"데드크로스 매도 체결: {'사용' if d_on else '끔'}\n\n"
        "[매수] 골든 매수를 켠 경우에만 이평 골든크로스가 매수 후보입니다. "
        "'대세(Slope)'·'돌파 강도'를 켠 경우 활성 필터 조건은 **모두 AND** 로 같은 봉에서 만족해야 최종 매수합니다. "
        "시간 버퍼 진입 역시 검증되는 신호 봉에서 활성 필터 AND 를 통과해야 합니다.\n\n"
        "[매도] 가변 낙폭을 켠 경우 고점 대비 하락 조건이 먼저 충족되면 다음 봉 시가 조기 청산합니다. "
        "그렇지 않거나 끈 경우에는 데드 매도 사용 시 데크로스 다음 봉 시가 청산 — **트레일 우선**(OR 브랜치).\n\n"
        "신호는 봉 종가에서 확정, 체결은 다음 봉 시가입니다."
    )
    if trailing_stop_enabled:
        hinge = trailing_hinge_pct
        bd = trailing_below_drop_pct
        ad = trailing_above_drop_pct
        body += (
            f"\n\n[가변 낙폭 ON] 피크 기준 {hinge:g}% 미만 구간은 고점 대비 {bd:g}% 하락, "
            f"그 이상 한 번이라도 노출 시에는 고점 대비 {ad:g}% 하락 시 조기 청산(차트 타점 밝은 노랑 ▼)."
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
    if "golden_buy_enabled" in st:
        ui.var_golden_buy.set(bool(st["golden_buy_enabled"]))
    if "dead_cross_sell_enabled" in st:
        ui.var_dead_sell.set(bool(st["dead_cross_sell_enabled"]))
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
    if sel:
        line = ui.list_codes.get(sel[0])
        code = line.split()[0].strip()
    else:
        code = str((cfg.get("universe") or {}).get("selected_code") or "").strip()
        if not code:
            if not silent:
                messagebox.showwarning(
                    "알림",
                    "종목 검색 후 리스트에서 종목 1개를 선택하거나, "
                    "config/settings.yaml 의 universe.selected_code 를 설정하세요.",
                )
            return None
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

    cfg.setdefault("strategy", {})["golden_buy_enabled"] = bool(ui.var_golden_buy.get())
    cfg.setdefault("strategy", {})["dead_cross_sell_enabled"] = bool(
        ui.var_dead_sell.get()
    )

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
