# /root/utbot2/src/utbot2/strategy/ichimoku_engine.py
import pandas as pd
import numpy as np

class IchimokuEngine:
    """
    Berechnet die Ichimoku Cloud Indikatoren.
    """
    def __init__(self, settings: dict):
        # Standard-Einstellungen für Krypto oft: 9, 26, 52, 26
        self.tenkan_period = settings.get('tenkan_period', 9)
        self.kijun_period = settings.get('kijun_period', 26)
        self.senkou_span_b_period = settings.get('senkou_span_b_period', 52)
        self.displacement = settings.get('displacement', 26)

    def _donchian(self, series_high, series_low, window):
        """Hilfsfunktion für (Highest High + Lowest Low) / 2"""
        return (series_high.rolling(window=window).max() + series_low.rolling(window=window).min()) / 2

    def process_dataframe(self, df: pd.DataFrame):
        """
        Fügt Ichimoku-Spalten zum DataFrame hinzu.
        """
        if df.empty:
            return df

        # Daten kopieren
        df = df.copy()

        # 1. Tenkan-sen (Conversion Line)
        df['tenkan_sen'] = self._donchian(df['high'], df['low'], self.tenkan_period)

        # 2. Kijun-sen (Base Line)
        df['kijun_sen'] = self._donchian(df['high'], df['low'], self.kijun_period)

        # 3. Senkou Span A (Leading Span A) - In die Zukunft verschoben
        df['senkou_span_a'] = ((df['tenkan_sen'] + df['kijun_sen']) / 2).shift(self.displacement)

        # 4. Senkou Span B (Leading Span B) - In die Zukunft verschoben
        df['senkou_span_b'] = self._donchian(df['high'], df['low'], self.senkou_span_b_period).shift(self.displacement)

        # 5. Chikou Span (Lagging Span) - In die Vergangenheit verschoben
        df['chikou_span'] = df['close'].shift(-self.displacement)

        return df
