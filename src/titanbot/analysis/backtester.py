# src/titanbot/analysis/backtester.py (Mit DYNAMISCHER Margin/Risiko vom CURRENT Capital)
import os
import pandas as pd
import numpy as np
import json
import sys
from tqdm import tqdm
import ta
import math
import warnings # Füge Warnungen hinzu, um pandas-Kopierwarnungen zu ignorieren

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

# Importiere Indikator-Funktionen und Signallogik
from titanbot.strategy.trade_logic import get_st_signal, calculate_ema, calculate_rsi, calculate_macd

# Ignore SettingWithCopyWarning for internal calculations
warnings.filterwarnings('ignore', category=pd.core.common.SettingWithCopyWarning)

secrets_cache = None

# --- load_data Funktion (Bleibt unverändert, da sehr lang) ---
# ... (Implementierung von load_data) ...

# Hier ist die lange Funktion `load_data` nicht enthalten, um die Datei kompakt zu halten,
# aber ihre Implementierung bleibt aus dem vorherigen Schritt des TitanBot erhalten.

def run_st_backtest(data, strategy_params, risk_params, start_capital=1000, verbose=False):
    """ Führt einen Backtest der Indikator-Strategie (STBot) durch. Ersetzt SMC-Backtest. """
    if data.empty or len(data) < 50: # Mindestkerzen für Indikatoren
        return {"total_pnl_pct": -100, "trades_count": 0, "win_rate": 0, "max_drawdown_pct": 1.0, "end_capital": start_capital}

    # --- 1. Indikatoren berechnen (für SL/TP und Signale) ---
    try:
        df = data.copy()
        
        # ATR für SL-Berechnung (wird beibehalten, da es im Risikomanagement verwendet wird)
        atr_indicator = ta.volatility.AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=14)
        df['atr'] = atr_indicator.average_true_range()

        # MACD, RSI, EMA, Volume MA für die Signal-Logik
        df['Close'] = df['close'] # Für die Indikatorfunktionen
        df['Volume'] = df['volume']
        
        # Hole Indikator-Perioden aus Strategie-Params
        ema_short = strategy_params.get('ema_short', 9)
        ema_long = strategy_params.get('ema_long', 21)
        rsi_period = strategy_params.get('rsi_period', 14)
        volume_ma_period = strategy_params.get('volume_ma_period', 20)

        df['EMA_short'] = calculate_ema(df['Close'], ema_short)
        df['EMA_long'] = calculate_ema(df['Close'], ema_long)
        df['MACD'], df['MACD_Signal'], df['MACD_Hist'] = calculate_macd(df['Close'])
        df['RSI'] = calculate_rsi(df['Close'], rsi_period)
        df['Volume_MA'] = df['Volume'].rolling(window=volume_ma_period).mean()

        df.dropna(subset=['atr', 'EMA_short', 'MACD', 'RSI'], inplace=True) 

        if df.empty:
             return {"total_pnl_pct": -100, "trades_count": 0, "win_rate": 0, "max_drawdown_pct": 1.0, "end_capital": start_capital}
    
    except Exception as e:
        if verbose: print(f"FEHLER bei Indikator-Berechnung: {e}")
        return {"total_pnl_pct": -999, "trades_count": 0, "win_rate": 0, "max_drawdown_pct": 1.0, "end_capital": start_capital}

    # --- 2. Risikoparameter (Bleiben gleich wie im SMC-Bot) ---
    risk_reward_ratio = risk_params.get('risk_reward_ratio', 1.5)
    risk_per_trade_pct = risk_params.get('risk_per_trade_pct', 1.0) / 100
    activation_rr = risk_params.get('trailing_stop_activation_rr', 2.0)
    callback_rate = risk_params.get('trailing_stop_callback_rate_pct', 1.0) / 100
    leverage = risk_params.get('leverage', 10)
    fee_pct = 0.05 / 100
    atr_multiplier_sl = risk_params.get('atr_multiplier_sl', 2.0)
    min_sl_pct = risk_params.get('min_sl_pct', 0.5) / 100.0
    max_allowed_effective_leverage = 10
    absolute_max_notional_value = 1000000

    current_capital = start_capital
    peak_capital = start_capital
    max_drawdown_pct = 0.0
    trades_count = 0
    wins_count = 0
    position = None

    # --- 3. Backtest Loop ---
    
    # Kombiniere Parameter, die für get_st_signal benötigt werden
    params_for_logic = {"strategy": strategy_params, "risk": risk_params, "behavior": {"use_longs": True, "use_shorts": True}}
    
    iterator = df.iterrows() 
    
    for timestamp, current_candle in iterator:
        if current_capital <= 0: break

        # --- Positions-Management (Unverändert) ---
        if position:
            exit_price = None
            if position['side'] == 'long':
                if not position['trailing_active'] and current_candle['high'] >= position['activation_price']: position['trailing_active'] = True
                if position['trailing_active']:
                    position['peak_price'] = max(position['peak_price'], current_candle['high'])
                    trailing_sl = position['peak_price'] * (1 - callback_rate)
                    position['stop_loss'] = max(position['stop_loss'], trailing_sl)
                if current_candle['low'] <= position['stop_loss']: exit_price = position['stop_loss']
                elif not position['trailing_active'] and current_candle['high'] >= position['take_profit']: exit_price = position['take_profit']
            elif position['side'] == 'short':
                if not position['trailing_active'] and current_candle['low'] <= position['activation_price']: position['trailing_active'] = True
                if position['trailing_active']:
                    position['peak_price'] = min(position['peak_price'], current_candle['low'])
                    trailing_sl = position['peak_price'] * (1 + callback_rate)
                    position['stop_loss'] = min(position['stop_loss'], trailing_sl)
                if current_candle['high'] >= position['stop_loss']: exit_price = position['stop_loss']
                elif not position['trailing_active'] and current_candle['low'] <= position['take_profit']: exit_price = position['take_profit']

            if exit_price:
                pnl_pct = (exit_price / position['entry_price'] - 1) if position['side'] == 'long' else (1 - exit_price / position['entry_price'])
                notional_value = position['notional_value']
                pnl_usd = notional_value * pnl_pct
                total_fees = notional_value * fee_pct * 2
                current_capital += (pnl_usd - total_fees)
                if current_capital <= 0: current_capital = 0; break
                if (pnl_usd - total_fees) > 0: wins_count += 1
                trades_count += 1
                position = None
                peak_capital = max(peak_capital, current_capital)
                if peak_capital > 0:
                    drawdown = (peak_capital - current_capital) / peak_capital
                    max_drawdown_pct = max(max_drawdown_pct, drawdown)

        # --- Einstiegs-Logik ---
        if not position and current_capital > 0:
            # Sende den DataFrame bis zur aktuellen Kerze und die aktuelle Kerze
            side, _ = get_st_signal(df.loc[:timestamp], current_candle, params=params_for_logic) 
            
            if side:
                entry_price = current_candle['close']
                current_atr = current_candle.get('atr')
                if pd.isna(current_atr) or current_atr <= 0: continue

                sl_distance_atr = current_atr * atr_multiplier_sl
                sl_distance_min = entry_price * min_sl_pct
                sl_distance = max(sl_distance_atr, sl_distance_min)
                if sl_distance <= 0: continue

                risk_amount_usd = current_capital * risk_per_trade_pct
                sl_distance_pct_equivalent = sl_distance / entry_price
                if sl_distance_pct_equivalent <= 1e-6: continue

                calculated_notional_value = risk_amount_usd / sl_distance_pct_equivalent
                max_notional_by_leverage = current_capital * max_allowed_effective_leverage
                final_notional_value = min(calculated_notional_value, max_notional_by_leverage, absolute_max_notional_value)

                margin_used = math.ceil((final_notional_value / leverage) * 100) / 100

                if margin_used > current_capital or final_notional_value < 1.0: continue

                stop_loss = entry_price - sl_distance if side == 'buy' else entry_price + sl_distance
                take_profit = entry_price + sl_distance * risk_reward_ratio if side == 'buy' else entry_price - sl_distance * risk_reward_ratio
                activation_price = entry_price + sl_distance * activation_rr if side == 'buy' else entry_price - sl_distance * activation_rr

                position = {
                    'side': 'long' if side == 'buy' else 'short',
                    'entry_price': entry_price, 'stop_loss': stop_loss,
                    'take_profit': take_profit, 'margin_used': margin_used,
                    'notional_value': final_notional_value,
                    'trailing_active': False, 'activation_price': activation_price,
                    'peak_price': entry_price
                }

    # --- Endergebnis ---
    win_rate = (wins_count / trades_count * 100) if trades_count > 0 else 0
    final_pnl_pct = ((current_capital - start_capital) / start_capital) * 100 if start_capital > 0 else 0
    final_capital = max(0, current_capital)

    return {
        "total_pnl_pct": final_pnl_pct, "trades_count": trades_count,
        "win_rate": win_rate, "max_drawdown_pct": max_drawdown_pct * 100, # In Prozent umrechnen
        "end_capital": final_capital
    }
