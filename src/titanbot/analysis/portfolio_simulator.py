# src/titanbot/analysis/portfolio_simulator.py (Version für STBot Indikator-Strategie)
import pandas as pd
import numpy as np
from tqdm import tqdm
import sys
import os
import ta 
import math 

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from titanbot.strategy.trade_logic import get_st_signal, calculate_ema, calculate_rsi, calculate_macd

def calculate_stbot_indicators(data: pd.DataFrame, strat_key: str, strategy_params: dict):
    """ Berechnet alle benötigten Indikatoren für die STBot Strategie. """
    temp_data = data.copy()
    
    # Indikator-Perioden aus Strategie-Params
    ema_short = strategy_params.get('ema_short', 9)
    ema_long = strategy_params.get('ema_long', 21)
    rsi_period = strategy_params.get('rsi_period', 14)
    volume_ma_period = strategy_params.get('volume_ma_period', 20)
    
    # ATR (Bleibt für SL-Berechnung)
    if len(temp_data) >= 15: 
        atr_indicator = ta.volatility.AverageTrueRange(high=temp_data['high'], low=temp_data['low'], close=temp_data['close'], window=14)
        temp_data['atr'] = atr_indicator.average_true_range()
    else:
        print(f"WARNUNG: Nicht genug Daten ({len(temp_data)}) für ATR bei Strategie {strat_key}.")
        return pd.DataFrame()
    
    # MACD, RSI, EMA, Volume MA (Für Signallogik)
    if 'Close' not in temp_data.columns: temp_data['Close'] = temp_data['close']
    if 'Volume' not in temp_data.columns: temp_data['Volume'] = temp_data['volume']
    
    temp_data['EMA_short'] = calculate_ema(temp_data['Close'], ema_short)
    temp_data['EMA_long'] = calculate_ema(temp_data['Close'], ema_long)
    temp_data['MACD'], temp_data['MACD_Signal'], temp_data['MACD_Hist'] = calculate_macd(temp_data['Close'])
    temp_data['RSI'] = calculate_rsi(temp_data['Close'], rsi_period)
    temp_data['Volume_MA'] = temp_data['Volume'].rolling(window=volume_ma_period).mean()
    
    temp_data.dropna(subset=['atr', 'EMA_short', 'MACD', 'RSI'], inplace=True) # Zeilen ohne Indikatoren entfernen
    
    return temp_data if not temp_data.empty else pd.DataFrame()

