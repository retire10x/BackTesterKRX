"""
GUI 전용 헬퍼: YAML 반영·설정 dict 빌드·툴팁 등 (엔진 `metrics` 와 분리).
`BacktestGUI` 클래스는 `gui.py` 에만 둔다 (작업지시서 §10.6).
"""
from __future__ import annotations

import copy
import json
import os
import tkinter as tk
from collections.abc import Callable
from datetime import date, datetime
from tkinter import messagebox

import customtkinter as ctk

import numpy as np

from src.backtest_constants import CHART_MA_TOGGLE_PERIODS
from src.data_loader import (
    default_backtest_period_range,
    load_config,
    normalize_krx_listing_market,
)
from src.metrics import BacktestResult, trend_overlay_flags_from_strategy
from src.stock_screener import default_screener_config

# =========================================================================
# 레거시 screener_mode 문자열 마이그레이션 및 검색 결과 포맷(티커 | 종목명 | 시총 · v4.14 파이프라인 확장 컬럼)
# =========================================================================

GUI_SCREENER_MODE_WHOLE = "whole"
GUI_SCREENER_MODE_SCREENER = "screener"
GUI_SCREENER_MODE_MCAP_TOP = "mcap_top30"
GUI_SCREENER_MODE_BREAKOUT = "breakout_energy"
# v4.12_Beta: 매수 규칙(골든+진입 필터) 전환일 추적 스크린
GUI_SCREENER_MODE_ENTRY_EVENT = "entry_event_track"
# v4.13 김직선식 일봉 1봉 패턴 스크린
GUI_SCREENER_MODE_KIM_LINE_1BAR = "kim_line_1bar"

VALID_GUI_SCREENER_MODES_FROZEN = frozenset(
    {
        GUI_SCREENER_MODE_WHOLE,
        GUI_SCREENER_MODE_SCREENER,
        GUI_SCREENER_MODE_MCAP_TOP,
        GUI_SCREENER_MODE_BREAKOUT,
        GUI_SCREENER_MODE_ENTRY_EVENT,
        GUI_SCREENER_MODE_KIM_LINE_1BAR,
    }
)


def migrate_legacy_screener_mode_to_pipeline(
    legacy_mode_raw: object,
) -> tuple[bool, bool, bool]:
    """
    예전 라디오 `universe.screener_mode` → 파이프라인 3단계 (시총·매수규칙·김직선) 체크 초기값.
    ATR 랭킹·돌파 전용 라디오는 신규 UI에 없음 — 필요 시 사용자가 새 체크박스로 조합(v4.14).
    """
    if legacy_mode_raw is None:
        return (False, False, False)
    m = str(legacy_mode_raw).strip().lower()
    if not m:
        return (False, False, False)
    if m == str(GUI_SCREENER_MODE_MCAP_TOP).lower():
        return (True, False, False)
    if m == str(GUI_SCREENER_MODE_ENTRY_EVENT).lower():
        return (False, True, False)
    if m == str(GUI_SCREENER_MODE_KIM_LINE_1BAR).lower():
        return (False, False, True)
    return (False, False, False)


def default_screener_pipeline_dict() -> dict[str, bool]:
    return {
        "stage_mcap_top100": False,
        "stage_buy_rules": False,
        "stage_kim_line_1bar": False,
    }


def parse_gui_list_row_code(line: str) -> str:
    """리스트 줄에서 6자리 티커만 추출(구분 '|' 허용, 첫 세그먼트만 사용)."""
    raw = str(line or "").strip()
    if not raw:
        return ""
    head = raw.split("|", 1)[0].strip()
    if not head:
        return ""
    tok = head.split()[0].strip()
    digits = "".join(ch for ch in tok if ch.isdigit())
    if not digits:
        return ""
    if len(digits) >= 6:
        return digits[-6:]
    return digits.zfill(6)


def normalize_krx_listing_market_arg(raw: object) -> str:
    """기본 폴백 KOSPI. settings·GUI 에서 허용 시장만 허용."""
    m = normalize_krx_listing_market(raw)
    return m if m is not None else "KOSPI"


def format_gui_list_hist(
    code: str,
    name: str,
    listing_market: str,
    market_cap_krw: float | None,
) -> str:
    """최근 백테스트 이력 4컬럼: 티커 | 종목명 | 시장 | 시총(v4.8)."""
    cdf = str(code or "").strip().zfill(6)
    nm = str(name or "").strip() or cdf
    mk = normalize_krx_listing_market_arg(listing_market)
    mc_p = (
        format_gui_list_triple(cdf, nm, market_cap_krw).split("|", 2)[-1].strip()
    )
    return f"{cdf} | {nm} | {mk} | {mc_p}"


