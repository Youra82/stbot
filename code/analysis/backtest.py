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
    
    data_main_tf_with_signals = calculate_signals(data_main_tf, params)

    if data_lower_tf is not None:
        if verbose:
            print(f"\nStarte präzise Simulation: Haupt-TF={main_timeframe}, Simulations-TF={LOWER_TF_MAP.get(main_timeframe)}")
        
        simulation_tf_to_report = LOWER_TF_MAP.get(main_timeframe, main_timeframe)
        
        columns_to_merge = ['buy_signal', 'sell_signal', 'atr']
        if 'adx' in data_main_tf_with_signals.columns:
            columns_to_merge.append('adx')
        if 'donchian_upper' in data_main_tf_with_signals.columns:
            columns_to_merge.extend(['donchian_upper', 'donchian_lower'])
        
        sim_data = pd.merge_asof(
            data_lower_tf,
            data_main_tf_with_signals[columns_to_merge],
            left_index=True, right_index=True, direction='backward'
        )
        sim_data.dropna(inplace=True)
    else:
        if verbose: print(f"\nFühre einfachen Backtest auf '{main_timeframe}' aus.")
        sim_data = data_main_tf_with_signals

    fee_pct = params.get('fee_percentage', 0.05) / 100.0
    trade_size_pct = params.get('trade_size_pct', 100.0) / 100.0

    hebel_params = params.get('hebel_einstellungen', {})
    enable_dynamic_leverage = hebel_params.get('enable_dynamic_leverage', False)
    
    sl_params = params.get('stop_loss_einstellungen', {})
    enable_donchian_sl = sl_params.get('enable_donchian_channel_sl', False)
    st_params = params.get('supertrend_einstellungen', {})
    sl_atr_multiplier = st_params.get('sl_atr_multiplier', 1.5)

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
    is_liquidated = False
    current_leverage = hebel_params.get('fallback_leverage', 10)

    for i in range(1, len(sim_data)):
        if is_liquidated:
            break

        prev_candle = sim_data.iloc[i-1]
        current_candle = sim_data.iloc[i]

        def close_position(exit_price, reason, exit_time):
            nonlocal capital, peak_capital, max_drawdown_pct, trades_count, wins_count, in_position, is_liquidated
            
            pnl_percentage = (exit_price - entry_price) / entry_price if position_side == 'long' else (entry_price - exit_price) / entry_price
            net_pnl_pct = (pnl_percentage * current_leverage) - (2 * fee_pct * current_leverage)
            
            margin_used = capital * trade_size_pct
            capital_change_usdt = margin_used * net_pnl_pct
            capital += capital_change_usdt
            
            if capital > peak_capital: peak_capital = capital
            drawdown = (peak_capital - capital) / peak_capital
            if drawdown > max_drawdown_pct: max_drawdown_pct = drawdown

            if capital <= 0:
                capital, is_liquidated, reason = 0, True, "LIQUIDATION"

            if verbose: 
                print(f"{exit_time.strftime('%Y-%m-%d %H:%M')} | {reason.ljust(12)}| PnL: {net_pnl_pct*100:.2f}% (${capital_change_usdt:,.2f}) | New Capital: ${capital:,.2f}")

            trades_count += 1
            if net_pnl_pct > 0 and not is_liquidated: wins_count += 1
            in_position = False
            trade_log.append({"exit_time": exit_time, "side": position_side, "reason": reason, "pnl_usdt": capital_change_usdt, "capital_after": capital})

        if in_position:
            pnl_at_low = ((current_candle['low'] - entry_price) / entry_price) * current_leverage if position_side == 'long' else ((entry_price - current_candle['high']) / entry_price) * current_leverage
            if 1 + pnl_at_low <= 0:
                liquidation_price = entry_price * (1 - (1/current_leverage)) if position_side == 'long' else entry_price * (1 + (1/current_leverage))
                close_position(liquidation_price, "LIQUIDATION", current_candle.name)
                continue
            
            if (position_side == 'long' and current_candle['low'] <= stop_loss_price) or \
               (position_side == 'short' and current_candle['high'] >= stop_loss_price):
                close_position(stop_loss_price, 'STOP-LOSS', current_candle.name)
                continue

            if (prev_candle['buy_signal'] and position_side == 'short') or \
               (prev_candle['sell_signal'] and position_side == 'long'):
                close_position(current_candle['open'], 'GEGENSIGNAL', current_candle.name)
                continue

        if not in_position:
            side_to_open = None
            if prev_candle['buy_signal'] and params.get('use_longs', True):
                side_to_open = 'long'
            elif prev_candle['sell_signal'] and params.get('use_shorts', True):
                side_to_open = 'short'
            
            if side_to_open:
                in_position = True
                position_side = side_to_open
                entry_price = current_candle['open']
                
                if enable_dynamic_leverage and 'adx' in prev_candle and pd.notna(prev_candle['adx']):
                    if prev_candle['adx'] >= hebel_params.get('adx_strong_trend_threshold', 25):
                        current_leverage = hebel_params.get('leverage_strong_trend', 15)
                    else:
                        current_leverage = hebel_params.get('leverage_weak_trend', 5)
                else:
                    current_leverage = hebel_params.get('fallback_leverage', 10)

                if enable_donchian_sl and 'donchian_lower' in prev_candle and pd.notna(prev_candle['donchian_lower']):
                    stop_loss_price = prev_candle['donchian_lower'] if position_side == 'long' else prev_candle['donchian_upper']
                else:
                    stop_loss_price = entry_price - (prev_candle['atr'] * sl_atr_multiplier) if position_side == 'long' else entry_price + (prev_candle['atr'] * sl_atr_multiplier)
                
                if verbose:
                    print(f"{current_candle.name.strftime('%Y-%m-%d %H:%M')} | OPEN {position_side.upper()} | @ {entry_price:.4f} | SL: {stop_loss_price:.4f} | LEV: {current_leverage}x")
    
    win_rate = (wins_count / trades_count * 100) if trades_count > 0 else 0
    return {
        "profit_usdt": capital - initial_capital,
        "total_pnl_pct": (capital - initial_capital) / initial_capital * 100,
        "trades_count": trades_count,
        "win_rate": win_rate,
        "max_drawdown_pct": max_drawdown_pct * 100,
        "params": params,
        "simulation_tf": simulation_tf_to_report,
        "trade_log": trade_log
    }

def load_data_for_backtest(symbol, timeframe, start_date_str, end_date_str):
    cache_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'historical_data')
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
        print(f"Lade Daten für {timeframe} von {download_start_date} bis {end_date_str} für {symbol}...")
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
            print(f"\nFehler beim Download der Daten für {timeframe}: {e}")
    if data is not None and not data.empty: return data.loc[start_date_str:end_date_str]
    return None
