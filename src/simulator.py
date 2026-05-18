import math

import pandas as pd


class PortfolioSimulator:
    def __init__(self, config):
        self.initial_cash = config["portfolio"]["initial_cash"]
        self.buy_cost = config["trading_costs"]["buy_cost"]
        self.sell_cost = config["trading_costs"]["sell_cost"]

    def run_backtest(self, df, start_date):
        """당일 종가 신호 -> 익일 시가 체결 모델 시뮬레이션"""
        df = df.loc[start_date:].copy()
        if df.empty:
            return None

        cash = self.initial_cash
        shares = 0
        position = 0
        pending_signal = 0

        asset_history = []
        trades = []

        for i in range(len(df)):
            current_date = df.index[i]
            open_price = df["Open"].iloc[i]
            close_price = df["Close"].iloc[i]
            today_signal = df["Signal"].iloc[i]

            if pending_signal == 1 and position == 0:
                if (
                    pd.notna(open_price)
                    and open_price > 0
                    and cash > 0
                ):
                    shares = math.floor(
                        cash / (open_price * (1 + self.buy_cost))
                    )
                    if shares > 0:
                        exec_money = shares * open_price
                        fee = exec_money * self.buy_cost
                        cash -= exec_money + fee
                        position = 1
                        trades.append(
                            {
                                "Date": current_date,
                                "Type": "BUY",
                                "Price": open_price,
                                "Shares": shares,
                            }
                        )

            elif pending_signal == -1 and position == 1:
                if pd.notna(open_price) and open_price > 0 and shares > 0:
                    exec_money = shares * open_price
                    fee_and_tax = exec_money * self.sell_cost
                    cash += exec_money - fee_and_tax
                    trades.append(
                        {
                            "Date": current_date,
                            "Type": "SELL",
                            "Price": open_price,
                            "Shares": shares,
                        }
                    )
                    shares = 0
                    position = 0

            total_asset = cash + (shares * close_price)
            asset_history.append(total_asset)

            pending_signal = today_signal

        df["Total_Asset"] = asset_history
        return df, trades
