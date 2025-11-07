# src/stbot/strategy/trade_logic.py
import pandas as pd
# Importiere die SMC-spezifischen Klassen NICHT mehr
# from titanbot.strategy.smc_engine import Bias, FVG, OrderBlock 

def get_titan_signal(data_with_indicators: pd.DataFrame, current_candle: pd.Series, params: dict):
    """
    Generiert ein Kauf-/Verkauf-Signal basierend auf den Indikatoren.

    :param data_with_indicators: DataFrame mit allen berechneten Indikatoren (vom Engine)
    :param current_candle: Die letzte Kerze (Series) mit Indikator-Werten
    :param params: Konfigurations-Dictionary (strategy, risk, behavior)

    Rückgabewerte:
    - (side, entry_price): z.B. ("buy", 123.45) oder ("sell", 123.45)
    - (None, None): Wenn kein Signal vorhanden ist.
    """
    
    # Hole Konfiguration
    behavior_params = params.get('behavior', {})
    
    # NEU: Der ADX-Filter wird hier nicht mehr benötigt, da er jetzt direkt 
    # in der Signallogik durch EMA/RSI/MACD/Volume ersetzt wird.
    
    # Wir arbeiten mit der letzten, vollständigen Kerze
    if current_candle.empty:
        return None, None
        
    current_close = current_candle['Close']
    
    # ----------------------------------------------------
    # Deine Indikator-Signallogik (Übernommen aus dem Prompt)
    # ----------------------------------------------------
    
    # Buy-Signal-Check
    long_active = behavior_params.get('use_longs', True)
    if long_active:
        buy_condition_met = (
            (current_candle['MACD'] > current_candle['MACD_Signal']) and # MACD-Crossover bullish
            (current_candle['RSI'] < 30) and # RSI im überverkauften Bereich
            (current_candle['EMA_short'] > current_candle['EMA_long']) and # Kurze EMA über langer
            (current_candle['Volume'] > current_candle['Volume_MA']) # Validierung durch Volume
        )
        if buy_condition_met:
            return "buy", current_close # Entry-Preis ist der Schlusskurs der Signal-Kerze
    
    # Sell-Signal-Check
    short_active = behavior_params.get('use_shorts', True)
    if short_active:
        sell_condition_met = (
            (current_candle['MACD'] < current_candle['MACD_Signal']) and # MACD-Crossover bearish
            (current_candle['RSI'] > 70) and # RSI im überkauften Bereich
            (current_candle['EMA_short'] < current_candle['EMA_long']) and # Kurze EMA unter langer
            (current_candle['Volume'] > current_candle['Volume_MA']) # Validierung durch Volume
        )
        if sell_condition_met:
            return "sell", current_close # Entry-Preis ist der Schlusskurs der Signal-Kerze
            
    # Kein Signal gefunden
    return None, None
