# src/kbot/strategy/stochrsi_engine.py
"""
StochRSIEngine
- Einfacher, gut dokumentierter Stoch‑RSI Signal-Generator für KBot
- API-kompatible Hilfsfunktionen, sodass `trade_manager` weiterverwendet werden kann
"""
from dataclasses import dataclass
import pandas as pd
import numpy as np


@dataclass
class ChannelState:
    bot: float
    top: float
    trend: int


class StochRSIEngine:
    def __init__(self, settings: dict = None):
        settings = settings or {}
        # Strategie-Parameter (sinnvolle Defaults)
        self.rsi_period = int(settings.get('rsi_period', 14))
        self.stochrsi_len = int(settings.get('stochrsi_len', 14))
        self.k = int(settings.get('k', 3))
        self.d = int(settings.get('d', 3))
        # Overbought / Oversold im Bereich 0..1
        self.ob = float(settings.get('ob', 0.8))
        self.os = float(settings.get('os', 0.2))
        # Stop-Loss / ATR
        self.atr_period = int(settings.get('atr_period', 14))
        self.sl_atr_mult = float(settings.get('sl_atr_mult', settings.get('sl_atr_multiplier', 1.5)))
        # Default RR (kann vom Trade-Manager überschrieben werden)
        self.default_rr = float(settings.get('risk_reward_ratio', 2.0))

    def _rsi(self, close: pd.Series) -> pd.Series:
        delta = close.diff()
        up = delta.clip(lower=0)
        down = -delta.clip(upper=0)
        # Wilder Smoothing (EMA-like) für RSI
        roll_up = up.ewm(alpha=1.0 / self.rsi_period, adjust=False).mean()
        roll_down = down.ewm(alpha=1.0 / self.rsi_period, adjust=False).mean()
        rs = roll_up / roll_down.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return rsi.fillna(50.0)

    def _atr(self, df: pd.DataFrame) -> pd.Series:
        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(self.atr_period, min_periods=1).mean()
        return atr.fillna(method='bfill').fillna(0.0)

    def process_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Fügt `stochrsi_k`, `stochrsi_d`, `stochrsi_signal` und `atr` hinzu."""
        if df.empty:
            return df
        df = df.copy()

        # RSI
        rsi = self._rsi(df['close'])

        # StochRSI = (RSI - min(RSI, len)) / (max(RSI, len) - min(RSI, len)) in [0,1]
        rsi_min = rsi.rolling(self.stochrsi_len, min_periods=1).min()
        rsi_max = rsi.rolling(self.stochrsi_len, min_periods=1).max()
        denom = (rsi_max - rsi_min).replace(0, np.nan)
        stochrsi = (rsi - rsi_min) / denom
        stochrsi = stochrsi.fillna(0.5).clip(0, 1)

        # Smooth K/D
        stochrsi_k = stochrsi.rolling(self.k, min_periods=1).mean()
        stochrsi_d = stochrsi_k.rolling(self.d, min_periods=1).mean()

        df['stochrsi_k'] = stochrsi_k
        df['stochrsi_d'] = stochrsi_d
        df['atr'] = self._atr(df)

        # Kompatible Channel-Felder (synthetisch, für bestehenden Code‑Pfad)
        df['channel_top'] = df['close'] + df['atr']
        df['channel_bot'] = df['close'] - df['atr']
        df['channel_avg'] = df['close']
        df['channel_trend'] = np.where(df['stochrsi_k'] > df['stochrsi_d'], 1, -1)

        # Signal: 1 = long (K steigt über OS), -1 = short (K fällt unter OB)
        signals = np.zeros(len(df), dtype=int)
        for i in range(1, len(df)):
            prev_k = stochrsi_k.iloc[i - 1]
            curr_k = stochrsi_k.iloc[i]
            prev_d = stochrsi_d.iloc[i - 1]
            curr_d = stochrsi_d.iloc[i]

            # Long: K kreuzt über Oversold und bestätigt (optional) durch K>D
            if prev_k < self.os and curr_k >= self.os and curr_k > curr_d:
                signals[i] = 1
            # Short: K kreuzt unter Overbought und bestätigt durch K<D
            elif prev_k > self.ob and curr_k <= self.ob and curr_k < curr_d:
                signals[i] = -1

        df['stochrsi_signal'] = signals
        return df

    def get_signal(self, df: pd.DataFrame, use_volume_confirmation: bool = False):
        """Gibt aktuelles Signal und Grund zurück (compatible mit Trade-Manager).
        Rückgabe: ('long'|'short'|None, reason)
        """
        if df is None or len(df) < 2:
            return None, 'keine Daten'
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        sig = int(curr.get('stochrsi_signal', 0))
        if sig == 1:
            return 'long', f"StochRSI K crossed over OS ({self.os})"
        if sig == -1:
            return 'short', f"StochRSI K crossed under OB ({self.ob})"
        return None, 'Kein Signal'

    def get_stop_loss_take_profit(self, df: pd.DataFrame, side: str, risk_reward: float = None):
        """Berechnet SL/TP basierend auf ATR und konfiguriertem Multiplier."""
        if risk_reward is None:
            risk_reward = self.default_rr
        last_close = float(df['close'].iloc[-1])
        atr = float(df['atr'].iloc[-1]) if 'atr' in df.columns else max(0.01, last_close * 0.01)
        sl_dist = max(atr * self.sl_atr_mult, last_close * 0.002)  # minimaler SL Abstand

        if side == 'long':
            sl = last_close - sl_dist
            tp = last_close + sl_dist * risk_reward
        else:
            sl = last_close + sl_dist
            tp = last_close - sl_dist * risk_reward
        return float(max(0.0, sl)), float(max(0.0, tp))

    def get_channel_state(self, df: pd.DataFrame) -> ChannelState:
        """Hilfsfunktion für Statusmeldungen (Kompatibilität mit bestehenden Views)."""
        last = df.iloc[-1]
        last_close = float(last['close'])
        atr = float(last.get('atr', 0.0))
        top = last_close + atr
        bot = last_close - atr
        trend = 1 if last.get('stochrsi_k', 0) > last.get('stochrsi_d', 0) else -1
        return ChannelState(bot=bot, top=top, trend=trend)
