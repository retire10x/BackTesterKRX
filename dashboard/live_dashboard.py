"""
v11.2 Streamlit 실시간 관제 대시보드 (단일 SSOT).

  streamlit run dashboard/live_dashboard.py

data/live_trading.db — live_equity · live_trades
config/v11_dashboard_snapshot.json — 감시 유니버스 · 보유 포지션
"""
from __future__ import annotations

import json
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
SNAPSHOT_REL = "config/v11_dashboard_snapshot.json"
NAVER_CHART_URL = "https://finance.naver.com/item/main.naver?code={code}"
BOT_WAIT_MSG = "🤖 봇이 구동되기를 기다리는 중입니다... 봇을 먼저 실행해 주세요."
DEFAULT_CAPITAL = 2_000_000.0

st.set_page_config(
    page_title="v11.2 ORB Live Paper",
    page_icon="📡",
    layout="wide",
)

REFRESH_MS = 60_000

_LINK_COL = {
    "네이버 차트": st.column_config.LinkColumn("네이버 차트", display_text="📈 네이버 차트"),
}


@st.cache_resource
def _db() -> LiveDbManager:
    return LiveDbManager(project_root=project_root)


def _load_dashboard_snapshot() -> dict:
    path = project_root / SNAPSHOT_REL
    if not path.is_file():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _naver_link(code: str) -> str:
    return NAVER_CHART_URL.format(code=str(code).zfill(6))


def _fmt_volume(v: float) -> str:
    if v <= 0:
        return "—"
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.0f}K"
    return f"{v:,.0f}"


def _fmt_won(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{float(v):,.0f}원"


def _load_market_data(today: str) -> dict:
    """DB·스냅샷 일괄 로드 — 실패 시 빈 값 반환."""
    result: dict = {
        "latest_equity": None,
        "equity_rows": [],
        "trades": [],
        "snapshot": {},
        "error": None,
    }
    try:
        db = _db()
        result["latest_equity"] = db.fetch_latest_equity()
        result["equity_rows"] = db.fetch_equity_today(date_prefix=today)
        result["trades"] = list(reversed(db.fetch_trades_today(date_prefix=today)))
    except Exception as exc:
        result["error"] = str(exc)
    result["snapshot"] = _load_dashboard_snapshot()
    return result


def _bot_has_data(data: dict) -> bool:
    if data.get("latest_equity"):
        return True
    if data.get("equity_rows"):
        return True
    if data.get("snapshot", {}).get("watch_items"):
        return True
    if data.get("snapshot", {}).get("updated_at"):
        return True
    return False


def _resolve_balances(data: dict) -> tuple[float, float, float]:
    latest = data.get("latest_equity")
    snapshot = data.get("snapshot") or {}

    total_equity = float(
        (latest or {}).get("total_equity")
        or snapshot.get("total_equity")
        or DEFAULT_CAPITAL
    )
    used_cash = float((latest or {}).get("used_cash") or 0.0)
    available_cash = float(
        snapshot.get("available_cash")
        if snapshot.get("available_cash") is not None
        else max(0.0, total_equity - used_cash)
    )
    return total_equity, available_cash, used_cash


def _render_positions_table(positions: list[dict]) -> None:
    if not positions:
        st.info("현재 보유 종목 없음.")
        return
    pdf = pd.DataFrame(positions)
    pdf["평가손익률(%)"] = pdf["pnl_rate"].astype(float) * 100
    pdf["네이버 차트"] = pdf["code"].map(_naver_link)
    pdf = pdf[[
        "name", "code", "entry_price", "current_price", "qty", "평가손익률(%)", "네이버 차트",
    ]]
    pdf.columns = ["종목명", "코드", "매수가", "현재가", "수량", "평가손익률(%)", "네이버 차트"]
    st.dataframe(
        pdf,
        use_container_width=True,
        hide_index=True,
        column_config={
            "매수가": st.column_config.NumberColumn(format="%,.0f"),
            "현재가": st.column_config.NumberColumn(format="%,.0f"),
            "평가손익률(%)": st.column_config.NumberColumn(format="%.2f"),
            **_LINK_COL,
        },
    )


def _render_universe_table(watch_items: list[dict], snap_updated: str | None, watch_count: int) -> None:
    if snap_updated:
        st.caption(f"스냅샷 갱신: {snap_updated} · 감시 {watch_count}종")
    if not watch_items:
        st.info("감시 유니버스 없음 — 봇 기동 후 유니버스 수집이 완료되면 표시됩니다.")
        return
    rows = []
    for item in watch_items:
        code = str(item.get("code", "")).zfill(6)
        rows.append({
            "순위": item.get("rank"),
            "종목명": item.get("name") or code,
            "코드": code,
            "5일평균거래량": _fmt_volume(float(item.get("avg_volume") or 0)),
            "네이버 차트": _naver_link(code),
        })
    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
        column_config=_LINK_COL,
    )


