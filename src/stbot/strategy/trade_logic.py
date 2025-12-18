# src/stbot/strategy/trade_logic.py
import pandas as pd

def get_titan_signal(processed_data: pd.DataFrame, current_candle: pd.Series, params: dict, market_bias=None):
    """
    Handelslogik für StBot (SRv2 Strategie).
    Reagiert auf Breakouts durch dynamische S/R Zonen.
    """
    
    # Sicherheitscheck
    if processed_data is None or processed_data.empty or 'sr_signal' not in processed_data.columns:
        return None, None

    # Wir nutzen das vorbrechnete Signal aus der SREngine
    # Da 'current_candle' eine Series ist (eine Zeile), holen wir den Wert direkt.
    signal_val = current_candle.get('sr_signal', 0)
    close_price = current_candle['close']

    signal_side = None

    # --- VOLUMEN-BESTÄTIGUNG ---
    # Breakout braucht überdurchschnittliches Volumen
    if signal_val != 0 and 'volume' in processed_data.columns:
        try:
            vol_avg = processed_data['volume'].tail(20).mean()
            current_vol = current_candle.get('volume', 0)
            
            # Breakout braucht mindestens 120% des durchschnittlichen Volumens
            if current_vol < vol_avg * 1.2:
                return None, None  # Schwacher Breakout ohne Volumen → Skip
        except Exception:
            pass  # Bei Fehler → kein Volumen-Filter

    # --- LONG SIGNAL ---
    # Resistance wurde nach oben durchbrochen
    if signal_val == 1:
        signal_side = "buy"

    # --- SHORT SIGNAL ---
    # Support wurde nach unten durchbrochen
    if signal_val == -1:
        signal_side = "sell"

    # --- MTF Bias Filter (Optional) ---
    # Die SRv2 Strategie ist stark genug, um alleine zu stehen, 
    # aber wir behalten den Filter bei, falls im Optimizer aktiviert.
    # Standardmäßig ist der Bias oft NEUTRAL, wenn im Optimizer nicht anders gefordert.
    if signal_side and market_bias and market_bias != "NEUTRAL":
        if market_bias == "BULLISH" and signal_side == "sell":
            return None, None
        if market_bias == "BEARISH" and signal_side == "buy":
            return None, None

    if signal_side:
        return signal_side, close_price

    return None, None
