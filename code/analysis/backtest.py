# code/analysis/backtest.py
import os
import sys
import json
import pandas as pd
import argparse
import numpy as np

# Adjust path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from utilities.bitget_futures import BitgetFutures
from utilities.strategy_logic import calculate_signals

def run_backtest(data, params, verbose=True):
    if verbose:
        print("\nRunning backtest...")

    # Parameters
    leverage = params.get('leverage', 1.0)
    sl_multiplier = params.get('stop_loss_atr_multiplier', 1.5)
    fee_pct = 0.05 / 100
    
    # --- NEU: Trailing Take-Profit Parameter ---
    enable_ttp = params.get('enable_trailing_take_profit', False)
    ttp_drawdown_pct = params.get('trailing_take_profit_drawdown_pct', 1.5)

    # Equity and Drawdown Tracking
    initial_capital = 1000.0
    capital = initial_capital
    peak_capital = initial_capital
    max_drawdown_pct = 0.0

    # Trade State
    in_position = False
    position_side = None
    entry_price = 0.0
    stop_loss_price = 0.0
    trades_count = 0
    wins_count = 0
    peak_pnl_pct_trade = 0.0 # Höchster PnL des aktuellen Trades

    for i in range(1, len(data)):
        prev_candle = data.iloc[i-1]
        current_candle = data.iloc[i]

        def close_position(exit_price, reason):
            nonlocal capital, peak_capital, max_drawdown_pct, total_pnl, trades_count, wins_count, in_position
            
            pnl_percentage = 0.0
            if position_side == 'long':
                pnl_percentage = (exit_price - entry_price) / entry_price
            elif position_side == 'short':
                pnl_percentage = (entry_price - exit_price) / entry_price

            net_pnl_pct = (pnl_percentage * leverage) - (2 * fee_pct * leverage)
            
            capital += capital * net_pnl_pct
            
            if capital > peak_capital:
                peak_capital = capital
            
            drawdown = (peak_capital - capital) / peak_capital
            if drawdown > max_drawdown_pct:
                max_drawdown_pct = drawdown

            if verbose: print(f"{current_candle.name.strftime('%Y-%m-%d %H:%M')} | {reason.ljust(12)}| PnL: {net_pnl_pct*100:.2f}% | New Capital: {capital:.2f}")

            trades_count += 1
            if net_pnl_pct > 0: wins_count += 1
            in_position = False

        if in_position:
            # Unrealized PnL für Drawdown-Berechnung
            unrealized_low_pnl_pct = (((current_candle['low'] - entry_price) / entry_price if position_side == 'long' else (entry_price - current_candle['high']) / entry_price) * leverage)
            unrealized_low_capital = capital * (1 + unrealized_low_pnl_pct) # Simuliert Kapital während des Kerzentiefs
            if unrealized_low_capital < peak_capital:
                 temp_drawdown = (peak_capital - unrealized_low_capital) / peak_capital
                 if temp_drawdown > max_drawdown_pct:
                    max_drawdown_pct = temp_drawdown
            
            # --- NEU: TRAILING TAKE-PROFIT LOGIK ---
            if enable_ttp:
                # 1. Höchsten PnL der Kerze finden und Peak aktualisieren
                pnl_at_high_pct = (((current_candle['high'] - entry_price) / entry_price if position_side == 'long' else (entry_price - current_candle['low']) / entry_price) * leverage) * 100
                peak_pnl_pct_trade = max(peak_pnl_pct_trade, pnl_at_high_pct)

                # 2. PnL am Kerzenschluss berechnen
                pnl_at_close_pct = (((current_candle['close'] - entry_price) / entry_price if position_side == 'long' else (entry_price - current_candle['close']) / entry_price) * leverage) * 100

                # 3. Trigger-Bedingung prüfen
                profit_drawdown = peak_pnl_pct_trade - pnl_at_close_pct
                if peak_pnl_pct_trade > ttp_drawdown_pct and profit_drawdown >= ttp_drawdown_pct:
                    # Berechne den exakten Ausstiegspreis
                    target_pnl_pct = peak_pnl_pct_trade - ttp_drawdown_pct
                    target_pnl_ratio = target_pnl_pct / (100 * leverage)
                    
                    if position_side == 'long':
                        exit_price = entry_price * (1 + target_pnl_ratio)
                    else: # short
                        exit_price = entry_price * (1 - target_pnl_ratio)

                    close_position(exit_price, 'TRAIL-PROFIT')
                    continue # Gehe zur nächsten Kerze

            # STOP-LOSS LOGIK
            if position_side == 'long' and current_candle['low'] <= stop_loss_price:
                close_position(stop_loss_price, 'STOP-LOSS')
                continue
            elif position_side == 'short' and current_candle['high'] >= stop_loss_price:
                close_position(stop_loss_price, 'STOP-LOSS')
                continue

            # COUNTER-SIGNAL LOGIK
            if position_side == 'long' and prev_candle['sell_signal']:
                close_position(current_candle['open'], 'CLOSE LONG')
            elif position_side == 'short' and prev_candle['buy_signal']:
                close_position(current_candle['open'], 'CLOSE SHORT')

        if not in_position:
            if prev_candle['buy_signal'] and params.get('use_longs', True):
                in_position = True
                position_side = 'long'
                entry_price = current_candle['open']
                stop_loss_price = entry_price - (prev_candle['atr'] * sl_multiplier)
                peak_pnl_pct_trade = 0.0 # Reset für neuen Trade
                if verbose: print(f"{current_candle.name.strftime('%Y-%m-%d %H:%M')} | OPEN LONG   | @ {entry_price:.2f} | SL: {stop_loss_price:.2f}")

            elif prev_candle['sell_signal'] and params.get('use_shorts', True):
                in_position = True
                position_side = 'short'
                entry_price = current_candle['open']
                stop_loss_price = entry_price + (prev_candle['atr'] * sl_multiplier)
                peak_pnl_pct_trade = 0.0 # Reset für neuen Trade
                if verbose: print(f"{current_candle.name.strftime('%Y-%m-%d %H:%M')} | OPEN SHORT  | @ {entry_price:.2f} | SL: {stop_loss_price:.2f}")

    win_rate = (wins_count / trades_count * 100) if trades_count > 0 else 0
    final_pnl_pct = (capital - initial_capital) / initial_capital * 100
    
    max_safe_leverage = (1 / max_drawdown_pct) if max_drawdown_pct > 0 else np.inf
    
    # Die 'verbose' Ausgabe und das 'return'-Statement bleiben wie in der letzten Version
    if verbose:
        print("\n--- Backtest Results ---")
        print(f"Period: {data.index[0].strftime('%Y-%m-%d')} -> {data.index[-1].strftime('%Y-%m-%d')}")
        if 'symbol_display' in params:
            print(f"Symbol: {params['symbol_display']}")
        print(f"Timeframe: {params['timeframe']}")
        print(f"Leverage: {leverage}x | SL-Multiplier: {sl_multiplier}")
        print(f"Parameters: st_atr_period={params['st_atr_period']}, st_atr_multiplier={params['st_atr_multiplier']}")
        if enable_ttp:
            print(f"Trailing TP: Enabled ({ttp_drawdown_pct}% drawdown)")
        print("-" * 27)
        print(f"Total PnL (leveraged): {final_pnl_pct:.2f}%")
        print(f"Final Capital: {capital:.2f} (from {initial_capital})")
        print(f"Number of Trades: {trades_count}")
        print(f"Won Trades: {wins_count}")
        print(f"Win Rate: {win_rate:.2f}%")
        print(f"Max Portfolio Drawdown: {max_drawdown_pct * 100:.2f}%")
        leverage_text = f"{max_safe_leverage:.2f}x" if max_safe_leverage != np.inf else "No losses"
        print(f"Max Safe Leverage: {leverage_text}")
        print("---------------------------")

    return {
        "total_pnl_pct": final_pnl_pct,
        "trades_count": trades_count,
        "win_rate": win_rate,
        "max_drawdown_pct": max_drawdown_pct * 100,
        "max_safe_leverage": max_safe_leverage,
        "params": params
    }

# Der Rest der Datei (load_data_for_backtest, main block) bleibt unverändert
# ...
