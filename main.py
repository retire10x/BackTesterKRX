"""
BackTesterKRX — 일봉·주봉 · 싱글 종목 백테스트
설정: config/settings.yaml · CLI로 덮어쓰기 가능 · 후보: python main.py --list
"""
import argparse
import copy
import datetime
import math
import os
import sys

import FinanceDataReader as fdr
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml
from tabulate import tabulate

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (OSError, ValueError):
        pass

_WARMUP_DAYS_DAILY = 120
_WARMUP_DAYS_FOR_WEEKLY = 800


def _setup_korean_font():
    for font in ("Malgun Gothic", "AppleGothic", "NanumGothic", "DejaVu Sans"):
        try:
            plt.rcParams["font.family"] = font
            plt.rcParams["axes.unicode_minus"] = False
            return
        except Exception:
            continue
    plt.rcParams["axes.unicode_minus"] = False


def load_config(path: str | None = None):
    cfg_path = path or os.path.join("config", "settings.yaml")
    with open(cfg_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def merge_cli_into_config(cfg: dict, args: argparse.Namespace) -> dict:
    out = copy.deepcopy(cfg)
    if args.start_date:
        out.setdefault("period", {})["start_date"] = args.start_date
    if args.end_date:
        out.setdefault("period", {})["end_date"] = args.end_date
    if args.market:
        out.setdefault("universe", {})["market"] = args.market
    if args.search_keyword is not None:
        out.setdefault("universe", {})["search_keyword"] = args.search_keyword
    if args.code:
        out.setdefault("universe", {})["selected_code"] = args.code
    if args.ma_period is not None:
        out.setdefault("strategy", {})["ma_period"] = args.ma_period
    if args.interval:
        out.setdefault("strategy", {})["interval"] = args.interval
    return out


def fetch_filtered_universe(market: str, keyword: str) -> dict[str, str]:
    """종목 리스트에서 이름 키워드로 필터. keyword 가 비면 전체."""
    stocks = fdr.StockListing(market)
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


def print_candidate_list(universe: dict[str, str]) -> None:
    rows = [[c, n] for c, n in sorted(universe.items())]
    print(tabulate(rows, headers=["코드", "종목명"], tablefmt="grid"))
    print(f"\n[안내] 후보 {len(rows)}개. universe.selected_code 에 하나를 적거나 --code 로 넣으세요.")


def ensure_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index)
    return out.sort_index()


