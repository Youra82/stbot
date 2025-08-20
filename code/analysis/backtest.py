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

    # Parameters for realistic backtesting
    leverage = params.get('leverage', 1.0)
    sl_multiplier = params.get('stop_loss_atr_multiplier', 1.5)
    
    in_position = False
    position_side = None
    entry_price = 0.0
    stop_loss_price = 0.0
    total_pnl = 0.0
    trades_count = 0
    wins_count = 0
    entry_index = 0
    worst_drawdown_overall = 0.0
    
    fee_pct = 0.05 / 100

    for i in range(1, len(data)):
        prev_candle = data.iloc[i-1]
        current_candle = data.iloc[i]

        def close_position(exit_price, reason):
            nonlocal total_pnl, trades_count, wins_count, in_position, worst_drawdown_overall
            
            pnl = 0.0
            if position_side == 'long':
                pnl = (((exit_price - entry_price) / entry_price) * leverage) - (2 * fee_pct * leverage)
            elif position_side == 'short':
                pnl = (((entry_price - exit_price) / entry_price) * leverage) - (2 * fee_pct * leverage)

            # Drawdown calculation for the closed trade
            trade_candles = data.iloc[entry_index : i + 1] # CORRECTED LINE
            if not trade_candles.empty:
                if position_side == 'long':
                    lowest_price_during_trade = trade_candles['low'].min()
                    trade_drawdown = (entry_price - lowest_price_during_trade) / entry_price
                else: # short
                    highest_price_during_trade = trade_candles['high'].max()
                    trade_drawdown = (highest_price_during_trade - entry_price) / entry_price
                
                if trade_drawdown > worst_drawdown_overall:
                    worst_drawdown_overall = trade_drawdown

            if verbose: print(f"{current_candle.name.strftime('%Y-%m-%d %H:%M')} | {reason.ljust(12)}| PnL: {pnl*100:.2f}%")

            total_pnl += pnl
            trades_count += 1
            if pnl > 0: wins_count += 1
            in_position = False

        if in_position:
            if position_side == 'long' and current_candle['low'] <= stop_loss_price:
                close_position(stop_loss_price, 'STOP-LOSS')
                continue
            elif position_side == 'short' and current_candle['high'] >= stop_loss_price:
                close_position(stop_loss_price, 'STOP-LOSS')
                continue

        if in_position:
            if position_side == 'long' and prev_candle['sell_signal']:
                close_position(current_candle['open'], 'CLOSE LONG')
            elif position_side == 'short' and prev_candle['buy_signal']:
                close_position(current_candle['open'], 'CLOSE SHORT')

        if not in_position:
            if prev_candle['buy_signal'] and params.get('use_longs', True):
                in_position = True
                position_side = 'long'
                entry_price = current_candle['open']
                entry_index = i
                stop_loss_price = entry_price - (prev_candle['atr'] * sl_multiplier)
                if verbose: print(f"{current_candle.name.strftime('%Y-%m-%d %H:%M')} | OPEN LONG   | @ {entry_price:.2f} | SL: {stop_loss_price:.2f}")

            elif prev_candle['sell_signal'] and params.get('use_shorts', True):
                in_position = True
                position_side = 'short'
                entry_price = current_candle['open']
                entry_index = i
                stop_loss_price = entry_price + (prev_candle['atr'] * sl_multiplier)
                if verbose: print(f"{current_candle.name.strftime('%Y-%m-%d %H:%M')} | OPEN SHORT  | @ {entry_price:.2f} | SL: {stop_loss_price:.2f}")

    win_rate = (wins_count / trades_count * 100) if trades_count > 0 else 0
    
    max_safe_leverage = (1 / worst_drawdown_overall) if worst_drawdown_overall > 0 else np.inf

    if verbose:
        print("\n--- Backtest Results ---")
        print(f"Period: {data.index[0].strftime('%Y-%m-%d')} -> {data.index[-1].strftime('%Y-%m-%d')}")
        if 'symbol_display' in params:
            print(f"Symbol: {params['symbol_display']}")
        print(f"Timeframe: {params['timeframe']}")
        print(f"Leverage: {leverage}x | SL-Multiplier: {sl_multiplier}")
        print(f"Parameters: st_atr_period={params['st_atr_period']}, st_atr_multiplier={params['st_atr_multiplier']}")
        print("-" * 27)
        print(f"Total PnL (leveraged): {total_pnl * 100:.2f}%")
        print(f"Number of Trades: {trades_count}")
        print(f"Won Trades: {wins_count}")
        print(f"Win Rate: {win_rate:.2f}%")
        leverage_text = f"{max_safe_leverage:.2f}x" if max_safe_leverage != np.inf else "No losses (theoretically infinite)"
        print(f"Max Safe Leverage: {leverage_text}")
        print("---------------------------")

    return {
        "total_pnl_pct": total_pnl * 100,
        "trades_count": trades_count,
        "win_rate": win_rate,
        "max_safe_leverage": max_safe_leverage,
        "params": params
    }

