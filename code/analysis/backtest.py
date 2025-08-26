# code/analysis/backtest.py
import os
import sys
import json
import pandas as pd
import argparse
import numpy as np
import re

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

def run_backtest(data_main_tf, data_lower_tf, params, initial_capital=1000.0, verbose=True):
    main_timeframe = params['timeframe']
    simulation_tf_to_report = main_timeframe
    sim_data = None
    
    if data_lower_tf is not None:
        if verbose:
            print(f"\nStarte präzise Simulation: Haupt-TF={main_timeframe}, Simulations-TF={LOWER_TF_MAP.get(main_timeframe)}")
        
        simulation_tf_to_report = LOWER_TF_MAP.get(main_timeframe, main_timeframe)
        data_main_tf_with_signals = calculate_signals(data_main_tf, params)
        sim_data = pd.merge_asof(
            data_lower_tf,
            data_main_tf_with_signals[['buy_signal', 'sell_signal', 'atr']],
            left_index=True, right_index=True, direction='backward'
        )
        sim_data.dropna(inplace=True)
    
    if sim_data is None:
        if verbose: print(f"\nFühre einfachen Backtest auf '{main_timeframe}' aus.")
        sim_data = calculate_signals(data_main_tf, params)

    leverage = params.get('leverage', 1.0)
    sl_multiplier = params.get('stop_loss_atr_multiplier', 1.5)
    fee_pct = 0.05 / 100
    enable_ttp = params.get('enable_trailing_take_profit', False)
    ttp_drawdown_pct = params.get('trailing_take_profit_drawdown_pct', 1.5)
    trade_size_pct = params.get('trade_size_pct', 100.0) / 100.0 # Als Ratio 0-1

    trade_log = []
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
    is_liquidated = False

    for i in range(1, len(sim_data)):
        if is_liquidated:
            break

        prev_candle = sim_data.iloc[i-1]
        current_candle = sim_data.iloc[i]

        def close_position(exit_price, reason, exit_time):
            nonlocal capital, peak_capital, max_drawdown_pct, trades_count, wins_count, in_position, is_liquidated
            
            pnl_percentage = 0.0
            if position_side == 'long': pnl_percentage = (exit_price - entry_price) / entry_price
            elif position_side == 'short': pnl_percentage = (entry_price - exit_price) / entry_price

            net_pnl_pct = (pnl_percentage * leverage) - (2 * fee_pct * leverage)
            
            capital_before_trade = capital
            
            # --- KORREKTUR: Wende den PnL nur auf den eingesetzten Kapitalanteil an ---
            margin_used = capital_before_trade * trade_size_pct
            capital_change_usdt = margin_used * net_pnl_pct
            capital += capital_change_usdt
            
            if capital > peak_capital: peak_capital = capital
            
            drawdown = (peak_capital - capital) / peak_capital
            if drawdown > max_drawdown_pct: max_drawdown_pct = drawdown

            if capital <= 0:
                capital = 0
                is_liquidated = True
                reason = "LIQUIDATION"

            if verbose: 
                price_format = ".8f" if entry_price < 1 else ".2f"
                print(f"{exit_time.strftime('%Y-%m-%d %H:%M')} | {reason.ljust(12)}| PnL: {net_pnl_pct*100:.2f}% (${capital_change_usdt:,.2f}) | New Capital: ${capital:,.2f}")

            trades_count += 1
            if net_pnl_pct > 0 and not is_liquidated: wins_count += 1
            in_position = False

            trade_log.append({
                "exit_time": exit_time, "side": position_side, "reason": reason,
                "pnl_usdt": capital_change_usdt, "capital_after": capital
            })

        if in_position:
            unrealized_capital_at_risk = (capital * trade_size_pct)
            if position_side == 'long': unrealized_pnl_pct = ((current_candle['low'] - entry_price) / entry_price) * leverage
            else: unrealized_pnl_pct = ((entry_price - current_candle['high']) / entry_price) * leverage
            
            unrealized_loss = unrealized_capital_at_risk * unrealized_pnl_pct if unrealized_pnl_pct < 0 else 0
            unrealized_capital = capital + unrealized_loss

            if unrealized_capital <= 0:
                liquidation_price = entry_price * (1 - (1/leverage)) if position_side == 'long' else entry_price * (1 + (1/leverage))
                close_position(liquidation_price, "LIQUIDATION", current_candle.name)
                continue

            if unrealized_capital < peak_capital:
                 temp_drawdown = (peak_capital - unrealized_capital) / peak_capital
                 if temp_drawdown > max_drawdown_pct: max_drawdown_pct = temp_drawdown
            
            if enable_ttp and data_lower_tf is not None:
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

            if prev_candle['buy_signal'] and (position_side == 'short'):
                close_position(current_candle['open'], 'CLOSE SHORT', current_candle.name)
                continue
            elif prev_candle['sell_signal'] and (position_side == 'long'):
                close_position(current_candle['open'], 'CLOSE LONG', current_candle.name)
                continue

        if not in_position:
            if prev_candle['buy_signal'] and params.get('use_longs', True):
                in_position = True
                position_side = 'long'
                entry_price = current_candle['open']
                stop_loss_price = entry_price - (prev_candle['atr'] * sl_multiplier)
                peak_pnl_pct_trade = 0.0
                if verbose:
                    price_format = ".8f" if entry_price < 1 else ".2f"
                    print(f"{current_candle.name.strftime('%Y-%m-%d %H:%M')} | OPEN LONG   | @ {entry_price:{price_format}} | SL: {stop_loss_price:{price_format}}")
            elif prev_candle['sell_signal'] and params.get('use_shorts', True):
                in_position = True
                position_side = 'short'
                entry_price = current_candle['open']
                stop_loss_price = entry_price + (prev_candle['atr'] * sl_multiplier)
                peak_pnl_pct_trade = 0.0
                if verbose:
                    price_format = ".8f" if entry_price < 1 else ".2f"
                    print(f"{current_candle.name.strftime('%Y-%m-%d %H:%M')} | OPEN SHORT  | @ {entry_price:{price_format}} | SL: {stop_loss_price:{price_format}}")

    win_rate = (wins_count / trades_count * 100) if trades_count > 0 else 0
    final_pnl_pct = (capital - initial_capital) / initial_capital * 100 if initial_capital > 0 else -100
    profit_usdt = capital - initial_capital
    max_safe_leverage = (1 / max_drawdown_pct) if max_drawdown_pct > 0 else np.inf
    
    return {
        "total_pnl_pct": final_pnl_pct, "profit_usdt": profit_usdt, "trades_count": trades_count, 
        "win_rate": win_rate, "max_drawdown_pct": max_drawdown_pct * 100, "max_safe_leverage": max_safe_leverage,
        "params": params, "simulation_tf": simulation_tf_to_report, "trade_log": trade_log
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
    parser.add_argument('--capital', type=float, default=1000.0)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--timeframe', required=True)
    parser.add_argument('--symbols', nargs='+')
    parser.add_argument('--leverage', type=float)
    parser.add_argument('--st_period', type=int)
    parser.add_argument('--st_multiplier', type=float)
    parser.add_argument('--sl_multiplier', type=float)
    parser.add_argument('--ttp_enabled', action='store_true')
    parser.add_argument('--ttp_drawdown', type=float)
    args = parser.parse_args()
    
    config_path = os.path.join(os.path.dirname(__file__), '..', 'strategies', 'envelope', 'config.json')
    with open(config_path, 'r') as f: base_params = json.load(f)
    
    symbols_to_test = args.symbols if args.symbols else [base_params['symbol']]

    for symbol_arg in symbols_to_test:
        params = base_params.copy()
        
        params['timeframe'] = args.timeframe
        if args.leverage is not None: params['leverage'] = args.leverage
        if args.st_period is not None: params['st_atr_period'] = args.st_period
        if args.st_multiplier is not None: params['st_atr_multiplier'] = args.st_multiplier
        if args.sl_multiplier is not None: params['stop_loss_atr_multiplier'] = args.sl_multiplier
        if args.ttp_enabled: params['enable_trailing_take_profit'] = True
        if args.ttp_drawdown is not None: params['trailing_take_profit_drawdown_pct'] = args.ttp_drawdown
        
        raw_symbol = symbol_arg
        if '/' not in raw_symbol: params['symbol'] = f"{raw_symbol.upper()}/USDT:USDT"
        else: params['symbol'] = raw_symbol.upper()
        
        print(f"\n\n==================== STARTING TEST FOR: {params['symbol']} ====================")
        
        data_for_backtest_main = load_data_for_backtest(params['symbol'], args.timeframe, args.start, args.end)
        
        if data_for_backtest_main is not None and not data_for_backtest_main.empty:
            params_for_run = params.copy()
            params_for_run['symbol_display'] = params['symbol']
            
            lower_tf = LOWER_TF_MAP.get(args.timeframe)
            data_for_backtest_lower = None
            if lower_tf:
                data_for_backtest_lower = load_data_for_backtest(params['symbol'], lower_tf, args.start, args.end)

            final_results = run_backtest(data_for_backtest_main, data_for_backtest_lower, params_for_run, initial_capital=args.capital, verbose=True)
            
            trade_log = final_results.get('trade_log', [])
            if trade_log:
                print(f"\n{'-'*75}")
                print(f"{'Datum':<20} | {'Richtung':>8} | {'Typ':<12} | {'PnL (USDT)':>15} | {'Kontostand':>15}")
                print(f"{'-'*75}")
                for trade in trade_log:
                    print(f"{trade['exit_time'].strftime('%Y-%m-%d %H:%M'):<20} | {trade['side'].capitalize():>8} | {trade['reason']:<12} | ${trade['pnl_usdt']:>14,.2f} | ${trade['capital_after']:>14,.2f}")
                print(f"{'-'*75}\n")
        else:
            print(f"No data available for symbol {params['symbol']} in the specified period.")
        
        print(f"==================== END OF TEST FOR: {params['symbol']} =====================\n")
