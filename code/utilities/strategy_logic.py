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
    # Quelle ist (High + Low) / 2
    src = (data['high'] + data['low']) / 2

    # ATR berechnen
    data['atr'] = ta.volatility.average_true_range(
        high=data['high'], 
        low=data['low'], 
        close=data['close'], 
        window=st_period, 
        fillna=False
    )
    
    # Initialen Upper und Lower Bands berechnen
    up_band = src + (st_multiplier * data['atr'])
    low_band = src - (st_multiplier * data['atr'])

    # Finale Bänder und Trendrichtung initialisieren
    final_up_band = pd.Series(np.nan, index=data.index)
    final_low_band = pd.Series(np.nan, index=data.index)
    supertrend = pd.Series(np.nan, index=data.index)
    trend = pd.Series(np.nan, index=data.index)

    # Iterative Berechnung (Supertrend ist zustandsabhängig)
    for i in range(1, len(data)):
        # Wenn der vorherige Close unter dem vorherigen Upper Band lag...
        if data['close'].iloc[i-1] <= final_up_band.iloc[i-1]:
            # ...wird das neue Upper Band das Minimum aus dem berechneten und dem vorherigen Band.
            final_up_band.iloc[i] = min(up_band.iloc[i], final_up_band.iloc[i-1])
        else:
            # ansonsten wird der berechnete Wert genommen.
            final_up_band.iloc[i] = up_band.iloc[i]

        # Wenn der vorherige Close über dem vorherigen Lower Band lag...
        if data['close'].iloc[i-1] >= final_low_band.iloc[i-1]:
             # ...wird das neue Lower Band das Maximum aus dem berechneten und dem vorherigen Band.
            final_low_band.iloc[i] = max(low_band.iloc[i], final_low_band.iloc[i-1])
        else:
            # ansonsten wird der berechnete Wert genommen.
            final_low_band.iloc[i] = low_band.iloc[i]

        # Trendrichtung bestimmen
        if trend.iloc[i-1] == 1 and data['close'].iloc[i] < final_low_band.iloc[i]:
            trend.iloc[i] = -1
        elif trend.iloc[i-1] == -1 and data['close'].iloc[i] > final_up_band.iloc[i]:
            trend.iloc[i] = 1
        else:
            trend.iloc[i] = trend.iloc[i-1] # Trend beibehalten
            
        # Supertrend-Linie setzen
        if trend.iloc[i] == 1:
            supertrend.iloc[i] = final_low_band.iloc[i]
        else:
            supertrend.iloc[i] = final_up_band.iloc[i]

    data['trend'] = trend
    data['supertrend_line'] = supertrend # Für Trailing Stop

    # --- Signale generieren (Trendwechsel) ---
    # Kaufsignal, wenn der Trend von -1 (short) auf 1 (long) wechselt
    data['buy_signal'] = (data['trend'] == 1) & (data['trend'].shift(1) == -1)
    # Verkaufssignal, wenn der Trend von 1 (long) auf -1 (short) wechselt
    data['sell_signal'] = (data['trend'] == -1) & (data['trend'].shift(1) == 1)

    return data
