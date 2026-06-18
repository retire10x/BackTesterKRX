"""
v11.2 Streamlit 실시간 관제 대시보드.

  streamlit run dashboard/live_dashboard.py

data/live_trading.db 의 live_equity · live_trades 를 1분마다 자동 갱신.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

from src.live.live_db_manager import LiveDbManager, default_db_path

KST = ZoneInfo("Asia/Seoul")

st.set_page_config(
    page_title="v11.2 ORB Live Paper",
    page_icon="📡",
    layout="wide",
)

REFRESH_MS = 60_000


@st.cache_resource
def _db() -> LiveDbManager:
    return LiveDbManager(project_root=project_root)


def _load_capital_buffer_safe() -> float:
    try:
        from src.engine.capital_buffer_manager import load_capital_buffer
        mgr = load_capital_buffer(project_root=project_root)
        return float(mgr.safe_vault)
    except Exception:
        return 0.0


def main() -> None:
    if st_autorefresh:
        st_autorefresh(interval=REFRESH_MS, key="v11_live_refresh")
    else:
        st.caption("💡 `pip install streamlit-autorefresh` 설치 시 1분 자동 새로고침 활성화")

    now_kst = datetime.now(KST)
    today = now_kst.strftime("%Y-%m-%d")
    current_time = now_kst.strftime("%Y-%m-%d %H:%M:%S")
    st.markdown(f"### ⏱️ 현재 관제 시각: `{current_time}` (1분 자동 갱신 중)")
    st.title("📡 v11.2 ORB Live Paper — 3중 관제탑")
    st.caption(f"DB: {default_db_path(project_root)}")

    db = _db()
    latest = db.fetch_latest_equity()
    trades = db.fetch_trades_today(date_prefix=today)
    equity_rows = db.fetch_equity_today(date_prefix=today)

    total_equity = float(latest["total_equity"]) if latest else 2_000_000.0
    safe_vault = float(latest["safe_vault"]) if latest else _load_capital_buffer_safe()
    today_pnl = db.today_realized_pnl_rate(date_prefix=today)

    c1, c2, c3 = st.columns(3)
    c1.metric("💰 현재 총자산", f"{total_equity:,.0f}원")
    c2.metric("📈 오늘 실현 손익", f"{today_pnl * 100:+.2f}%")
    c3.metric("🏦 Safe Vault", f"{safe_vault:,.0f}원")

    st.subheader("📊 자산 궤적 (09:00~)")
    if equity_rows:
        eq_df = pd.DataFrame(equity_rows)
        eq_df["timestamp"] = pd.to_datetime(eq_df["timestamp"])
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=eq_df["timestamp"],
            y=eq_df["total_equity"],
            mode="lines+markers",
            name="총자산",
            line=dict(color="#1f77b4", width=2),
            marker=dict(size=7, symbol="circle"),
        ))
        fig.update_layout(
            xaxis_title="시각",
            yaxis_title="총자산 (원)",
            hovermode="x unified",
            height=400,
            margin=dict(l=40, r=20, t=30, b=40),
        )
        fig.update_yaxes(tickformat=",")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("오늘 equity 스냅샷 없음 — 봇 기동 후 1분마다 기록됩니다.")

    st.subheader("🎯 실시간 타격 로그")
    if trades:
        tdf = pd.DataFrame(trades)
        tdf["pnl_pct"] = tdf["pnl_rate"].astype(float) * 100
        tdf = tdf[[
            "exit_time", "name", "code", "buy_price", "sell_price",
            "qty", "pnl_pct", "exit_reason",
        ]]
        tdf.columns = [
            "청산시각", "종목", "코드", "매수가", "매도가",
            "수량", "수익률(%)", "사유",
        ]
        st.dataframe(tdf, use_container_width=True, hide_index=True)
    else:
        st.info("오늘 체결된 거래 없음.")

    with st.expander("📋 감시 종목 / 설정"):
        st.markdown(
            "- **봇 실행**: `python run_v11_live_paper_trading.py`\n"
            "- **Mock 검증**: `python run_v11_live_paper_trading.py --mock --speed 0`\n"
            "- **로그**: `logs/v11_live_paper.log`"
        )


if __name__ == "__main__":
    main()
