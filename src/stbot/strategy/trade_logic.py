# src/stbot/strategy/trade_logic.py
import pandas as pd

from stbot.strategy.trade_rules import entry_side, VOLUME_LOOKBACK


def get_titan_signal(processed_data: pd.DataFrame, current_candle: pd.Series, params: dict, market_bias=None):
    """
    Kompatibilitaets-Huelle (show_chart.py, Tests). Die eigentliche Einstiegslogik
    liegt in strategy/trade_rules.py und wird von Backtester, Portfolio-Simulator
    und Live gemeinsam genutzt -- hier wird nur noch dorthin delegiert.
    """
    if processed_data is None or processed_data.empty or 'sr_signal' not in processed_data.columns:
        return None, None
    candle = current_candle
    if 'vol_avg' not in candle.index and 'volume' in processed_data.columns:
        candle = candle.copy()
        candle['vol_avg'] = processed_data['volume'].loc[:current_candle.name].tail(VOLUME_LOOKBACK).mean()
    side = entry_side(candle, current_candle.name, params.get('strategy', {}), None)
    return (side, current_candle['close']) if side else (None, None)
