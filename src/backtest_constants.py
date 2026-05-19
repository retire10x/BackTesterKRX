"""
백테스트 파이프라인·차트가 공유하는 상수.
(metrics / backtest_chart / gui_helpers 경계에서 순환 참조를 피하기 위해 분리.)
"""

from __future__ import annotations

# 차트 표시용 추세 이평 기간 → 선색 (매매 기준 이평과 겹치면 해당 추세선 스킵)
TREND_MA_PERIODS = (5, 10, 20, 60, 120, 200)
TREND_MA_COLORS: dict[int, str] = {
    5: "#5d4037",
    10: "#00695c",
    20: "#558b2f",
    60: "#f9a825",
    120: "#ff6f00",
    200: "#6a1b9a",
}

# 타점 마커 — 데이터 앵커(저가/고가) + offset points 고정 간격(v3.3·v3.4 매칭)
TRADE_MARKER_OFFSET_PT = 15.0
MARKER_BUY_COLOR = "#2e7d32"
MARKER_BUY_OUTLINE = "#1b5e20"
MARKER_SELL_COLOR = "#fdd835"
MARKER_SELL_OUTLINE = "#b45309"
MARKER_ANNOT_SIZE = 11

# make_backtest_figure 가 생성한 Figure 에 부착: 차트에서 스킵된 매매 타점 건수(v3.5)
FIG_ATTR_TRADE_MARKERS_SKIPPED = "_trade_markers_skipped"
