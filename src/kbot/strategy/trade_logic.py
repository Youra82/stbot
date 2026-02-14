# src/kbot/strategy/trade_logic.py
import pandas as pd

def get_titan_signal(processed_data: pd.DataFrame, current_candle: pd.Series, params: dict, market_bias=None):
    """
    Handelslogik für KBot (StochRSI).
    Erwartet, dass `processed_data` die Spalte `sr_signal` enthält (1 = buy, -1 = sell).
    """
    if processed_data is None or processed_data.empty or 'sr_signal' not in processed_data.columns:
        return None, None

    signal_val = current_candle.get('sr_signal', 0)
    close_price = current_candle['close']

    signal_side = None

    # Volumen-Bestätigung (optional)
    if signal_val != 0 and 'volume' in processed_data.columns:
        try:
            vol_avg = processed_data['volume'].tail(20).mean()
            current_vol = current_candle.get('volume', 0)
            if current_vol < vol_avg * 1.0:  # weniger strikt als SRv2
                return None, None
        except Exception:
            pass

    if signal_val == 1:
        signal_side = 'buy'
    elif signal_val == -1:
        signal_side = 'sell'

    # MTF Bias Filter (optional)
    if signal_side and market_bias and market_bias != 'NEUTRAL':
        if market_bias == 'BULLISH' and signal_side == 'sell':
            return None, None
        if market_bias == 'BEARISH' and signal_side == 'buy':
            return None, None

    if signal_side:
        return signal_side, close_price
    return None, None