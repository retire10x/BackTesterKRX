import pandas as pd


class MetricsCalculator:
    @staticmethod
    def calculate(df):
        if df is None or len(df) == 0:
            return {}

        total_days = len(df)
        initial_asset = df["Total_Asset"].iloc[0]
        final_asset = df["Total_Asset"].iloc[-1]

        total_return = (final_asset / initial_asset - 1) * 100

        cagr = (
            (final_asset / initial_asset) ** (1 / (total_days / 252)) - 1
        ) * 100

        peak = df["Total_Asset"].cummax()
        drawdown = (peak - df["Total_Asset"]) / peak
        mdd = drawdown.max() * 100

        return {
            "최종자산": int(final_asset),
            "누적수익률(%)": total_return,
            "CAGR(%)": cagr,
            "MDD(%)": mdd,
        }
