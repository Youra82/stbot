# code/analysis/optimizer.py
import os
import sys
import json
import pandas as pd
import argparse
from itertools import product
import numpy as np

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from utilities.strategy_logic import calculate_signals
from analysis.backtest import run_backtest, load_data_for_backtest

def run_optimization(start_date, end_date, timeframes_str, symbols_list, leverage=None, sl_multiplier=None):
    print("Loading base configuration...")
    config_path = os.path.join(os.path.dirname(__file__), '..', 'strategies', 'envelope', 'config.json')
    with open(config_path, 'r') as f:
        default_params = json.load(f)

    symbols_to_optimize = symbols_list or [default_params['symbol']]
    overall_best_results = []

    for symbol_arg in symbols_to_optimize:
        base_params = default_params.copy()
        if leverage: base_params['leverage'] = leverage
        if sl_multiplier: base_params['stop_loss_atr_multiplier'] = sl_multiplier
        
        raw_symbol = symbol_arg
        if '/' not in raw_symbol: base_params['symbol'] = f"{raw_symbol.upper()}/USDT:USDT"
        else: base_params['symbol'] = raw_symbol.upper()
        
        print(f"\n\n#################### STARTING OPTIMIZATION FOR: {base_params['symbol']} ####################")
        
        timeframes_to_test = timeframes_str.split()
        param_grid = {
            'st_atr_period': [10, 14, 21],
            'st_atr_multiplier': [2.5, 3.0, 3.5],
            'stop_loss_atr_multiplier': [1.0, 1.5, 2.0],
            'enable_trailing_take_profit': [True, False], 
            'trailing_take_profit_drawdown_pct': [1.0, 1.5, 2.0]
        }
        if sl_multiplier: del param_grid['stop_loss_atr_multiplier']
        
        keys, values = zip(*param_grid.items())
        param_combinations = [dict(zip(keys, v)) for v in product(*values)]
        all_results = []
        
        for timeframe in timeframes_to_test:
            print(f"\n--- Processing timeframe: {timeframe} ---")
            data = load_data_for_backtest(base_params['symbol'], timeframe, start_date, end_date)
            if data is None or data.empty:
                print(f"No main timeframe data for {timeframe}. Skipping.")
                continue

            for params_to_test in param_combinations:
                # ... (Logik zum Überspringen und Vorbereiten der Parameter)
                current_params = base_params.copy()
                current_params.update(params_to_test)
                current_params['timeframe'] = timeframe
                
                result = run_backtest(data.copy(), current_params, verbose=False) # Wichtig: .copy()
                all_results.append(result)

        if not all_results: continue
            
        print("\n\n--- Optimization finished ---")
        results_df = pd.DataFrame(all_results)
        params_df = pd.json_normalize(results_df['params'])
        # --- NEU: Auch 'simulation_tf' aus den Ergebnissen holen ---
        results_df = pd.concat([results_df.drop('params', axis=1), params_df, results_df['simulation_tf']], axis=1)
        
        sorted_results = results_df.sort_values(by=['total_pnl_pct', 'win_rate', 'trades_count'], ascending=[False, False, False])
        if not sorted_results.empty:
            overall_best_results.append(sorted_results.iloc[0].to_dict())

        top_10_results = sorted_results.head(10)
        print(f"\nBest results for {base_params['symbol']} (Top 10 across all timeframes):")
        
        for i, row in top_10_results.reset_index(drop=True).iterrows():
            print("\n" + "="*30)
            print(f"         --- RANK {i + 1} ---")
            print("="*30)
            print("\n  PERFORMANCE:")
            print(f"    Profit (PnL):       {row['total_pnl_pct']:.2f} %")
            print(f"    Win Rate:           {row['win_rate']:.2f} %")
            print(f"    Number of Trades:   {int(row['trades_count'])}")
            print(f"    Max Drawdown:       {row.get('max_drawdown_pct', 0):.2f} %")

            print("\n  PARAMETERS USED:")
            print(f"    Timeframe:          {row['timeframe']}")
            # --- NEU: Anzeige des Prüfintervalls ---
            print(f"    Prüfintervall:      {row.get('simulation_tf', 'N/A')}")
            print(f"    ST ATR Period:      {int(row['st_atr_period'])}")
            print(f"    ST Multiplier:      {row['st_atr_multiplier']:.1f}")
            print(f"    SL Multiplier:      {row['stop_loss_atr_multiplier']:.1f}")
            if row.get('enable_trailing_take_profit', False):
                print(f"    Trailing TP:        Enabled ({row.get('trailing_take_profit_drawdown_pct', 0):.1f}% drawdown)")
            else:
                print(f"    Trailing TP:        Disabled")

    # (Finale Zusammenfassung, ebenfalls angepasst)
    if len(overall_best_results) > 1:
        print("\n\n#################### FINAL OVERALL SUMMARY (BEST RUN PER COIN) ####################")
        summary_df = pd.DataFrame(overall_best_results)
        #... (Sortierlogik)
        for i, row in summary_df.iterrows():
            print("\n  BEST PARAMETERS FOR THIS COIN:")
            print(f"    Timeframe:          {row['timeframe']}")
            # --- NEU: Anzeige des Prüfintervalls ---
            print(f"    Prüfintervall:      {row.get('simulation_tf', 'N/A')}")
            # ... (restliche Parameter)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Strategy optimizer for the Supertrend Bot.")
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--timeframes', required=True)
    parser.add_argument('--symbols', nargs='+')
    parser.add_argument('--leverage', type=float)
    parser.add_argument('--sl_multiplier', type=float)
    args = parser.parse_args()
    run_optimization(args.start, args.end, args.timeframes, args.symbols, args.leverage, args.sl_multiplier)
