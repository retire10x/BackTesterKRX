"""
데이터 수집·정렬·주봉 집계·설정 YAML 로드 (FinanceDataReader 등).
필요 시 pykrx 등으로 확장. GUI 비의존.
"""
from __future__ import annotations

import calendar
import os
from datetime import date

import FinanceDataReader as fdr
import pandas as pd
import yaml


def months_before(d: date, months: int) -> date:
    """동일 일 기준으로 월을 되돌림(말일 초과 시 해당 월 말일로 클램프)."""
    y, m = d.year, d.month
    m -= months
    while m <= 0:
        m += 12
        y -= 1
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last))


def default_backtest_period_range() -> tuple[date, date]:
    """시작=오늘 기준 6개월 전, 종료=오늘."""
    today = date.today()
    return months_before(today, 6), today


# 일봉: 사용자 시작일 이전 최소 이 거래일만큼 OHLCV 를 당겨와 MA120·v4.0 기울기 필터 워밍업
OHLCV_EXTRA_TRADING_BARS_DAILY = 130
# 주봉: 일봉 원천을 충분히 길게 로드한 뒤 주간 리샘플 (약 130주 + 여유)
OHLCV_WEEKLY_FETCH_CALENDAR_DAYS = 980


def ohlcv_warm_start_date(user_start: str, *, interval: str) -> str:
    """
    차트·시뮬에 쓰는 사용자 시작일은 그대로 두되, OHLCV 로드 시작일만 더 과거로 당김.
    일봉: 거래일 기준 OHLCV_EXTRA_TRADING_BARS_DAILY 만큼 이전부터.
    주봉: 일봉 시계열을 넉넉히 가져온 뒤 주봉으로 집계하므로 캘린더 일 단위로 과거 확장.
    """
    iv = str(interval).strip().lower()
    ts = pd.Timestamp(str(user_start).strip()[:10])
    if iv == "weekly":
        warm_ts = ts - pd.Timedelta(days=OHLCV_WEEKLY_FETCH_CALENDAR_DAYS)
    else:
        warm_ts = ts - pd.offsets.BDay(OHLCV_EXTRA_TRADING_BARS_DAILY)
    return warm_ts.strftime("%Y-%m-%d")


def load_config(path: str | None = None) -> dict:
    """config/settings.yaml 로드. 기간이 비어 있으면 6개월 전~오늘로 채움."""
    cfg_path = path or os.path.join("config", "settings.yaml")
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if cfg is None:
        cfg = {}
    period = cfg.setdefault("period", {})
    ds, de = period.get("start_date"), period.get("end_date")
    if not str(ds or "").strip() or not str(de or "").strip():
        s_d, e_d = default_backtest_period_range()
        if not str(ds or "").strip():
            period["start_date"] = s_d.strftime("%Y-%m-%d")
        if not str(de or "").strip():
            period["end_date"] = e_d.strftime("%Y-%m-%d")
    return cfg


def fdr_stock_listing(market: str) -> pd.DataFrame | None:
    """FinanceDataReader StockListing 호출 전 시장 라벨 정규화."""
    m = str(market or "").strip().upper()
    if m == "ETF":
        return fdr.StockListing("ETF/KR")
    return fdr.StockListing(m)


def fetch_filtered_universe(market: str, keyword: str) -> dict[str, str]:
    """종목 리스트에서 이름 키워드로 필터. keyword 가 비면 전체."""
    stocks = fdr_stock_listing(market)
    if stocks is None or stocks.empty:
        return {}
    if "Code" not in stocks.columns or "Name" not in stocks.columns:
        return {}
    if keyword and str(keyword).strip():
        kw = str(keyword).strip()
        mask = stocks["Name"].astype(str).str.contains(kw, na=False)
        sub = stocks.loc[mask].copy()
    else:
        sub = stocks.copy()
    codes = sub["Code"].astype(str).str.zfill(6)
    names = sub["Name"].astype(str)
    return dict(zip(codes, names))


def ensure_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index)
    return out.sort_index()


def resample_weekly_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """
    일봉 → 주봉. 한국 주식 관례에 가깝게 **금요일 말** 기준 주간 봉.
    """
    d = ensure_datetime_index(df)
    agg: dict = {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
    if "Volume" in d.columns:
        agg["Volume"] = "sum"
    elif "Amount" in d.columns:
        agg["Amount"] = "sum"
    w = d.resample("W-FRI", label="right", closed="right").agg(agg)
    return w.dropna(how="any", subset=["Open", "High", "Low", "Close"])


def load_ohlcv(symbol: str, start: str, end: str) -> pd.DataFrame | None:
    """FinanceDataReader 로 OHLCV. 실패·빈 데이터 시 None."""
    try:
        df = fdr.DataReader(symbol, start=start, end=end)
        if df is None or df.empty:
            return None
        return df
    except Exception:
        return None
