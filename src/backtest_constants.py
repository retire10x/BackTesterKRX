"""
백테스트 파이프라인·차트가 공유하는 상수.
(metrics / backtest_chart / gui_helpers 경계에서 순환 참조를 피하기 위해 분리.)
"""

from __future__ import annotations

# 차트 표시용 추세 이평 기간 → 선색 (매매 기준 이평과 겹치면 해당 추세선 스킵)
TREND_MA_PERIODS = (5, 10, 20, 60, 120, 200)
# v3.15 GUI 차트 상단 토글 대상 (5중 이평)
CHART_MA_TOGGLE_PERIODS = (5, 10, 20, 60, 120)
TREND_MA_COLORS: dict[int, str] = {
    5: "magenta",
    10: "cyan",
    20: "green",
    60: "purple",
    120: "orange",
    200: "#6a1b9a",
}

# 차트 추세선 선 굵기 — Legend 라인 샘플과 실제 플롯을 1:1로 맞춤(v3.15: 기간별)
TREND_MA_LINEWIDTHS: dict[int, float] = {
    5: 0.8,
    10: 0.8,
    20: 1.5,
    60: 1.0,
    120: 2.0,
    200: 0.95,
}
TREND_MA_LINEWIDTH = 0.95

# 타점 마커 — 데이터 앵커(저가/고가) + offset points 고정 간격(v3.3·v3.4 매칭)
TRADE_MARKER_OFFSET_PT = 15.0
MARKER_BUY_COLOR = "#2e7d32"
MARKER_BUY_OUTLINE = "#1b5e20"
MARKER_SELL_COLOR = "#fdd835"
MARKER_SELL_OUTLINE = "#b45309"
# v4.4 가변 낙폭 매도(TRAIL STOP) 타점 — MA 데드크로스 매도와 구분
MARKER_TRAIL_STOP_COLOR = "#ffea00"
MARKER_TRAIL_STOP_OUTLINE = "#f57f17"
MARKER_ANNOT_SIZE = 9

# make_backtest_figure 가 생성한 Figure 에 부착: 차트에서 스킵된 매매 타점 건수(v3.5)
FIG_ATTR_TRADE_MARKERS_SKIPPED = "_trade_markers_skipped"
FIG_ATTR_NO_XDATE_LABELS = "_btkrx_no_xdate_labels"  # 레거시 alias
FIG_ATTR_PRICE_PANEL_XDATE = "_btkrx_price_panel_xdate"

# GUI 차트 휠 줌
CHART_ZOOM_MIN_VISIBLE_BARS = 12
CHART_ZOOM_WHEEL_FACTOR = 0.82
