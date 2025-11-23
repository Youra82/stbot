# /root/utbot2/src/utbot2/strategy/trade_logic.py
import pandas as pd

def get_titan_signal(processed_data: pd.DataFrame, current_candle: pd.Series, params: dict, market_bias=None):
    """
    Ichimoku Trading Logik für UtBot2.
    
    Strategie:
    1. TK Cross (Tenkan kreuzt Kijun)
    2. Kumo Filter: Preis muss auf der richtigen Seite der Wolke sein.
    3. Chikou Filter: Preis heute muss besser sein als vor 26 Perioden.
    """
    
    # Sicherheitscheck
    if processed_data is None or processed_data.empty or len(processed_data) < 30:
        return None, None

    # Parameter laden
    strategy_params = params.get('strategy', {})
    use_chikou_filter = strategy_params.get('use_chikou_filter', True)
    displacement = strategy_params.get('displacement', 26)
    
    # Wir brauchen die letzten beiden abgeschlossenen Zeilen für Crossovers
    last_row = processed_data.iloc[-1]
    prev_row = processed_data.iloc[-2]
    
    # Aktuelle Werte
    close = last_row['close']
    tenkan = last_row['tenkan_sen']
    kijun = last_row['kijun_sen']
    
    # Die Wolke (Senkou A/B) ist bereits geshiftet im DataFrame.
    ssa = last_row['senkou_span_a']
    ssb = last_row['senkou_span_b']
    
    # Vorherige Werte (für Crossover Erkennung)
    prev_tenkan = prev_row['tenkan_sen']
    prev_kijun = prev_row['kijun_sen']
    
    signal_side = None
    signal_price = close
    
    # --- Prüfung auf Gültigkeit der Indikatoren ---
    if pd.isna(ssa) or pd.isna(ssb) or pd.isna(tenkan) or pd.isna(kijun):
        return None, None

    # --- LONG SIGNAL ---
    # 1. TK Cross Bullish: Tenkan kreuzt Kijun von unten nach oben
    tk_cross_bull = (prev_tenkan <= prev_kijun) and (tenkan > kijun)
    
    # 2. Kumo Filter: Preis muss ÜBER der Wolke sein
    cloud_top = max(ssa, ssb)
    price_above_cloud = close > cloud_top
    
    if tk_cross_bull and price_above_cloud:
        signal_side = "buy"
        
    # --- SHORT SIGNAL ---
    # 1. TK Cross Bearish: Tenkan kreuzt Kijun von oben nach unten
    tk_cross_bear = (prev_tenkan >= prev_kijun) and (tenkan < kijun)
    
    # 2. Kumo Filter: Preis muss UNTER der Wolke sein
    cloud_bottom = min(ssa, ssb)
    price_below_cloud = close < cloud_bottom
    
    if tk_cross_bear and price_below_cloud:
        signal_side = "sell"

    # --- Chikou Span Filter (Optional) ---
    if signal_side and use_chikou_filter:
        if len(processed_data) > displacement + 1:
            past_idx = -(displacement + 1)
            past_price = processed_data.iloc[past_idx]['close']
            
            if signal_side == "buy" and close <= past_price:
                return None, None # Blockiert
            if signal_side == "sell" and close >= past_price:
                return None, None # Blockiert
                
    # --- MTF Bias Filter ---
    if market_bias and market_bias != "NEUTRAL":
        if market_bias == "BULLISH" and signal_side == "sell":
            return None, None
        if market_bias == "BEARISH" and signal_side == "buy":
            return None, None

    if signal_side:
        return signal_side, signal_price

    return None, None