def resample_weekly_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """
    일봉 → 주봉. 한국 주식 관례에 가깝게 **금요일 말** 기준 주간 봉.
    Open=첫 거래일 시가, High/Low=구간 최고·최저, Close=마지막 거래일 종가.
    """
    d = ensure_datetime_index(df)
    agg: dict = {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
    if "Volume" in d.columns:
        agg["Volume"] = "sum"
    elif "Amount" in d.columns:
        agg["Amount"] = "sum"
    w = d.resample("W-FRI", label="right", closed="right").agg(agg)
    return w.dropna(how="any", subset=["Open", "High", "Low", "Close"])


def add_signals(df: pd.DataFrame, ma_period: int) -> pd.DataFrame:
    df = df.copy()
    col_ma = f"MA{ma_period}"
    df[col_ma] = df["Close"].rolling(window=ma_period).mean()
    df["Prev_Close"] = df["Close"].shift(1)
    df[col_ma + "_prev"] = df[col_ma].shift(1)
    df["Signal"] = 0
    buy = (df["Prev_Close"] <= df[col_ma + "_prev"]) & (df["Close"] > df[col_ma])
    sell = (df["Prev_Close"] >= df[col_ma + "_prev"]) & (df["Close"] < df[col_ma])
    df.loc[buy, "Signal"] = 1
    df.loc[sell, "Signal"] = -1
    return df


def simulate_single(df: pd.DataFrame, start_date: str, initial: float, buy_cost: float, sell_cost: float):
    """봉 종가에서 신호 확정 → 다음 봉 시가 체결. 전액 매수/전액 매도.
    반환: (결과 DF, 체결 목록). 체결 목록은 실제 체결이 일어난 봉의 시가·날짜."""
    start_ts = pd.Timestamp(start_date)
    d = df.loc[df.index >= start_ts].copy()
    if d.empty or len(d) < 2:
        return None

    past = df.loc[df.index < start_ts]
    pending = int(past["Signal"].iloc[-1]) if len(past) else 0

    cash = float(initial)
    shares = 0
    position = 0
    equity = []
    trades: list[dict] = []

    for i in range(len(d)):
        o = d["Open"].iloc[i]
        cl = d["Close"].iloc[i]
        sig = int(d["Signal"].iloc[i])

        if pending == 1 and position == 0:
            if pd.notna(o) and o > 0 and cash > 0:
                sh = math.floor(cash / (o * (1 + buy_cost)))
                if sh > 0:
                    cash -= sh * o * (1 + buy_cost)
                    position = 1
                    shares = sh
                    trades.append(
                        {"date": d.index[i], "side": "BUY", "price": float(o)}
                    )
        elif pending == -1 and position == 1:
            if pd.notna(o) and o > 0 and shares > 0:
                cash += shares * o * (1 - sell_cost)
                trades.append(
                    {"date": d.index[i], "side": "SELL", "price": float(o)}
                )
                shares = 0
                position = 0

        eq = cash + shares * (cl if pd.notna(cl) else 0)
        equity.append(eq)
        pending = sig

    out = d.copy()
    out["Equity"] = equity
    return out, trades


def metrics_total_cagr_mdd_equity(equity: pd.Series, initial: float, bars_per_year: float):
    """누적수익률(%), CAGR(%), MDD(%). CAGR는 봉 수 기준 년 환산(일봉 252, 주봉 52)."""
    ret_pct = (equity / float(initial) - 1.0) * 100.0
    n = len(equity)
    if n < 2:
        return 0.0, 0.0, 0.0, ret_pct

    total_ret = float(ret_pct.iloc[-1])
    years = n / float(bars_per_year)
    if years <= 0:
        cagr_pct = 0.0
    else:
        ratio = float(equity.iloc[-1]) / float(initial)
        cagr_pct = (ratio ** (1.0 / years) - 1.0) * 100.0

    peak_eq = equity.cummax()
    dd = np.where(peak_eq > 1e-12, (peak_eq - equity) / peak_eq, 0.0)
    mdd_pct = float(np.nanmax(dd)) * 100.0 if len(dd) else 0.0

    return total_ret, cagr_pct, mdd_pct, ret_pct


def normalize_interval(s: str) -> str:
    x = (s or "daily").strip().lower()
    if x in ("d", "day", "daily", "일", "일봉"):
        return "daily"
    if x in ("w", "week", "weekly", "주", "주봉"):
        return "weekly"
    raise ValueError(f"지원하지 않는 interval: {s} (daily 또는 weekly)")


def run_backtest(cfg: dict, override_code: str | None = None) -> bool:
    period = cfg.get("period", {})
    start = period.get("start_date")
    end = period.get("end_date")
    uni = cfg.get("universe", {})
    market = uni.get("market", "KOSPI")
    keyword = uni.get("search_keyword", "") or ""
    selected = (override_code or uni.get("selected_code") or "").strip().zfill(6)

    st = cfg.get("strategy", {})
    ma_n = int(st.get("ma_period", 20))
    interval = normalize_interval(str(st.get("interval", "daily")))

    costs = cfg.get("trading_costs", {})
    buy_c = float(costs.get("buy_cost", 0.00015))
    sell_c = float(costs.get("sell_cost", 0.0020))

    port = cfg.get("portfolio", {})
    initial = float(port.get("initial_cash", 5_000_000))

    if not selected or selected == "000000":
        print("[오류] universe.selected_code 가 비었습니다. python main.py --list 로 후보를 확인하세요.")
        return False

    candidates = fetch_filtered_universe(market, keyword)
    if selected not in candidates:
        print(
            f"[오류] 코드 {selected} 가 현재 필터 리스트에 없습니다. "
            f"키워드: '{keyword or '(전체)'}' 시장: {market}"
        )
        return False

    name = candidates[selected]
    bar_label = "주봉" if interval == "weekly" else "일봉"
    bars_per_year = 52.0 if interval == "weekly" else 252.0

    print(
        f"[시작] {start} ~ {end} | {name} ({selected}) | {bar_label} | 이평 {ma_n} | 초기 {initial:,.0f}원 전액"
    )

    start_dt = datetime.datetime.strptime(start, "%Y-%m-%d")
    warm_days = _WARMUP_DAYS_FOR_WEEKLY if interval == "weekly" else _WARMUP_DAYS_DAILY
    warm = (start_dt - datetime.timedelta(days=warm_days)).strftime("%Y-%m-%d")

    try:
        raw = fdr.DataReader(selected, start=warm, end=end)
    except Exception as e:
        print(f"[오류] 데이터 로드 실패: {e}")
        return False

    if raw is None or raw.empty:
        print("[오류] 가격 데이터가 없습니다.")
        return False

    raw = ensure_datetime_index(raw)
    if interval == "weekly":
        bars = resample_weekly_ohlcv(raw)
    else:
        bars = raw

    if len(bars) < ma_n + 5:
        print("[오류] 봉 데이터가 너무 적습니다. 기간·warm-up·interval 을 확인하세요.")
        return False

    sig_df = add_signals(bars, ma_n)
    res = simulate_single(sig_df, start, initial, buy_c, sell_c)
    if res is None:
        print("[오류] 시뮬 구간이 너무 짧습니다.")
        return False
    sim, trades = res

    eq = sim["Equity"]
    total_r, cagr_r, mdd_r, ret_series = metrics_total_cagr_mdd_equity(eq, initial, bars_per_year)
    final_eq = float(eq.iloc[-1])

    summary = [
        ["종목", f"{name} ({selected})"],
        ["봉 주기", bar_label],
        ["초기 자산", f"{initial:,.2f} 원"],
        ["최종 평가액", f"{final_eq:,.2f} 원"],
        ["누적 수익률", f"{total_r:.2f} %"],
        ["CAGR", f"{cagr_r:.2f} %"],
        ["MDD", f"{mdd_r:.2f} %"],
    ]
    print("\n" + "=" * 52)
    print(" 백테스트 성과 (싱글 종목)")
    print("=" * 52)
    print(tabulate(summary, tablefmt="grid"))

    _setup_korean_font()
    os.makedirs("output", exist_ok=True)
    out_png = os.path.join("output", "backtest_report.png")

    buys = [t for t in trades if t["side"] == "BUY"]
    sells = [t for t in trades if t["side"] == "SELL"]

    fig, (ax_price, ax_ret) = plt.subplots(
        2,
        1,
        figsize=(12, 8),
        sharex=True,
        gridspec_kw={"height_ratios": [2.0, 1.0]},
    )

    ax_price.plot(
        sim.index,
        sim["Close"].values,
        color="#333333",
        linewidth=1.2,
        label="종가",
    )
    if buys:
        ax_price.scatter(
            [t["date"] for t in buys],
            [t["price"] for t in buys],
            marker="^",
            s=120,
            c="red",
            edgecolors="darkred",
            linewidths=0.8,
            zorder=5,
            label="매수 체결 (익봉 시가)",
        )
    if sells:
        ax_price.scatter(
            [t["date"] for t in sells],
            [t["price"] for t in sells],
            marker="v",
            s=120,
            c="blue",
            edgecolors="navy",
            linewidths=0.8,
            zorder=5,
            label="매도 체결 (익봉 시가)",
        )
    ax_price.set_ylabel("가격 (원)")
    ax_price.grid(True, linestyle="--", alpha=0.45)
    ax_price.legend(loc="upper left", fontsize=9)
    ax_price.set_title(
        f"{name} · {bar_label} · {ma_n}봉 이평 | 주가·매매 타점",
        fontsize=13,
        pad=10,
    )

    ax_ret.plot(
        sim.index,
        ret_series.values,
        color="royalblue",
        linewidth=2,
        label="누적 수익률 (%)",
    )
    ax_ret.set_xlabel("날짜 (봉 기준)")
    ax_ret.set_ylabel("수익률 (%)")
    ax_ret.grid(True, linestyle="--", alpha=0.45)
    ax_ret.legend(loc="upper left", fontsize=9)

    fig.tight_layout()
    fig.savefig(out_png, dpi=300)
    plt.close(fig)
    print(f"\n[그래프] 저장: {out_png} (매수 {len(buys)}회 / 매도 {len(sells)}회)")
    return True


def main():
    ap = argparse.ArgumentParser(
        description="BackTesterKRX — 일봉/주봉 · 싱글 종목 (설정은 YAML, 옵션으로 덮어쓰기)"
    )
    ap.add_argument("--list", action="store_true", help="키워드 필터 후보만 출력하고 종료")
    ap.add_argument("--config", type=str, default=None, help="설정 YAML 경로")
    ap.add_argument("--code", type=str, default=None, help="종목코드 6자리 (YAML 덮어쓰기)")
    ap.add_argument(
        "--interval",
        choices=["daily", "weekly"],
        default=None,
        help="봉 주기: daily(일봉), weekly(주봉). 미지정 시 YAML strategy.interval",
    )
    ap.add_argument("--start", dest="start_date", default=None, help="시작일 YYYY-MM-DD")
    ap.add_argument("--end", dest="end_date", default=None, help="종료일 YYYY-MM-DD")
    ap.add_argument("--keyword", dest="search_keyword", default=None, help="종목명 검색 키워드 (빈칸 허용)")
    ap.add_argument("--market", type=str, default=None, help="시장 (예: KOSPI)")
    ap.add_argument("--ma", type=int, dest="ma_period", default=None, help="이평선 N")
    args = ap.parse_args()

    base = load_config(args.config)
    cfg = merge_cli_into_config(base, args)

    if args.list:
        uni = cfg.get("universe", {})
        m = uni.get("market", "KOSPI")
        kw = uni.get("search_keyword", "") or ""
        cand = fetch_filtered_universe(m, kw)
        print(f"[{m}] 키워드: '{kw or '(전체)'}' 후보 {len(cand)}개\n")
        print_candidate_list(cand)
        return

    ok = run_backtest(cfg, override_code=args.code)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
