# code/utilities/strategy_logic.py
import numpy as np
import ta

def calculate_signals(data, params):
    """
    Berechnet die UT-Bot-Signale und den ADX-Trendindikator.
    """
    src = data['close']
    
    # --- UT Bot Signals ---
    data['atr'] = ta.volatility.average_true_range(data['high'], data['low'], data['close'], window=params['ut_atr_period'])
    n_loss = params['ut_key_value'] * data['atr']
    
    x_atr_trailing_stop = np.zeros(len(data))
    for i in range(len(data)):
        if i == 0:
            x_atr_trailing_stop[i] = src.iloc[i] - n_loss.iloc[i]
        else:
            if src.iloc[i] > x_atr_trailing_stop[i-1] and src.iloc[i-1] > x_atr_trailing_stop[i-1]:
                x_atr_trailing_stop[i] = max(x_atr_trailing_stop[i-1], src.iloc[i] - n_loss.iloc[i])
            elif src.iloc[i] < x_atr_trailing_stop[i-1] and src.iloc[i-1] < x_atr_trailing_stop[i-1]:
                x_atr_trailing_stop[i] = min(x_atr_trailing_stop[i-1], src.iloc[i] + n_loss.iloc[i])
            else:
                if src.iloc[i] > x_atr_trailing_stop[i-1]:
                    x_atr_trailing_stop[i] = src.iloc[i] - n_loss.iloc[i]
                else:
                    x_atr_trailing_stop[i] = src.iloc[i] + n_loss.iloc[i]

    data['x_atr_trailing_stop'] = x_atr_trailing_stop
    data['buy_signal_ut'] = (src > data['x_atr_trailing_stop']) & (src.shift(1) <= data['x_atr_trailing_stop'].shift(1))
    data['sell_signal_ut'] = (src < data['x_atr_trailing_stop']) & (src.shift(1) >= data['x_atr_trailing_stop'].shift(1))

    # --- ADX Trend Filter ---
    if params.get('use_adx_filter', False):
        data['adx'] = ta.trend.adx(data['high'], data['low'], data['close'], window=params.get('adx_window', 14))
        is_trending = data['adx'] > params.get('adx_threshold', 25)
        data['buy_signal'] = data['buy_signal_ut'] & is_trending
        data['sell_signal'] = data['sell_signal_ut'] & is_trending
    else:
        # Wenn Filter aus, nutze die originalen Signale
        data['buy_signal'] = data['buy_signal_ut']
        data['sell_signal'] = data['sell_signal_ut']
        data['adx'] = 0 # Platzhalter

    return data
