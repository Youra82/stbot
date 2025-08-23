# code/analysis/backtest.py
import os
import sys
import json
import pandas as pd
import argparse
import numpy as np
import re

# Adjust path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from utilities.bitget_futures import BitgetFutures
from utilities.strategy_logic import calculate_signals

LOWER_TF_MAP = {
    '15m': '5m', '30m': '10m', '1h': '15m', '2h': '30m',
    '4h': '1h', '6h': '2h', '12h': '4h', '1d': '4h'
}

def parse_timeframe_to_minutes(tf_str):
    if not isinstance(tf_str, str): return 0
    match = re.match(r"(\d+)(\w)", tf_str)
    if not match: return 0
    value, unit = int(match.group(1)), match.group(2).lower()
    if unit == 'm': return value
    elif unit == 'h': return value * 60
    elif unit == 'd': return value * 24 * 60
    return 0

def run_backtest(data_main_tf, params, verbose=True):
    main_timeframe = params['timeframe']
    lower_timeframe = LOWER_TF_MAP.get(main_timeframe)
    simulation_tf_to_report = main_timeframe # Fallback, falls kleinerer TF fehlschlägt

    if lower_timeframe:
        if verbose:
            print(f"\nStarte präzise Simulation: Haupt-TF={main_timeframe}, Simulations-TF={lower_timeframe}")
        
        start_date = data_main_tf.index[0].strftime('%Y-%m-%d')
        end_date = data_main_tf.index[-1].strftime('%Y-%m-%d')
        data_lower_tf = load_data_for_backtest(params['symbol'], lower_timeframe, start_date, end_date)

        if data_lower_tf is not None and not data_lower_tf.empty:
            simulation_tf_to_report = lower_timeframe
            data_main_tf_with_signals = calculate_signals(data_main_tf, params)
            sim_data = pd.merge_asof(
                data_lower_tf,
                data_main_tf_with_signals[['buy_signal', 'sell_signal', 'atr']],
                left_index=True, right_index=True, direction='backward'
            )
            sim_data.dropna(inplace=True)
        else:
            if verbose: print(f"\nWARNUNG: Konnte keine Daten für '{lower_timeframe}' laden. Führe einfachen Backtest auf '{main_timeframe}' aus.")
            sim_data = calculate_signals(data_main_tf, params)
    else:
        if verbose: print(f"\nFühre einfachen Backtest auf '{main_timeframe}' aus (kein kleinerer TF definiert).")
        sim_data = calculate_signals(data_main_tf, params)

    leverage = params.get('leverage', 1.0)
    sl_multiplier = params.get('stop_loss_atr_multiplier', 1.5)
    fee_pct = 0.05 / 100
    enable_ttp = params.get('enable_trailing_take_profit', False)
    ttp_drawdown_pct = params.get('trailing_take_profit_drawdown_pct', 1.5)

    initial_capital = 1000.0
    capital = initial_capital
    peak_capital = initial_capital
    max_drawdown_pct = 0.0

    in_position = False
    position_side = None
    entry_price = 0.0
    stop_loss_price = 0.0
    trades_count = 0
    wins_count = 0
    peak_pnl_pct_trade = 0.0
    
    for i in range(1, len(sim_data)):
        prev_candle = sim_data.iloc[i-1]
        current_candle = sim_data.iloc[i]

        def close_position(exit_price, reason, exit_time):
            nonlocal capital, peak_capital, max_drawdown_pct, trades_count, wins_count, in_position
            pnl_percentage = 0.0
            if position_side == 'long': pnl_percentage = (exit_price - entry_price) / entry_price
            elif position_side == 'short': pnl_percentage = (entry_price - exit_price) / entry_price
            net_pnl_pct = (pnl_percentage * leverage) - (2 * fee_pct * leverage)
            capital += capital * net_pnl_pct
            if capital > peak_capital: peak_capital = capital
            drawdown = (peak_capital - capital) / peak_capital
            if drawdown > max_drawdown_pct: max_drawdown_pct = drawdown
            if verbose: print(f"{exit_time.strftime('%Y-%m-%d %H:%M')} | {reason.ljust(12)}| PnL: {net_pnl_pct*100:.2f}% | New Capital: {capital:.2f}")
            trades_count += 1
            if net_pnl_pct > 0: wins_count += 1
            in_position = False

        if in_position:
            if position_side == 'long': unrealized_pnl_pct = ((current_candle['low'] - entry_price) / entry_price) * leverage
            else: unrealized_pnl_pct = ((entry_price - current_candle['high']) / entry_price) * leverage
            unrealized_capital = capital * (1 + unrealized_pnl_pct)
            if unrealized_capital < peak_capital:
                 temp_drawdown = (peak_capital - unrealized_capital) / peak_capital
                 if temp_drawdown > max_drawdown_pct: max_drawdown_pct = temp_drawdown
            
            if enable_ttp and lower_timeframe: # TTP nur im präzisen Modus
                if position_side == 'long': pnl_at_high_pct = ((current_candle['high'] - entry_price) / entry_price) * leverage * 100
                else: pnl_at_high_pct = ((entry_price - current_candle['low']) / entry_price) * leverage * 100
                peak_pnl_pct_trade = max(peak_pnl_pct_trade, pnl_at_high_pct)
                if position_side == 'long': pnl_at_close_pct = ((current_candle['close'] - entry_price) / entry_price) * leverage * 100
                else: pnl_at_close_pct = ((entry_price - current_candle['close']) / entry_price) * leverage * 100
                profit_drawdown = peak_pnl_pct_trade - pnl_at_close_pct
                if peak_pnl_pct_trade > ttp_drawdown_pct and profit_drawdown >= ttp_drawdown_pct:
                    target_pnl_pct = peak_pnl_pct_trade - ttp_drawdown_pct
                    target_pnl_ratio = target_pnl_pct / (100 * leverage)
                    if position_side == 'long': exit_price = entry_price * (1 + target_pnl_ratio)
                    else: exit_price = entry_price * (1 - target_pnl_ratio)
                    close_position(exit_price, 'TRAIL-PROFIT', current_candle.name)
                    continue

            if position_side == 'long' and current_candle['low'] <= stop_loss_price:
                close_position(stop_loss_price, 'STOP-LOSS', current_candle.name)
                continue
            elif position_side == 'short' and current_candle['high'] >= stop_loss_price:
                close_position(stop_loss_price, 'STOP-LOSS', current_candle.name)
                continue

            if position_side == 'long' and prev_candle['sell_signal']:
                close_position(current_candle['open'], 'CLOSE LONG', current_candle.name)
                continue
            elif position_side == 'short' and prev_candle['buy_signal']:
                close_position(current_candle['open'], 'CLOSE SHORT', current_candle.name)
                continue

        if not in_position:
            if prev_candle['buy_signal'] and params.get('use_longs', True):
                in_position = True
                position_side = 'long'
                entry_price = current_candle['open']
                stop_loss_price = entry_price - (prev_candle['atr'] * sl_multiplier)
                peak_pnl_pct_trade = 0.0
                if verbose: print(f"{current_candle.name.strftime('%Y-%m-%d %H:%M')} | OPEN LONG   | @ {entry_price:.2f} | SL: {stop_loss_price:.2f}")
            elif prev_candle['sell_signal'] and params.get('use_shorts', True):
                in_position = True
                position_side = 'short'
                entry_price = current_candle['open']
                stop_loss_price = entry_price + (prev_candle['atr'] * sl_multiplier)
                peak_pnl_pct_trade = 0.0
                if verbose: print(f"{current_candle.name.strftime('%Y-%m-%d %H:%M')} | OPEN SHORT  | @ {entry_price:.2f} | SL: {stop_loss_price:.2f}")

    win_rate = (wins_count / trades_count * 100) if trades_count > 0 else 0
    final_pnl_pct = (capital - initial_capital) / initial_capital * 100
    max_safe_leverage = (1 / max_drawdown_pct) if max_drawdown_pct > 0 else np.inf
    
    return {
        "total_pnl_pct": final_pnl_pct, "trades_count": trades_count, "win_rate": win_rate,
        "max_drawdown_pct": max_drawdown_pct * 100, "max_safe_leverage": max_safe_leverage,
        "params": params, "simulation_tf": simulation_tf_to_report # <-- NEU HINZUGEFÜGT
    }

