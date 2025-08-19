# code/utilities/strategy_logic.py
import numpy as np
import pandas as pd
import ta

def calculate_signals(data, params):
    """
    Berechnet die reinen Supertrend-Signale.
    """
    # --- Parameter aus der Konfiguration holen ---
    st_period = params.get('st_atr_period', 10)
    st_multiplier = params.get('st_atr_multiplier', 3.0)
    
    # --- Supertrend-Berechnung ---
    src = (data['high'] + data['low']) / 2

    data['atr'] = ta.volatility.average_true_range(
        high=data['high'], 
        low=data['low'], 
        close=data['close'], 
        window=st_period, 
        fillna=False
    )
    
    up_band = src + (st_multiplier * data['atr'])
    low_band = src - (st_multiplier * data['atr'])

    final_up_band = pd.Series(np.nan, index=data.index)
    final_low_band = pd.Series(np.nan, index=data.index)
    trend = pd.Series(np.nan, index=data.index)
    supertrend = pd.Series(np.nan, index=data.index)

    # +++ KORREKTUR: Initialwerte für die erste Kerze setzen +++
    # Wir nehmen an, der Trend startet als Aufwärtstrend
    trend.iloc[0] = 1 
    final_low_band.iloc[0] = low_band.iloc[0]
    final_up_band.iloc[0] = up_band.iloc[0]

    # Iterative Berechnung ab der zweiten Kerze
    for i in range(1, len(data)):
        # Upper Band anpassen
        if data['close'].iloc[i-1] <= final_up_band.iloc[i-1]:
            final_up_band.iloc[i] = min(up_band.iloc[i], final_up_band.iloc[i-1])
        else:
            final_up_band.iloc[i] = up_band.iloc[i]

        # Lower Band anpassen
        if data['close'].iloc[i-1] >= final_low_band.iloc[i-1]:
            final_low_band.iloc[i] = max(low_band.iloc[i], final_low_band.iloc[i-1])
        else:
            final_low_band.iloc[i] = low_band.iloc[i]

        # Trendrichtung bestimmen
        if trend.iloc[i-1] == 1 and data['close'].iloc[i] < final_low_band.iloc[i]:
            trend.iloc[i] = -1
        elif trend.iloc[i-1] == -1 and data['close'].iloc[i] > final_up_band.iloc[i]:
            trend.iloc[i] = 1
        else:
            trend.iloc[i] = trend.iloc[i-1]
            
        # Supertrend-Linie setzen
        if trend.iloc[i] == 1:
            supertrend.iloc[i] = final_low_band.iloc[i]
        else:
            supertrend.iloc[i] = final_up_band.iloc[i]

    data['trend'] = trend
    data['supertrend_line'] = supertrend

    # --- Signale generieren (Trendwechsel) ---
    data['buy_signal'] = (data['trend'] == 1) & (data['trend'].shift(1) == -1)
    data['sell_signal'] = (data['trend'] == -1) & (data['trend'].shift(1) == 1)

    return data