def history_row_normalize(
    tup: tuple | list,
) -> tuple[str, str, float | None, str]:
    """디스크/메모리 이력 3튜플·4튜플 → (코드,이름,시총,시장)."""
    if len(tup) >= 4:
        c, n, mc_raw, mk = tup[0], tup[1], tup[2], tup[3]
    elif len(tup) >= 3:
        c, n, mc_raw, mk = tup[0], tup[1], tup[2], "KOSPI"
    else:
        return "", "", None, "KOSPI"
    cd = str(c or "").strip().zfill(6)
    nm = str(n or "").strip() or cd
    try:
        mcv = (
            float(mc_raw)
            if mc_raw is not None
            and str(mc_raw) not in ("", "null", "None")
            else None
        )
    except (TypeError, ValueError):
        mcv = None
    if mcv is not None and (not np.isfinite(mcv) or mcv <= 0):
        mcv = None
    lm = normalize_krx_listing_market_arg(mk)
    return cd, nm, mcv, lm


def format_gui_list_triple(code: str, name: str, market_cap_krw: float | None) -> str:
    """예: 009150 | 삼화콘덴서 | 7,421 억원."""
    cdf = str(code or "").strip().zfill(6)
    nm = str(name or "").strip() or cdf
    if market_cap_krw is None:
        mcap_disp = "N/A"
    else:
        try:
            x = float(market_cap_krw)
        except (TypeError, ValueError):
            mcap_disp = "N/A"
        else:
            if not np.isfinite(x) or x <= 0:
                mcap_disp = "N/A"
            else:
                eok = int(round(x / 1e8))
                mcap_disp = f"{eok:,d} 억원"
    return f"{cdf} | {nm} | {mcap_disp}"


def format_gui_list_entry_event(
    code: str,
    name: str,
    market_cap_krw: float | None,
    *,
    signal_age_td: int,
    spread_pct: float,
) -> str:
    """당일 타점(Event) 추적 모드 전용 — 기본 3열 + 신호 경과일 + 타점 이격도(%)."""
    base = format_gui_list_triple(code, name, market_cap_krw)
    age_s = "당일" if signal_age_td == 0 else str(int(signal_age_td))
    spread_s = f"{float(spread_pct):+.2f}%"
    return f"{base} | {age_s} | {spread_s}"


def format_gui_list_kim_candle(
    code: str,
    name: str,
    market_cap_krw: float | None,
    *,
    pattern_label: str,
    base_turnover_krw: float,
    spread_pct: float,
) -> str:
    """김직선 1봉 캔들 모드 — 티커|종목명|시총 + 패턴 + 기준봉 거래대금(억) + 타점 이격도(%)."""
    base = format_gui_list_triple(code, name, market_cap_krw)
    try:
        tv = float(base_turnover_krw)
        if not np.isfinite(tv) or tv <= 0:
            tv_disp = "N/A"
        else:
            tv_disp = f"{int(round(tv / 1e8)):,d} 억"
    except (TypeError, ValueError):
        tv_disp = "N/A"
    spread_s = f"{float(spread_pct):+.2f}%"
    return f"{base} | {pattern_label} | {tv_disp} | {spread_s}"


def format_gui_list_pipeline(
    code: str,
    name: str,
    market_cap_krw: float | None,
    *,
    entry_match_flag: str,
    candle_pattern: str,
    spread_from_ref_pct: float | None,
) -> str:
    """v4.14 통합 파이프라인 — 티커·종목명·시총 · 매수조건 플래그 · 캔들 패턴 · 이격도."""
    base = format_gui_list_triple(code, name, market_cap_krw)
    spread_s = (
        "—"
        if spread_from_ref_pct is None
        or (
            isinstance(spread_from_ref_pct, float)
            and not np.isfinite(spread_from_ref_pct)
        )
        else f"{float(spread_from_ref_pct):+.2f}%"
    )
    return (
        f"{base} | {str(entry_match_flag)} | "
        f"{str(candle_pattern)} | {spread_s}"
    )


