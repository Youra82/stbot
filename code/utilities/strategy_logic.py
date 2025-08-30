# code/utilities/strategy_logic.py
import numpy as np
import pandas as pd
import ta

def calculate_signals(data, params):
    """
    Berechnet die Supertrend-Signale und fügt optional ADX und Donchian Channels hinzu.
    """
    # --- Supertrend-Berechnung (bestehend) ---
    st_params = params.get('supertrend_einstellungen', {})
    st_period = st_params.get('st_atr_period', 10)
    st_multiplier = st_params.get('st_atr_multiplier', 3.0)
    
    src = (data['high'] + data['low']) / 2
    data['atr'] = ta.volatility.average_true_range(
        high=data['high'], low=data['low'], close=data['close'], window=st_period, fillna=False
    )
    
    up_band = src + (st_multiplier * data['atr'])
    low_band = src - (st_multiplier * data['atr'])

    final_up_band = pd.Series(np.nan, index=data.index)
    final_low_band = pd.Series(np.nan, index=data.index)
    trend = pd.Series(np.nan, index=data.index)
    supertrend = pd.Series(np.nan, index=data.index)

    trend.iloc[0] = 1 
    final_low_band.iloc[0] = low_band.iloc[0]
    final_up_band.iloc[0] = up_band.iloc[0]

    for i in range(1, len(data)):
        if data['close'].iloc[i-1] <= final_up_band.iloc[i-1]:
            final_up_band.iloc[i] = min(up_band.iloc[i], final_up_band.iloc[i-1])
        else:
            final_up_band.iloc[i] = up_band.iloc[i]

        if data['close'].iloc[i-1] >= final_low_band.iloc[i-1]:
            final_low_band.iloc[i] = max(low_band.iloc[i], final_low_band.iloc[i-1])
        else:
            final_low_band.iloc[i] = low_band.iloc[i]

        if trend.iloc[i-1] == 1 and data['close'].iloc[i] < final_low_band.iloc[i]:
            trend.iloc[i] = -1
        elif trend.iloc[i-1] == -1 and data['close'].iloc[i] > final_up_band.iloc[i]:
            trend.iloc[i] = 1
        else:
            trend.iloc[i] = trend.iloc[i-1]
            
        if trend.iloc[i] == 1:
            supertrend.iloc[i] = final_low_band.iloc[i]
        else:
            supertrend.iloc[i] = final_up_band.iloc[i]

    data['trend'] = trend
    data['supertrend_line'] = supertrend
    data['buy_signal'] = (data['trend'] == 1) & (data['trend'].shift(1) == -1)
    data['sell_signal'] = (data['trend'] == -1) & (data['trend'].shift(1) == 1)

    # --- NEU: ADX-Berechnung für dynamischen Hebel ---
    hebel_params = params.get('hebel_einstellungen', {})
    if hebel_params.get('enable_dynamic_leverage', False):
        adx_period = hebel_params.get('adx_period', 14)
        adx_indicator = ta.trend.ADXIndicator(
            high=data['high'], low=data['low'], close=data['close'], window=adx_period, fillna=False
        )
        data['adx'] = adx_indicator.adx()

    # --- NEU: Donchian-Channel-Berechnung für dynamischen SL ---
    sl_params = params.get('stop_loss_einstellungen', {})
    if sl_params.get('enable_donchian_channel_sl', False):
        donchian_period = sl_params.get('donchian_period', 20)
        donchian = ta.volatility.DonchianChannel(
            high=data['high'], low=data['low'], close=data['close'], window=donchian_period, fillna=False
        )
        data['donchian_upper'] = donchian.donchian_channel_hband()
        data['donchian_lower'] = donchian.donchian_channel_lband()
        
    return data
