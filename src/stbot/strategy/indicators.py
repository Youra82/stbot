# src/stbot/strategy/indicators.py
import pandas as pd
import numpy as np

def calculate_ema(data, period):
    """Berechnet den Exponential Moving Average (EMA)."""
    return data.ewm(span=period, adjust=False).mean()

def calculate_rsi(data, period=14):
    """Berechnet den Relative Strength Index (RSI)."""
    # Sicherstellen, dass die Daten nicht leer sind und mindestens 2 Perioden haben
    if data.empty or len(data) < 2:
        return pd.Series(np.nan, index=data.index)

    delta = data.diff()
    # Berechne Gewinne (positive Differenzen) und Verluste (negative Differenzen)
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    
    # Sicher gegen Division durch Null
    rs = gain / loss.replace(0, np.nan)
    
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_macd(data, short_period=12, long_period=26, signal_period=9):
    """Berechnet MACD, Signal Line und Histogram."""
    if data.empty or len(data) < long_period:
        nan_series = pd.Series(np.nan, index=data.index)
        return nan_series, nan_series, nan_series
        
    short_ema = calculate_ema(data, short_period)
    long_ema = calculate_ema(data, long_period)
    macd = short_ema - long_ema
    signal = calculate_ema(macd, signal_period)
    histogram = macd - signal
    return macd, signal, histogram

# --- STBot Logik (Simulation der SMC Engine) ---
class STBotEngine:
    """
    Diese Klasse simuliert die SMC-Engine, indem sie einfach alle benötigten 
    Indikatoren berechnet. Sie ist zustandslos für die Indikatoren selbst.
    """
    def __init__(self, settings: dict):
        self.settings = settings
    
    def process_dataframe(self, df: pd.DataFrame):
        """Berechnet alle Indikatoren und gibt das DataFrame zurück."""
        
        # Hole Perioden aus den Settings (falls vorhanden, sonst Defaults)
        ema_short = self.settings.get('ema_short', 9)
        ema_long = self.settings.get('ema_long', 21)
        rsi_period = self.settings.get('rsi_period', 14)
        volume_ma_period = self.settings.get('volume_ma_period', 20)
        
        # Sicherstellen, dass die Spaltennamen für die Berechnung korrekt sind ('Close')
        df = df.rename(columns={'close': 'Close', 'volume': 'Volume'}) 
        
        # Berechne EMAs
        df['EMA_short'] = calculate_ema(df['Close'], ema_short)
        df['EMA_long'] = calculate_ema(df['Close'], ema_long)
        
        # Berechne MACD (Standardperioden 12, 26, 9)
        df['MACD'], df['MACD_Signal'], df['MACD_Hist'] = calculate_macd(df['Close'])
        
        # Berechne RSI
        df['RSI'] = calculate_rsi(df['Close'], rsi_period)
        
        # Berechne Volume MA
        df['Volume_MA'] = df['Volume'].rolling(window=volume_ma_period).mean()
        
        df.dropna(inplace=True) # Entferne NaN-Werte, die durch die Indikator-Berechnung entstehen
        
        return df

# Dummy-Klassen zur Kompatibilität, falls noch irgendwo im Code auf sie zugegriffen wird
# Wir entfernen sie aber aus trade_logic.py
class Bias: pass
class FVG: pass
class OrderBlock: pass