# v3.76: Tk(DateEntry·Listbox)는 양수 pt — CTk `set_*_scaling(None)` 과 OS DPI 연동.
GUI_FONT_FAMILY = "Malgun Gothic"
GUI_FONT_SIZE_PT = 11
GUI_LIST_FONT_SIZE_PT = 10
GUI_HINT_FONT_SIZE_PT = 9
GUI_NAV_FONT_SIZE_PT = 11
# 레거시 import 호환
GUI_FONT_SIZE = GUI_FONT_SIZE_PT
GUI_LIST_FONT_SIZE = GUI_LIST_FONT_SIZE_PT
GUI_HINT_FONT_SIZE = GUI_HINT_FONT_SIZE_PT
GUI_DATE_ENTRY_WIDTH = 11
LAST_SESSION_JSON = os.path.join("config", "last_session.json")
VALID_UNIVERSE_LIMITS = frozenset({100, 300, 500})
# 콤보 표시값(시총 상위 N종) — 필드 제목은 GUI에서 "Top"
UNIVERSE_LIMIT_OPTIONS = tuple(str(n) for n in sorted(VALID_UNIVERSE_LIMITS))
# v3.66 이전 세션 JSON 호환
_LEGACY_UNIVERSE_COMBO_LABELS = {
    "Top 100": 100,
    "Top": 300,
    "Top 500": 500,
}

_gui_body_font_cached: ctk.CTkFont | None = None
_gui_action_btn_font_cached: ctk.CTkFont | None = None
_gui_hint_font_cached: ctk.CTkFont | None = None


def gui_tk_font_pt(
    size_pt: int, *, weight: str = "normal"
) -> tuple[str, int] | tuple[str, int, str]:
    """Tk/Canvas/DateEntry/Listbox — 양수 pt(CTk OS 스케일과 연동)."""
    pt = max(1, int(size_pt))
    if weight and weight != "normal":
        return (GUI_FONT_FAMILY, pt, weight)
    return (GUI_FONT_FAMILY, pt)


def gui_ctk_font_pt(size_pt: int, *, weight: str = "normal") -> ctk.CTkFont:
    """CTk 위젯용 pt 폰트(CTk 기본 OS DPI 스케일과 곱해짐)."""
    kw: dict = {"family": GUI_FONT_FAMILY, "size": max(1, int(size_pt))}
    if weight and weight != "normal":
        kw["weight"] = weight
    return ctk.CTkFont(**kw)


def gui_list_font_tuple() -> tuple[str, int]:
    """Listbox 등 밀집 데이터 표."""
    return gui_tk_font_pt(GUI_LIST_FONT_SIZE_PT)


def gui_nav_font_tuple() -> tuple[str, int]:
    """차트 내비 버튼 등."""
    return gui_tk_font_pt(GUI_NAV_FONT_SIZE_PT)


def gui_hint_font() -> ctk.CTkFont:
    """안내·면책 등 보조 텍스트."""
    global _gui_hint_font_cached
    if _gui_hint_font_cached is None:
        _gui_hint_font_cached = gui_ctk_font_pt(GUI_HINT_FONT_SIZE_PT)
    return _gui_hint_font_cached


def gui_body_font() -> ctk.CTkFont:
    """메인 CTk 창 `super().__init__()` 이후에만 호출. 싱글턴 캐시."""
    global _gui_body_font_cached
    if _gui_body_font_cached is None:
        _gui_body_font_cached = gui_ctk_font_pt(GUI_FONT_SIZE_PT)
    return _gui_body_font_cached


def gui_action_btn_font() -> ctk.CTkFont:
    """스캔·백테스트 등 타이머 표기가 들어가는 액션 버튼용."""
    global _gui_action_btn_font_cached
    if _gui_action_btn_font_cached is None:
        _gui_action_btn_font_cached = gui_ctk_font_pt(GUI_LIST_FONT_SIZE_PT)
    return _gui_action_btn_font_cached


def normalize_universe_limit_choice(raw: object, *, default: int) -> int:
    s = str(raw).strip()
    if s in _LEGACY_UNIVERSE_COMBO_LABELS:
        return int(_LEGACY_UNIVERSE_COMBO_LABELS[s])
    try:
        n = int(s)
    except (TypeError, ValueError):
        n = default
    if n not in VALID_UNIVERSE_LIMITS:
        n = default
    return n


def universe_limit_combo_value(n: int, *, default: int = 300) -> str:
    """콤보에 표시·설정할 문자열(100/300/500)."""
    v = int(n)
    if v in VALID_UNIVERSE_LIMITS:
        return str(v)
    return str(default)


