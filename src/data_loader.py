import datetime

import FinanceDataReader as fdr
import pandas as pd


class DataLoader:
    def __init__(self, config):
        self.config = config
        self.market = config["universe"]["market"]
        self.keyword = config["universe"]["search_keyword"]

    def fetch_universe_tickers(self):
        """KOSPI 전종목을 가져온 후 키워드 검색 필터링 적용"""
        print(f"[{self.market}] 전종목 리스트를 확보하는 중입니다...")
        stocks = fdr.StockListing(self.market)

        if self.keyword and self.keyword.strip() != "":
            filtered_stocks = stocks[
                stocks["Name"].str.contains(self.keyword, na=False)
            ]
            print(
                f"[검색] '{self.keyword}' 필터링 결과: {len(filtered_stocks)}개 종목 발견"
            )
        else:
            filtered_stocks = stocks
            print(
                f"[안내] 키워드 없음: 전종목({len(filtered_stocks)}개)을 대상으로 진행합니다."
            )

        return dict(zip(filtered_stocks["Code"], filtered_stocks["Name"]))

    def load_ohlcv_with_warmup(self, code, start_date, end_date):
        """이평선 계산 NaN 오류 방지를 위해 시작일 이전부터 데이터를 로드 (warm-up)"""
        start_dt = datetime.datetime.strptime(start_date, "%Y-%m-%d")
        warmup_start_dt = start_dt - datetime.timedelta(days=60)
        warmup_start_str = warmup_start_dt.strftime("%Y-%m-%d")

        try:
            df = fdr.DataReader(code, start=warmup_start_str, end=end_date)
            if df.empty:
                return None
            return df
        except Exception as e:
            print(f"[오류] [{code}] 데이터 로드 실패: {e}")
            return None
