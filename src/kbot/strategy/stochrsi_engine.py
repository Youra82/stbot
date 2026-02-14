# src/kbot/strategy/stochrsi_engine.py
import pandas as pd
import numpy as np

class StochRSIEngine:
    """
    Implements Stochastic RSI signal generation.
    Produces a DataFrame with a column `sr_signal`: 1 = buy, -1 = sell, 0 = neutral.
    """
    def __init__(self, settings: dict | None = None):
        settings = settings or {}
        self.rsi_period = int(settings.get('rsi_period', 14))
        self.stoch_rsi_period = int(settings.get('stoch_rsi_period', 14))
        self.stoch_k = int(settings.get('stoch_k', 3))
        self.stoch_d = int(settings.get('stoch_d', 3))
        self.low = float(settings.get('stoch_rsi_low', 20))
        self.high = float(settings.get('stoch_rsi_high', 80))

    def _rsi(self, close: pd.Series, period: int) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        # Wilder's smoothing (EMA with alpha=1/period)
        avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.fillna(50)

    def process_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        df = df.copy()

        # 1) RSI
        df['rsi'] = self._rsi(df['close'], self.rsi_period)

        # 2) Stochastic RSI
        rsi = df['rsi']
        min_rsi = rsi.rolling(self.stoch_rsi_period, min_periods=1).min()
        max_rsi = rsi.rolling(self.stoch_rsi_period, min_periods=1).max()
        stoch_rsi = 100 * (rsi - min_rsi) / (max_rsi - min_rsi + 1e-9)
        df['stochrsi'] = stoch_rsi

        # Smooth %K and %D
        k = stoch_rsi.rolling(self.stoch_k, min_periods=1).mean()
        d = k.rolling(self.stoch_d, min_periods=1).mean()
        df['stochrsi_k'] = k
        df['stochrsi_d'] = d

        # 3) Signals: bullish crossover below `low` -> BUY, bearish crossover above `high` -> SELL
        k_prev = k.shift(1)
        d_prev = d.shift(1)

        buy = (k_prev < d_prev) & (k > d) & (k < self.low)
        sell = (k_prev > d_prev) & (k < d) & (k > self.high)

        signals = np.zeros(len(df), dtype=int)
        signals[buy.fillna(False).values] = 1
        signals[sell.fillna(False).values] = -1

        df['sr_signal'] = signals
        return df