def apply_pullback_scan_params_to_ui(
    ui: "BacktestGUI", params: "PullbackScanParams"
) -> None:
    """SSOT에서 확정된 스캔 파라미터를 좌측 입력 위젯에 주입."""
    from src.v3_scan_config import PullbackScanParams

    if not isinstance(params, PullbackScanParams):
        return
    ui.var_volume_burst_multiple.set(f"{params.volume_burst_multiple:g}")
    ui.var_vol_shrink_limit.set(f"{params.vol_shrink_limit:g}")
    ui.var_use_momentum_filter.set(bool(params.use_momentum_filter))
    if hasattr(ui, "combo_universe"):
        try:
            ui.combo_universe.set(universe_limit_combo_value(params.universe_limit))
        except (tk.TclError, AttributeError):
            pass


def apply_last_session_chrome_to_ui(ui: "BacktestGUI") -> bool:
    """last_session.json 의 시장·기간만 UI에 반영(스캔 수치는 resolve 쪽에서 처리)."""
    from src.v3_scan_config import read_last_session_mapping

    data = read_last_session_mapping()
    if not data:
        return False

    mk = str(data.get("market") or "").strip().upper()
    if mk in ("KOSPI", "KOSDAQ", "ETF"):
        ui.var_market.set(mk)

    for key, setter in (
        ("start_date", ui._date_start),
        ("end_date", ui._date_end),
    ):
        raw = str(data.get(key) or "").strip()[:10]
        if not raw:
            continue
        try:
            setter.set_date(datetime.strptime(raw, "%Y-%m-%d").date())
        except (ValueError, tk.TclError, AttributeError):
            pass
    return True


def bootstrap_gui_pullback_scan_ssot(ui: "BacktestGUI") -> None:
    """
    v3.70: YAML 마스터 → last_session.json 오버레이 → StringVar/콤보 주입.
    기간·시장은 세션 파일이 있으면 추가 반영.
    """
    from src.v3_scan_config import resolve_effective_pullback_scan_params

    params = resolve_effective_pullback_scan_params()
    apply_pullback_scan_params_to_ui(ui, params)
    apply_last_session_chrome_to_ui(ui)