def load_data_for_backtest(symbol, timeframe, start_date_str, end_date_str):
    cache_dir = os.path.join(os.path.dirname(__file__), 'historical_data')
    os.makedirs(cache_dir, exist_ok=True)
    symbol_filename = symbol.replace('/', '-').replace(':', '-')
    cache_file = os.path.join(cache_dir, f"{symbol_filename}_{timeframe}.csv")
    data = None
    if os.path.exists(cache_file):
        try:
            data = pd.read_csv(cache_file, index_col='timestamp', parse_dates=True)
            data.index = pd.to_datetime(data.index, utc=True)
        except Exception: data = None
    download_start_date = start_date_str
    if data is not None and not data.empty:
        last_cached_date = data.index[-1].strftime('%Y-%m-%d')
        if pd.to_datetime(last_cached_date) < pd.to_datetime(end_date_str):
            timeframe_minutes = parse_timeframe_to_minutes(timeframe)
            if timeframe_minutes > 0:
                 download_start_date = (data.index[-1] + pd.Timedelta(minutes=timeframe_minutes)).strftime('%Y-%m-%d %H:%M:%S')
        else: download_start_date = None
    if download_start_date:
        print(f"Loading data for {timeframe} from {download_start_date} to {end_date_str} for {symbol}...")
        try:
            project_root = os.path.join(os.path.dirname(__file__), '..', '..')
            key_path = os.path.abspath(os.path.join(project_root, 'secret.json'))
            with open(key_path, "r") as f: api_setup = json.load(f)['envelope']
            bitget = BitgetFutures(api_setup)
            new_data = bitget.fetch_historical_ohlcv(symbol, timeframe, download_start_date, end_date_str)
            if new_data is not None and not new_data.empty:
                data = pd.concat([data, new_data]) if data is not None else new_data
                data = data[~data.index.duplicated(keep='first')]
                data.sort_index(inplace=True)
                data.to_csv(cache_file)
        except Exception as e:
            print(f"\nAn error occurred while downloading data for {timeframe}: {e}")
    if data is not None and not data.empty: return data.loc[start_date_str:end_date_str]
    return None

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Strategy backtest for the Supertrend Bot.")
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--timeframe', required=True)
    parser.add_argument('--symbols', nargs='+')
    parser.add_argument('--leverage', type=float)
    parser.add_argument('--sl_multiplier', type=float)
    args = parser.parse_args()
    config_path = os.path.join(os.path.dirname(__file__), '..', 'strategies', 'envelope', 'config.json')
    with open(config_path, 'r') as f: base_params = json.load(f)
    symbols_to_test = args.symbols if args.symbols else [base_params['symbol']]
    for symbol_arg in symbols_to_test:
        params = base_params.copy()
        params['timeframe'] = args.timeframe
        if args.leverage: params['leverage'] = args.leverage
        if args.sl_multiplier: params['stop_loss_atr_multiplier'] = args.sl_multiplier
        # ...
        data_for_backtest_main = load_data_for_backtest(params['symbol'], args.timeframe, args.start, args.end)
        if data_for_backtest_main is not None and not data_for_backtest_main.empty:
            params_for_run = params.copy()
            params_for_run['symbol_display'] = params['symbol']
            run_backtest(data_for_backtest_main, params_for_run)
        else:
            print(f"No data available for symbol {params['symbol']} in the specified period.")
