# stbot/utils/indicator_handler.py (KORRIGIERT: Mapping der pandas-ta Spaltennamen)
import pandas as pd
import pandas_ta as ta

def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Berechnet die Indikatoren, die vom stbot Hauptskript und der KI-Strategie erwartet werden.
    Wir mappen die langen pandas-ta Namen auf die kurzen Namen (z.B. stochk, bbp),
    die das Log-Statement in main.py erwartet.
    """
    if df.empty:
        return df

    # Stelle sicher, dass df die korrekten Spaltennamen hat (open, high, low, close)
    df.columns = df.columns.str.lower()
    
    # 1. Stochastic Oscillator (StochK und StochD)
    stoch = ta.stoch(df['high'], df['low'], df['close'], k=14, d=3, smooth_d=3)
    # Annahme: stochk ist der erste Teil des Stoch-Outputs
    if not stoch.empty:
        df['stochk'] = stoch.iloc[:, 0]  # Erster Stoch-Output (typischerweise %K)
        df['stochd'] = stoch.iloc[:, 1]  # Zweiter Stoch-Output (typischerweise %D)

    # 2. MACD Histogram (MACD_H)
    macd_data = ta.macd(df['close'], fast=12, slow=26, signal=9)
    if not macd_data.empty:
        # MACDh_12_26_9 ist das Histogram
        df['macd_hist'] = macd_data.iloc[:, 2] # Dritter Output ist das Histogram

    # 3. Bollinger Band Percent B (BBP)
    bbands = ta.bbands(df['close'], length=20, std=2)
    if not bbands.empty:
        # BBP_20_2.0_2.0 (Percent Bandwidth) ist typischerweise der letzte Output
        df['bbp'] = bbands.iloc[:, -1]

    # 4. On-Balance Volume (OBV)
    df['obv'] = ta.obv(df['close'], df['volume'])
    
    # Optional: Aufräumen von NaN-Werten, die durch die Berechnung entstehen
    df.dropna(subset=['stochk', 'macd_hist', 'bbp', 'obv'], inplace=True)

    return df