def dump_last_gui_session(ui: "BacktestGUI") -> None:
    """v3.70: 종료 시 스캔 파라미터·시장·기간을 last_session.json 에 저장."""
    from src.v3_scan_config import default_pullback_scan_params

    ssot_default = default_pullback_scan_params().universe_limit
    try:
        start_s = ui._date_start.get_date().strftime("%Y-%m-%d")
        end_s = ui._date_end.get_date().strftime("%Y-%m-%d")
    except (ValueError, tk.TclError, AttributeError):
        start_s, end_s = "", ""
    raw_univ = (
        ui.combo_universe.get()
        if hasattr(ui, "combo_universe")
        else ssot_default
    )
    payload = {
        "version": 1,
        "market": str(ui.var_market.get()).strip().upper() or "KOSPI",
        "universe_limit": normalize_universe_limit_choice(
            raw_univ, default=ssot_default
        ),
        "start_date": start_s,
        "end_date": end_s,
        "volume_burst_multiple": str(ui.var_volume_burst_multiple.get()).strip(),
        "vol_shrink_limit": str(ui.var_vol_shrink_limit.get()).strip(),
        "use_momentum_filter": bool(ui.var_use_momentum_filter.get()),
    }
    os.makedirs(os.path.dirname(LAST_SESSION_JSON) or ".", exist_ok=True)
    with open(LAST_SESSION_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_last_gui_session(ui: "BacktestGUI") -> bool:
    """레거시 호환 — v3.70 `bootstrap_gui_pullback_scan_ssot` 사용 권장."""
    from src.v3_scan_config import read_last_session_mapping

    if read_last_session_mapping() is None:
        return False
    bootstrap_gui_pullback_scan_ssot(ui)
    return True

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
            font=gui_tk_font_pt(GUI_FONT_SIZE_PT),
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
            font=gui_tk_font_pt(GUI_FONT_SIZE_PT),
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
    """성공 시 좌측 패널 성과 요약 · v4.7 최고·최저 누적수익률 텍스트 이관 포함."""
    d = {row[0]: row[1] for row in res.summary_rows}
    final = d.get("최종 평가액", "-")
    tot = d.get("누적 수익률", "-")
    hl = d.get("최고/최저 수익률", "-")
    cagr = d.get("연평균 수익률", "-")
    mdd = d.get("최대 손실 낙폭", "-")
    return "\n".join(
        [
            f"■ 매매 횟수 : 매수 {res.n_buy}회 / 매도 {res.n_sell}회",
            f"■ 최종 평가액 : {final}",
            f"■ 누적 수익률 : {tot}",
            f"■ 최고/최저 수익률 : {hl}",
            f"■ 연평균 수익률 : {cagr}",
            f"■ 최대 손실 낙폭(MDD) : {mdd}",
        ]
    )


def _decimal_rate_to_pct_str(dec: float) -> str:
    """0.00015 → '0.015'처럼 % 입력란 표시 문자열."""
    x = float(dec) * 100.0
    s = f"{x:.8f}".rstrip("0").rstrip(".")
    return s if s else "0"


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
        mv = str(uni["market"]).strip().upper()
        if mv not in ("KOSPI", "KOSDAQ", "ETF"):
            mv = "KOSPI"
        ui.var_market.set(mv)
    if uni.get("search_keyword") is not None:
        # 빈 문자열도 허용(시장 전체 후보 목록 등). 문자열 타입 고정 및 앞뒤 공백 제거.
        ui.var_keyword.set(str(uni["search_keyword"]).strip())
    scr_yaml = uni.get("screener") if isinstance(uni.get("screener"), dict) else {}
    if hasattr(ui, "var_pf_mcap_top100"):
        defs = default_screener_pipeline_dict()
        pip = uni.get("screener_pipeline")
        applied = False
        if isinstance(pip, dict):
            ui.var_pf_mcap_top100.set(
                bool(pip.get("stage_mcap_top100", defs["stage_mcap_top100"]))
            )
            ui.var_pf_buy_rules.set(
                bool(pip.get("stage_buy_rules", defs["stage_buy_rules"]))
            )
            ui.var_pf_kim_candle.set(
                bool(pip.get("stage_kim_line_1bar", defs["stage_kim_line_1bar"]))
            )
            applied = True
        if not applied:
            sm_raw = uni.get("screener_mode")
            s1, s2, s3 = migrate_legacy_screener_mode_to_pipeline(sm_raw)
            ui.var_pf_mcap_top100.set(s1)
            ui.var_pf_buy_rules.set(s2)
            ui.var_pf_kim_candle.set(s3)
    elif hasattr(ui, "var_screener_mode"):
        sm_raw = uni.get("screener_mode")
        if isinstance(sm_raw, str) and sm_raw.strip() in VALID_GUI_SCREENER_MODES_FROZEN:
            ui.var_screener_mode.set(sm_raw.strip())
        elif isinstance(scr_yaml, dict) and "enabled" in scr_yaml:
            ui.var_screener_mode.set(
                GUI_SCREENER_MODE_SCREENER
                if bool(scr_yaml.get("enabled"))
                else GUI_SCREENER_MODE_WHOLE
            )
        else:
            ui.var_screener_mode.set(GUI_SCREENER_MODE_WHOLE)
    st = cfg.get("strategy", {})
    if st.get("interval"):
        ui.var_interval.set(str(st["interval"]).lower())
    if st.get("ma_period") is not None:
        mp = int(st["ma_period"])
        ui.var_ma_period.set(str(mp) if mp in (5, 10, 20) else "20")
    tf = trend_overlay_flags_from_strategy(st)
    for p in CHART_MA_TOGGLE_PERIODS:
        ui._trend_vars[p].set(tf[p])
    if "show_chart_candle" in st:
        ui.var_show_candle.set(bool(st["show_chart_candle"]))
    if "show_chart_volume" in st:
        ui.var_show_volume.set(bool(st["show_chart_volume"]))
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
    if "use_slope_acceleration" in st and hasattr(ui, "check_slope_accel_var"):
        ui.check_slope_accel_var.set(bool(st["use_slope_acceleration"]))
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
        ui.var_cash.set(f"{int(port['initial_cash']):,}")
    tc = cfg.get("trading_costs", {})
    if hasattr(ui, "var_buy_fee_pct") and tc.get("buy_cost") is not None:
        ui.var_buy_fee_pct.set(_decimal_rate_to_pct_str(float(tc["buy_cost"])))
    if hasattr(ui, "var_sell_fee_pct") and tc.get("sell_cost") is not None:
        ui.var_sell_fee_pct.set(_decimal_rate_to_pct_str(float(tc["sell_cost"])))


def _parse_pct_fee_field(s: str, label: str) -> float | None:
    """사용자 입력을 백테스트 소수 비율로 변환(숫자는 % 단위로 해석, 예 0.015 → 0.00015)."""
    t = str(s or "").strip().replace("%", "").replace(",", "").strip()
    if not t:
        return None
    try:
        v = float(t)
    except ValueError:
        messagebox.showerror("오류", f"{label}은(는) 숫자로 입력해 주세요.")
        return None
    if not (0.0 <= v < 50.0):
        messagebox.showerror("오류", f"{label}은(는) 합리적인 범위(0 이상 ~ 50 미만 %)로 입력해 주세요.")
        return None
    return v / 100.0


def _has_explicit_stock_selection(
    ui: "BacktestGUI",
    *,
    selected_code_override: str | None,
    universe_cfg: dict,
) -> bool:
    """
    검색어 없이 단일 종목 실행이 가능한지(코드 오버라이드·결과 목록 선택·YAML selected_code 등).
    """
    ov = ""
    if selected_code_override:
        ov = str(selected_code_override).strip().zfill(6)
        if ov and ov != "000000":
            return True

    sel = getattr(ui, "list_codes", None)
    try:
        cur = sel.curselection() if sel is not None else ()
    except tk.TclError:
        cur = ()
    if cur:
        try:
            line = sel.get(cur[0])  # type: ignore[union-attr]
            c = parse_gui_list_row_code(str(line))
        except (tk.TclError, IndexError):
            c = ""
        if c and c != "000000":
            return True

    yaml_cd = str(universe_cfg.get("selected_code") or "").strip().zfill(6)
    return bool(yaml_cd and yaml_cd != "000000")


def merge_live_trade_panel_into_strategy(
    ui: "BacktestGUI",
    strategy_out: dict,
    *,
    ma_period_preset: int | None = None,
    clamp_invalid_ma_period: bool = False,
) -> str | None:
    """
    차트 표시 플래그(`show_*`)를 제외한 **매매 신호·진입 필터·트레일** 키를
    우측 패널·주기 위젯으로 `strategy_out` 에 제자리 반영한다.

    - `try_build_config`·`extract_live_strategy_config` 가 **동일 규격**으로 사용한다.

    매개변수:
    - ``ma_period_preset``: ``None`` 이면 위젿에서 읽음. 검증 성공값(5·10·20)을 넘길 경우
      (백테스트 경로에서 이미 검증한 뒤) 그대로 사용한다.
    - ``clamp_invalid_ma_period``: 프리셋 없이 위젓 값이 무효일 때 ``20`` 으로 고정 검색 호환 경로용.

    반환값: 검증 거부 등 실패 메시지(한 줄), 성공 시 ``None``.
    """
    iv = ui.var_interval.get()
    if isinstance(iv, str) and iv.strip():
        strategy_out["interval"] = str(iv).strip().lower()

    if ma_period_preset is not None:
        mn = int(ma_period_preset)
        if mn not in (5, 10, 20):
            return "매매 기준 이평은 5·10·20일선 중 하나여야 합니다."
        strategy_out["ma_period"] = mn
    else:
        try:
            mn = int(ui.var_ma_period.get())
        except ValueError:
            if clamp_invalid_ma_period:
                mn = 20
            else:
                return "매매 기준 이평은 5·10·20일선 중 하나여야 합니다."
        if mn not in (5, 10, 20):
            if clamp_invalid_ma_period:
                mn = 20
            else:
                return "매매 기준 이평은 5·10·20일선 중 하나여야 합니다."
        strategy_out["ma_period"] = mn

    strategy_out["golden_buy_enabled"] = bool(ui.var_golden_buy.get())
    strategy_out["dead_cross_sell_enabled"] = bool(ui.var_dead_sell.get())

    try:
        slope_thr = float(str(ui.var_slope_threshold.get()).replace(",", "").strip())
    except ValueError:
        slope_thr = float(strategy_out.get("slope_threshold", 0.01))
    strategy_out["slope_threshold"] = slope_thr
    strategy_out["filter_trend_slope"] = bool(ui.var_filter_trend.get())
    strategy_out["filter_breakout_strength"] = bool(ui.var_filter_breakout.get())
    strategy_out["filter_time_buffer"] = bool(ui.var_filter_timebuf.get())

    if hasattr(ui, "check_slope_accel_var"):
        strategy_out["use_slope_acceleration"] = bool(ui.check_slope_accel_var.get())

    try:
        t_ref = float(str(ui.var_trailing_reference_pct.get()).replace(",", "").strip())
        t_below = float(str(ui.var_trailing_drop_below_pct.get()).replace(",", "").strip())
        t_above = float(str(ui.var_trailing_drop_above_pct.get()).replace(",", "").strip())
    except ValueError:
        return "가변 낙폭 매도 수치(기준·미달·돌파 %)는 숫자로 입력하세요."
    if t_ref <= 0 or t_below <= 0 or t_above <= 0:
        return "가변 낙폭 매도 기준 및 낙폭 값은 모두 양수여야 합니다."
    strategy_out["trailing_stop_enabled"] = bool(ui.var_trailing_stop.get())
    strategy_out["trailing_reference_pct"] = t_ref
    strategy_out["trailing_drop_below_pct"] = t_below
    strategy_out["trailing_drop_above_pct"] = t_above

    return None


def extract_live_strategy_config(ui: "BacktestGUI") -> dict:
    """
    YAML ``strategy`` 딥카피 + 현재 패널 **매매(신호·필터·트레일)** 위젯을 오버레이한다.

    `execute_pipelined_screening`·외부 검증 코드와 규격이 `try_build_config` 결과의
    해당 키와 동일해야 한다 — 내부적으로 `merge_live_trade_panel_into_strategy` 를 사용한다.

    **Tk 변수는 메인 스레드에서만 읽음.**
    """
    base = load_config()
    st = copy.deepcopy(dict(base.get("strategy") or {}))
    err = merge_live_trade_panel_into_strategy(
        ui,
        st,
        ma_period_preset=None,
        clamp_invalid_ma_period=True,
    )
    if err:
        raise RuntimeError(err)
    return st


def try_build_config(
    ui: "BacktestGUI",
    *,
    silent: bool = False,
    selected_code_override: str | None = None,
    period_nav: bool = False,
    market_override: str | None = None,
    search_keyword_override: str | None = None,
) -> dict | None:
    base = load_config()
    cfg = copy.deepcopy(base)
    if search_keyword_override is not None:
        kw = str(search_keyword_override).strip()
    else:
        kw = ui.var_keyword.get().strip()
    m_gui = ui.var_market.get().strip().upper() or "KOSPI"
    if m_gui not in ("KOSPI", "KOSDAQ", "ETF"):
        m_gui = "KOSPI"

    mo = normalize_krx_listing_market(market_override)
    lur = normalize_krx_listing_market(getattr(ui, "_last_run_listing_market", None))

    if mo is not None:
        effective_m = mo
    elif period_nav and lur is not None:
        effective_m = lur
    else:
        effective_m = m_gui

    cfg.setdefault("universe", {})["market"] = effective_m
    cfg["universe"]["search_keyword"] = kw

    uni_block = cfg.setdefault("universe", {})
    yaml_uni = base.get("universe") or {}
    yaml_scr = (
        yaml_uni.get("screener")
        if isinstance(yaml_uni.get("screener"), dict)
        else {}
    )
    pf_mcap = False
    pf_buy = False
    pf_kim = False
    gui_mode_placeholder = GUI_SCREENER_MODE_WHOLE
    if hasattr(ui, "var_pf_mcap_top100"):
        try:
            pf_mcap = bool(ui.var_pf_mcap_top100.get())
            pf_buy = bool(ui.var_pf_buy_rules.get())
            pf_kim = bool(ui.var_pf_kim_candle.get())
        except (tk.TclError, AttributeError):
            pf_mcap, pf_buy, pf_kim = False, False, False
    elif hasattr(ui, "var_screener_mode"):
        gmv = str(ui.var_screener_mode.get()).strip()
        gui_mode_placeholder = (
            gmv if gmv in VALID_GUI_SCREENER_MODES_FROZEN else GUI_SCREENER_MODE_WHOLE
        )
        pf_mcap, pf_buy, pf_kim = migrate_legacy_screener_mode_to_pipeline(
            gui_mode_placeholder
        )

    scr_merged = {**default_screener_config(), **yaml_scr}
    scr_merged["volatility_metric"] = "atr14"
    # GUI v4.14 라디오 제거 후에도 YAML `enabled` 로 CLI 배치 스크린 동작 유지
    uni_block["screener"] = scr_merged

    uni_block["screener_pipeline"] = {
        "stage_mcap_top100": pf_mcap,
        "stage_buy_rules": pf_buy,
        "stage_kim_line_1bar": pf_kim,
    }
    uni_block["screener_mode"] = "pipeline_and_v414"

    any_pf = pf_mcap or pf_buy or pf_kim

    if (
        not silent
        and not any_pf
        and not kw
        and not _has_explicit_stock_selection(
            ui,
            selected_code_override=selected_code_override,
            universe_cfg=uni_block,
        )
    ):
        if hasattr(ui, "set_status_message"):
            ui.set_status_message(
                "오류: 검색 종목명을 입력하거나, 필터 단계 중 하나 이상을 켠 뒤 검색하세요."
            )
        messagebox.showwarning(
            "입력 오류",
            "상단 종목 검색창을 입력하거나, 퀀트 필터 파이프라인 체크를 하나 이상 켠 뒤 검색할 수 있습니다.\n"
            "또는 검색 결과·이력에서 종목을 먼저 선택하세요.",
        )
        return None

    code: str
    ov = (
        str(selected_code_override).strip().zfill(6)
        if selected_code_override
        else ""
    )
    if ov and ov != "000000":
        code = ov
    else:
        # 차트 기간 패닝: 리스트/YAML 우회 없이 활성 종목 오버라이드만 허용(삼성 등 YAML 기본값 오염 방지)
        if period_nav:
            code = ""
        else:
            sel = ui.list_codes.curselection()
            if sel:
                line = ui.list_codes.get(sel[0])
                code = parse_gui_list_row_code(str(line)).strip().zfill(6)
            else:
                code = str((cfg.get("universe") or {}).get("selected_code") or "").strip()
                if (not code or code == "000000") and any_pf:
                    fallback = (
                        str((yaml_uni or {}).get("selected_code") or "").strip().zfill(6)
                    )
                    if fallback and fallback != "000000":
                        code = fallback

    code = code.zfill(6) if code else ""

    if not code or code == "000000":
        if not silent:
            hint = ""
            if any_pf:
                hint = (
                    "\n\n자동 스캔 모드에서는 검색 결과에서 종목을 고르거나, "
                    "settings 의 universe.selected_code 에 6자리 코드가 있어야 합니다."
                )
            messagebox.showwarning(
                "알림",
                "종목을 선택하세요.\n검색 결과 목록에서 한 줄을 선택하거나, 이력에서 종목을 고르거나, "
                "필요 시 config/settings.yaml 의 universe.selected_code 에 코드를 입력하세요."
                + hint,
            )
            if hasattr(ui, "set_status_message"):
                ui.set_status_message("종목을 선택한 뒤 백테스트를 실행하세요.")
        return None

    cfg["universe"]["selected_code"] = code

    try:
        ma_n = int(ui.var_ma_period.get())
    except ValueError:
        ma_n = 20
    if ma_n not in (5, 10, 20):
        messagebox.showerror("오류", "매매 기준 이평은 5·10·20일선 중 하나여야 합니다.")
        return None

    st = cfg.setdefault("strategy", {})
    m_err = merge_live_trade_panel_into_strategy(ui, st, ma_period_preset=ma_n)
    if m_err:
        messagebox.showerror("오류", m_err)
        return None

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

    buy_pct = getattr(ui, "var_buy_fee_pct", None)
    sell_pct = getattr(ui, "var_sell_fee_pct", None)
    if buy_pct is not None and sell_pct is not None:
        br = _parse_pct_fee_field(buy_pct.get(), "매수 수수료(%)")
        if br is None:
            return None
        sr = _parse_pct_fee_field(sell_pct.get(), "매도 수수료(%)")
        if sr is None:
            return None
        cfg.setdefault("trading_costs", {})["buy_cost"] = br
        cfg["trading_costs"]["sell_cost"] = sr

    for p in CHART_MA_TOGGLE_PERIODS:
        cfg.setdefault("strategy", {})[f"show_trend_ma{p}"] = bool(ui._trend_vars[p].get())
    for legacy in ("show_ma120", "show_ma200", "show_return_overlay"):
        cfg.get("strategy", {}).pop(legacy, None)

    cfg.setdefault("strategy", {})["show_chart_candle"] = bool(ui.var_show_candle.get())
    cfg.setdefault("strategy", {})["show_chart_volume"] = bool(ui.var_show_volume.get())
    cfg.get("strategy", {}).pop("show_chart_return", None)
    cfg.get("strategy", {}).pop("show_chart_scroll", None)

    return cfg


def refresh_search_listbox_from_screener_entries(
    ui: "BacktestGUI",
    entries: list[object],
    *,
    announce: bool = True,
) -> None:
    """
    스크리너 백엔드가 산출한 종목 목록을 검색 결과 리스트박스·`_candidates`와 동기화한다.

    Tkinter 메인 스레드에서만 호출해야 한다(일반적으로 `after`(0, …) 콜백 내부).
    """
    fn = getattr(ui, "update_gui_with_screener_results", None)
    if callable(fn):
        fn(entries, announce=announce)