def load_data_for_backtest(symbol, timeframe, start_date_str, end_date_str):
    """Loads and caches historical data."""
    cache_dir = os.path.join(os.path.dirname(__file__), 'historical_data')
    os.makedirs(cache_dir, exist_ok=True)
    symbol_filename = symbol.replace('/', '-').replace(':', '-')
    cache_file = os.path.join(cache_dir, f"{symbol_filename}_{timeframe}.csv")

    data = None
    if os.path.exists(cache_file):
        print(f"Loading data from local cache file: {cache_file}")
        data = pd.read_csv(cache_file, index_col='timestamp', parse_dates=True)
        data.index = pd.to_datetime(data.index, utc=True)

    download_start_date = start_date_str
    if data is not None and not data.empty:
        last_cached_date = data.index[-1].strftime('%Y-%m-%d')
        print(f"Last date in cache: {last_cached_date}")
        if pd.to_datetime(last_cached_date) < pd.to_datetime(end_date_str):
            download_start_date = (data.index[-1] + pd.Timedelta(minutes=int(timeframe.replace('m','')))).strftime('%Y-%m-%d %H:%M:%S')
        else:
            print("Cache is up to date. No new data to download.")
            download_start_date = None

    if download_start_date:
        print(f"Loading new data from {download_start_date} to {end_date_str} for {symbol}...")
        try:
            project_root = os.path.join(os.path.dirname(__file__), '..', '..')
            key_path = os.path.abspath(os.path.join(project_root, 'secret.json'))
            
            with open(key_path, "r") as f:
                api_setup = json.load(f)['envelope']
            bitget = BitgetFutures(api_setup)
            new_data = bitget.fetch_historical_ohlcv(symbol, timeframe, download_start_date, end_date_str)
            
            if new_data is not None and not new_data.empty:
                data = pd.concat([data, new_data]) if data is not None else new_data
                data = data[~data.index.duplicated(keep='first')]
                data.sort_index(inplace=True)
                data.to_csv(cache_file)
                print("Cache file has been updated.")
            else:
                print("No new data received.")

        except Exception as e:
            print(f"\nAn error occurred while downloading data: {e}")
            return None
    
    if data is not None and not data.empty:
        return data.loc[start_date_str:end_date_str]
    return None

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Strategy backtest for the Supertrend Bot.")
    parser.add_argument('--start', required=True, help="Start date in YYYY-MM-DD format")
    parser.add_argument('--end', required=True, help="End date in YYYY-MM-DD format")
    parser.add_argument('--timeframe', required=True, help="Timeframe (e.g., 15m, 1h, 4h, 1d)")
    parser.add_argument('--symbols', nargs='+', help="One or more trading pairs (e.g., BTC ETH SOL), overrides config.json")
    parser.add_argument('--leverage', type=float, help="Optional leverage (e.g., 10)")
    parser.add_argument('--sl_multiplier', type=float, help="Optional Stop-Loss ATR multiplier (e.g., 1.5)")
    args = parser.parse_args()

    print("Loading configuration...")
    config_path = os.path.join(os.path.dirname(__file__), '..', 'strategies', 'envelope', 'config.json')
    with open(config_path, 'r') as f:
        base_params = json.load(f)
    
    symbols_to_test = args.symbols if args.symbols else [base_params['symbol']]

    for symbol_arg in symbols_to_test:
        
        params = base_params.copy()
        params['timeframe'] = args.timeframe

        if args.leverage:
            params['leverage'] = args.leverage
        if args.sl_multiplier:
            params['stop_loss_atr_multiplier'] = args.sl_multiplier

        raw_symbol = symbol_arg
        if '/' not in raw_symbol:
            formatted_symbol = f"{raw_symbol.upper()}/USDT:USDT"
            print(f"\n\n==================== STARTING TEST FOR: {formatted_symbol} ====================")
            params['symbol'] = formatted_symbol
        else:
            formatted_symbol = raw_symbol.upper()
            print(f"\n\n==================== STARTING TEST FOR: {formatted_symbol} ====================")
            params['symbol'] = formatted_symbol

        data_for_backtest = load_data_for_backtest(params['symbol'], args.timeframe, args.start, args.end)
        
        if data_for_backtest is not None and not data_for_backtest.empty:
            params_for_run = params.copy()
            params_for_run['symbol_display'] = params['symbol'] 

            data_with_signals = calculate_signals(data_for_backtest, params)
            
            run_backtest(data_with_signals, params_for_run)

        else:
            print(f"No data available for symbol {params['symbol']} in the specified period.")
        
        print(f"==================== END OF TEST FOR: {formatted_symbol} =====================\n")
