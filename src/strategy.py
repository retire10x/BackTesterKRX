import pandas as pd


class SmaCrossStrategy:
    def __init__(self, window=20):
        self.window = window

    def generate_signals(self, df):
        """20일 이평선 돌파 시그널 생성 (1: 매수, -1: 매도, 0: 관망)"""
        df = df.copy()

        df["MA20"] = df["Close"].rolling(window=self.window).mean()

        df["Signal"] = 0

        df["Prev_Close"] = df["Close"].shift(1)
        df["Prev_MA20"] = df["MA20"].shift(1)

        buy_cond = (df["Prev_Close"] <= df["Prev_MA20"]) & (
            df["Close"] > df["MA20"]
        )
        sell_cond = (df["Prev_Close"] >= df["Prev_MA20"]) & (
            df["Close"] < df["MA20"]
        )

        df.loc[buy_cond, "Signal"] = 1
        df.loc[sell_cond, "Signal"] = -1

        return df