def run_portfolio_simulation(start_capital, strategies_data, start_date, end_date):
    """
    Führt eine chronologische Portfolio-Simulation mit mehreren Indikator-Strategien (STBot) durch.
    """
    print("\n--- Starte Portfolio-Simulation (STBot)... ---")

    # --- 1. Kombiniere alle Zeitstempel & berechne Indikatoren ---
    all_timestamps = set()
    print("1/4: Berechne Indikatoren (ATR/EMA/RSI/MACD) für alle Strategien...")
    data_with_indicators = {}
    
    for key, strat in strategies_data.items():
        if 'data' in strat and not strat['data'].empty:
            try:
                # Nutze die neue Indikator-Funktion
                temp_data = calculate_stbot_indicators(strat['data'], key, strat.get('smc_params', {})) # smc_params enthält jetzt Indikator-Params
                
                if not temp_data.empty:
                    data_with_indicators[key] = temp_data 
                    all_timestamps.update(temp_data.index)
                else:
                    print(f"WARNUNG: Keine Daten für Strategie {key} nach Indikator-Berechnung übrig.")
            except Exception as e:
                print(f"FEHLER bei Indikator-Berechnung für {key}: {e}")
        else:
            print(f"WARNUNG: Keine Daten für Strategie {key} gefunden.")

    strategies_data_processed = {}
    for key, strat in strategies_data.items():
        if key in data_with_indicators:
            strategies_data_processed[key] = strat.copy()
            strategies_data_processed[key]['data'] = data_with_indicators[key]
            
    if not all_timestamps or not strategies_data_processed:
        print("Keine gültigen Daten für die Simulation gefunden (oder Indikatoren konnten nicht berechnet werden).")
        return None

    sorted_timestamps = sorted(list(all_timestamps))
    print(f"-> {len(sorted_timestamps)} eindeutige Zeitstempel gefunden.")

    # --- 2. Hier entfällt die SMC-Analyse, da die Signale direkt aus den Indikatoren kommen ---
    print("2/4: Signale werden im Simulationsschritt direkt aus den Indikatoren abgeleitet.")
    valid_strategies = strategies_data_processed

    # --- 3. Chronologische Simulation (Nutzt get_st_signal) ---
    print("3/4: Führe chronologische Backtests durch...")
    equity = start_capital
    peak_equity = start_capital
    max_drawdown_pct = 0.0
    max_drawdown_date = None
    min_equity_ever = start_capital
    liquidation_date = None

    open_positions = {}
    trade_history = []
    equity_curve = []

    # Konstanten aus Backtester
    fee_pct = 0.05 / 100
    max_allowed_effective_leverage = 10
    absolute_max_notional_value = 1000000
    min_notional = 5.0

    for ts in tqdm(sorted_timestamps, desc="Simuliere Portfolio"):
        if liquidation_date: break

        current_total_equity = equity
        unrealized_pnl = 0

        # --- 3a. Offene Positionen managen (Unverändert) ---
        positions_to_close = []
        for key, pos in open_positions.items():
            strat_data = valid_strategies.get(key)
            if not strat_data or ts not in strat_data['data'].index:
                if pos.get('last_known_price'):
                    pnl_mult = 1 if pos['side'] == 'long' else -1
                    unrealized_pnl += pos['notional_value'] * (pos['last_known_price'] / pos['entry_price'] -1) * pnl_mult
                continue

            current_candle = strat_data['data'].loc[ts]
            pos['last_known_price'] = current_candle['close']
            exit_price = None

            if pos['side'] == 'long':
                if not pos['trailing_active'] and current_candle['high'] >= pos['activation_price']:
                    pos['trailing_active'] = True
                if pos['trailing_active']:
                    pos['peak_price'] = max(pos['peak_price'], current_candle['high'])
                    trailing_sl = pos['peak_price'] * (1 - pos['callback_rate'])
                    pos['stop_loss'] = max(pos['stop_loss'], trailing_sl)
                if current_candle['low'] <= pos['stop_loss']: exit_price = pos['stop_loss']
                elif not pos['trailing_active'] and current_candle['high'] >= pos['take_profit']: exit_price = pos['take_profit']
            else: # Short
                if not pos['trailing_active'] and current_candle['low'] <= pos['activation_price']:
                    pos['trailing_active'] = True
                if pos['trailing_active']:
                    pos['peak_price'] = min(pos['peak_price'], current_candle['low'])
                    trailing_sl = pos['peak_price'] * (1 + pos['callback_rate'])
                    pos['stop_loss'] = min(pos['stop_loss'], trailing_sl)
                if current_candle['high'] >= pos['stop_loss']: exit_price = pos['stop_loss']
                elif not pos['trailing_active'] and current_candle['low'] <= pos['take_profit']: exit_price = pos['take_profit']

            if exit_price:
                pnl_pct = (exit_price / pos['entry_price'] - 1) if pos['side'] == 'long' else (1 - exit_price / pos['entry_price'])
                pnl_usd = pos['notional_value'] * pnl_pct
                total_fees = pos['notional_value'] * fee_pct * 2
                equity += (pnl_usd - total_fees)
                trade_history.append({'strategy_key': key, 'symbol': strat_data['symbol'], 'pnl': (pnl_usd - total_fees)})
                positions_to_close.append(key)
            else:
                pnl_mult = 1 if pos['side'] == 'long' else -1
                unrealized_pnl += pos['notional_value'] * (current_candle['close'] / pos['entry_price'] -1) * pnl_mult

        for key in positions_to_close:
            del open_positions[key]

        # --- 3b. Neue Signale prüfen und Positionen eröffnen ---
        if equity > 0:
            for key, strat in valid_strategies.items():
                if key not in open_positions and ts in strat['data'].index:
                    current_candle = strat['data'].loc[ts]
                    risk_params = strat.get('risk_params', {})
                    strategy_params = strat.get('smc_params', {}) # Enthält Indikator-Parameter

                    # NEU: Der Signal-Check benötigt den DataFrame, der nur bis zur aktuellen Kerze reicht
                    data_slice = strat['data'].loc[:ts].copy()
                    
                    params_for_logic = {"strategy": strategy_params, "risk": risk_params}
                    side, _ = get_st_signal(data_slice, current_candle, params=params_for_logic) 
                    
                    if side:
                        # Risiko- und Positionsgrößenberechnung (Unverändert)
                        entry_price = current_candle['close']
                        risk_per_trade_pct = risk_params.get('risk_per_trade_pct', 1.0) / 100
                        risk_reward_ratio = risk_params.get('risk_reward_ratio', 2.0)
                        leverage = risk_params.get('leverage', 10)
                        activation_rr = risk_params.get('trailing_stop_activation_rr', 2.0)
                        callback_rate = risk_params.get('trailing_stop_callback_rate_pct', 1.0) / 100
                        
                        atr_multiplier_sl = risk_params.get('atr_multiplier_sl', 2.0)
                        min_sl_pct = risk_params.get('min_sl_pct', 0.5) / 100.0

                        current_atr = current_candle.get('atr')
                        if pd.isna(current_atr) or current_atr <= 0:
                            continue

                        sl_distance_atr = current_atr * atr_multiplier_sl
                        sl_distance_min = entry_price * min_sl_pct
                        sl_distance = max(sl_distance_atr, sl_distance_min)
                        if sl_distance <= 0:
                            continue

                        risk_amount_usd = equity * risk_per_trade_pct
                        sl_distance_pct_equivalent = sl_distance / entry_price
                        if sl_distance_pct_equivalent <= 1e-6:
                            continue

                        calculated_notional_value = risk_amount_usd / sl_distance_pct_equivalent
                        max_notional_by_leverage = equity * max_allowed_effective_leverage
                        final_notional_value = min(calculated_notional_value, max_notional_by_leverage, absolute_max_notional_value)

                        if final_notional_value < min_notional:
                            continue

                        margin_used = math.ceil((final_notional_value / leverage) * 100) / 100

                        current_total_margin = sum(p['margin_used'] for p in open_positions.values())
                        if current_total_margin + margin_used > equity:
                            continue

                        stop_loss = entry_price - sl_distance if side == 'buy' else entry_price + sl_distance
                        take_profit = entry_price + sl_distance * risk_reward_ratio if side == 'buy' else entry_price - sl_distance * risk_reward_ratio
                        activation_price = entry_price + sl_distance * activation_rr if side == 'buy' else entry_price - sl_distance * activation_rr

                        open_positions[key] = {
                            'side': 'long' if side == 'buy' else 'short',
                            'entry_price': entry_price,
                            'stop_loss': stop_loss,
                            'take_profit': take_profit,
                            'notional_value': final_notional_value,
                            'margin_used': margin_used,
                            'trailing_active': False,
                            'activation_price': activation_price,
                            'peak_price': entry_price,
                            'callback_rate': callback_rate,
                            'last_known_price': entry_price
                        }

        # --- 3c. Equity Curve und Drawdown aktualisieren (Unverändert) ---
        current_total_equity = equity + unrealized_pnl
        equity_curve.append({'timestamp': ts, 'equity': current_total_equity})

        peak_equity = max(peak_equity, current_total_equity)
        drawdown = (peak_equity - current_total_equity) / peak_equity if peak_equity > 0 else 0
        if drawdown * 100 > max_drawdown_pct: 
            max_drawdown_pct = drawdown * 100
            max_drawdown_date = ts

        min_equity_ever = min(min_equity_ever, current_total_equity)
        if current_total_equity <= 0 and not liquidation_date:
            liquidation_date = ts

    # --- 4. Ergebnisse vorbereiten (Unverändert) ---
    print("4/4: Bereite Analyse-Ergebnisse vor...")
    final_equity = equity_curve[-1]['equity'] if equity_curve else start_capital
    total_pnl_pct = (final_equity / start_capital - 1) * 100 if start_capital > 0 else 0
    wins = sum(1 for t in trade_history if t['pnl'] > 0)
    win_rate = (wins / len(trade_history) * 100) if trade_history else 0

    trade_df = pd.DataFrame(trade_history)
    pnl_per_strategy = trade_df.groupby('strategy_key')['pnl'].sum().reset_index() if not trade_df.empty else pd.DataFrame(columns=['strategy_key', 'pnl'])
    trades_per_strategy = trade_df.groupby('strategy_key').size().reset_index(name='trades') if not trade_df.empty else pd.DataFrame(columns=['strategy_key', 'trades'])

    equity_df = pd.DataFrame(equity_curve)
    if not equity_df.empty:
        equity_df['peak'] = equity_df['equity'].cummax()
        equity_df['drawdown_pct'] = ((equity_df['peak'] - equity_df['equity']) / equity_df['peak'].replace(0, np.nan)).fillna(0)
        equity_df['timestamp'] = pd.to_datetime(equity_df['timestamp'])
        equity_df.set_index('timestamp', inplace=True, drop=False)

    print("Analyse abgeschlossen.")

    return {
        "start_capital": start_capital,
        "end_capital": final_equity,
        "total_pnl_pct": total_pnl_pct,
        "trade_count": len(trade_history),
        "win_rate": win_rate,
        "max_drawdown_pct": max_drawdown_pct, 
        "max_drawdown_date": max_drawdown_date,
        "min_equity": min_equity_ever,
        "liquidation_date": liquidation_date,
        "pnl_per_strategy": pnl_per_strategy,
        "trades_per_strategy": trades_per_strategy,
        "equity_curve": equity_df
    }