def _render_trades_table(trades: list[dict]) -> None:
    if not trades:
        st.info("오늘 체결된 거래 없음.")
        return
    tdf = pd.DataFrame(trades)
    tdf["실현수익률(%)"] = tdf["pnl_rate"].astype(float) * 100
    tdf["네이버 차트"] = tdf["code"].map(_naver_link)
    tdf = tdf[[
        "entry_time", "exit_time", "name", "code",
        "buy_price", "sell_price", "qty", "실현수익률(%)", "exit_reason", "네이버 차트",
    ]]
    tdf.columns = [
        "진입시각", "청산시각", "종목명", "코드",
        "매수가", "매도가", "수량", "실현수익률(%)", "청산사유", "네이버 차트",
    ]
    st.dataframe(
        tdf,
        use_container_width=True,
        hide_index=True,
        column_config={
            "매수가": st.column_config.NumberColumn(format="%,.0f"),
            "매도가": st.column_config.NumberColumn(format="%,.0f"),
            "실현수익률(%)": st.column_config.NumberColumn(format="%.2f"),
            **_LINK_COL,
        },
    )


def _render_equity_chart(equity_rows: list[dict]) -> None:
    if not equity_rows:
        st.info("오늘 equity 스냅샷 없음 — 봇 기동 후 1분마다 기록됩니다.")
        return
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


def main() -> None:
    if st_autorefresh:
        st_autorefresh(interval=REFRESH_MS, key="v11_live_refresh")
    else:
        st.caption("💡 `pip install streamlit-autorefresh` 설치 시 1분 자동 새로고침 활성화")

    now_kst = datetime.now(KST)
    today = now_kst.strftime("%Y-%m-%d")
    current_time = now_kst.strftime("%Y-%m-%d %H:%M:%S")

    st.markdown(f"### ⏱️ 현재 관제 시각: `{current_time}` (1분 자동 갱신)")
    st.title("📡 v11.2 ORB Live Paper — 실시간 관제탑")
    st.caption(f"SSOT DB: `{default_db_path(project_root)}` · 스냅샷: `{SNAPSHOT_REL}`")

    try:
        data = _load_market_data(today)
    except Exception as exc:
        st.error(f"데이터 로드 중 오류가 발생했습니다: {exc}")
        st.info(BOT_WAIT_MSG)
        return

    if data.get("error"):
        st.warning(f"DB 조회 경고: {data['error']}")

    if not _bot_has_data(data):
        st.info(BOT_WAIT_MSG)

    snapshot = data.get("snapshot") or {}
    total_equity, available_cash, used_cash = _resolve_balances(data)
    positions = snapshot.get("positions") or []
    watch_items = snapshot.get("watch_items") or []
    snap_updated = snapshot.get("updated_at")
    watch_count = int(snapshot.get("watch_count") or len(watch_items))

    # ── [Top] 실시간 잔고 현황 ──────────────────────────────────────────
    st.subheader("💰 실시간 잔고 현황")
    m1, m2, m3 = st.columns(3)
    m1.metric("총자산 (Total Equity)", _fmt_won(total_equity))
    m2.metric("가용 현금 (Available Cash)", _fmt_won(available_cash))
    m3.metric("사용 중인 자산 (Used Cash)", _fmt_won(used_cash))

    open_slots = int(snapshot.get("open_slot_count") or len(positions))
    max_slots = int(snapshot.get("max_slots") or 4)
    if snap_updated:
        st.caption(
            f"보유 슬롯 {open_slots}/{max_slots} · "
            f"스냅샷 {snap_updated}"
        )

    st.divider()

    # ── 자산 궤적 (매 분 live_equity 갱신 · 1분 자동 새로고침) ───────────
    st.subheader("📊 자산 궤적 (09:00~)")
    _render_equity_chart(data.get("equity_rows") or [])

    st.divider()

    # ── [Middle] 보유 포지션(좌) · 감시 유니버스(우) ─────────────────────
    col_pos, col_uni = st.columns(2)

    with col_pos:
        st.subheader("💼 현재 보유 포지션")
        _render_positions_table(positions)

    with col_uni:
        st.subheader("🎯 오늘 감시 유니버스")
        _render_universe_table(watch_items, snap_updated, watch_count)

    st.divider()

    # ── [Bottom] 당일 매매 이력 (최신순) ─────────────────────────────────
    st.subheader("📜 당일 매수·매도 누적 거래 이력")
    _render_trades_table(data.get("trades") or [])

    with st.expander("📋 운영 안내"):
        st.markdown(
            "- **봇 실행**: `python run_v11_live_paper_trading.py`\n"
            "- **Mock 검증**: `python run_v11_live_paper_trading.py --mock --speed 0`\n"
            "- **로그**: `logs/v11_live_paper.log`\n"
            "- **전략 가이드**: `docs/v11_2_strategy_guide.md`"
        )


if __name__ == "__main__":
    main()
