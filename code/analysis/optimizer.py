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

    if not symbols_list:
        symbols_to_optimize = [default_params['symbol']]
    else:
        symbols_to_optimize = symbols_list

    overall_best_results = []

    for symbol_arg in symbols_to_optimize:
        
        base_params = default_params.copy()

        if leverage:
            base_params['leverage'] = leverage
        if sl_multiplier:
            base_params['stop_loss_atr_multiplier'] = sl_multiplier
            print(f"INFO: Using fixed SL-Multiplier of {sl_multiplier} for the run.")

        raw_symbol = symbol_arg
        if '/' not in raw_symbol:
            formatted_symbol = f"{raw_symbol.upper()}/USDT:USDT"
            base_params['symbol'] = formatted_symbol
        else:
            base_params['symbol'] = raw_symbol.upper()
        
        print(f"\n\n#################### STARTING OPTIMIZATION FOR: {base_params['symbol']} ####################")
        
        timeframes_to_test = timeframes_str.split()
        
        # --- Parameter-Gitter für die Optimierung ---
        param_grid = {
            'st_atr_period': [10, 14, 21],
            'st_atr_multiplier': [2.5, 3.0, 3.5],
            'stop_loss_atr_multiplier': [1.0, 1.5, 2.0],
            'enable_trailing_take_profit': [True, False], 
            'trailing_take_profit_drawdown_pct': [1.0, 1.5, 2.0]
        }
        
        if sl_multiplier:
            del param_grid['stop_loss_atr_multiplier']
        
        print(f"INFO: Fixed leverage: {base_params.get('leverage', '1.0')}x.")

        keys, values = zip(*param_grid.items())
        param_combinations = [dict(zip(keys, v)) for v in product(*values)]
        
        all_results = []
        total_runs = len(param_combinations) * len(timeframes_to_test)
        current_run = 0
        
        print(f"\nStarting optimization run for '{base_params['symbol']}' on {len(timeframes_to_test)} timeframes with a total of {total_runs} combinations...")

        for timeframe in timeframes_to_test:
            print(f"\n--- Processing timeframe: {timeframe} ---")
            
            data = load_data_for_backtest(base_params['symbol'], timeframe, start_date, end_date)
            if data is None or data.empty:
                print(f"No data found for timeframe {timeframe}. Skipping.")
                current_run += len(param_combinations)
                continue

            for params_to_test in param_combinations:
                current_run += 1
                print(f"\rTesting combination {current_run}/{total_runs}...", end="")

                if not params_to_test['enable_trailing_take_profit'] and params_to_test['trailing_take_profit_drawdown_pct'] != param_grid['trailing_take_profit_drawdown_pct'][0]:
                    continue

                required_data_points = params_to_test.get('st_atr_period', 10) * 2
                if len(data) < required_data_points:
                    continue

                current_params = base_params.copy()
                current_params.update(params_to_test)
                current_params['timeframe'] = timeframe

                data_with_signals = calculate_signals(data.copy(), current_params)
                result = run_backtest(data_with_signals, current_params, verbose=False)
                all_results.append(result)

        if not all_results:
            print(f"\n\nNo results achieved for {base_params['symbol']}.")
            continue
            
        print("\n\n--- Optimization finished ---")
        results_df = pd.DataFrame(all_results)
        
        params_df = pd.json_normalize(results_df['params'])
        results_df = pd.concat([results_df.drop('params', axis=1), params_df], axis=1)
        
        sorted_results = results_df.sort_values(
            by=['total_pnl_pct', 'win_rate', 'trades_count'], 
            ascending=[False, False, False]
        )

        if not sorted_results.empty:
            best_run_for_this_symbol = sorted_results.iloc[0].to_dict()
            overall_best_results.append(best_run_for_this_symbol)

        top_10_results = sorted_results.head(10)

        print(f"\nBest results for {base_params['symbol']} (Top 10 across all timeframes):")
        
        for i, row in top_10_results.reset_index(drop=True).iterrows():
            platz = i + 1
            print("\n" + "="*30)
            print(f"         --- RANK {platz} ---")
            print("="*30)
            print("\n  PERFORMANCE:")
            print(f"    Profit (PnL):       {row['total_pnl_pct']:.2f} %")
            print(f"    Win Rate:           {row['win_rate']:.2f} %")
            print(f"    Number of Trades:   {int(row['trades_count'])}")
            print(f"    Max Drawdown:       {row.get('max_drawdown_pct', 0):.2f} %")
            
            safe_leverage = row.get('max_safe_leverage', np.inf)
            leverage_text = f"{safe_leverage:.2f}x" if safe_leverage != np.inf else "No losses"
            print(f"    Max Safe Leverage:  {leverage_text}")

            print("\n  PARAMETERS USED:")
            print(f"    Leverage:           {row['leverage']}x")
            print(f"    Timeframe:          {row['timeframe']}")
            print(f"    ST ATR Period:      {int(row['st_atr_period'])}")
            print(f"    ST Multiplier:      {row['st_atr_multiplier']:.1f}")
            print(f"    SL Multiplier:      {row['stop_loss_atr_multiplier']:.1f}")
            if row.get('enable_trailing_take_profit', False):
                print(f"    Trailing TP:        Enabled ({row.get('trailing_take_profit_drawdown_pct', 0):.1f}% drawdown)")
            else:
                print(f"    Trailing TP:        Disabled")

        print("\n" + "="*30)
        print(f"#################### END OF OPTIMIZATION FOR: {base_params['symbol']} ####################\n")

    if len(overall_best_results) > 1:
        print("\n\n#################### FINAL OVERALL SUMMARY (BEST RUN PER COIN) ####################")
        summary_df = pd.DataFrame(overall_best_results)
        final_ranking = summary_df.sort_values(
            by=['total_pnl_pct', 'win_rate', 'trades_count'],
            ascending=[False, False, False]
        ).reset_index(drop=True)

        print("\nRanking of trading pairs by best performance:")

        for i, row in final_ranking.iterrows():
            platz = i + 1
            print("\n" + "="*50)
            print(f"                 --- OVERALL RANK {platz} ---")
            print("="*50)
            print(f"\n  TRADING PAIR: {row['symbol']}")
            print("\n  PERFORMANCE:")
            print(f"    Profit (PnL):       {row['total_pnl_pct']:.2f} %")
            print(f"    Win Rate:           {row['win_rate']:.2f} %")
            print(f"    Number of Trades:   {int(row['trades_count'])}")
            
            safe_leverage = row.get('max_safe_leverage', np.inf)
            leverage_text = f"{safe_leverage:.2f}x" if safe_leverage != np.inf else "No losses"
            print(f"    Max Safe Leverage:  {leverage_text}")

            print("\n  BEST PARAMETERS FOR THIS COIN:")
            print(f"    Leverage:           {row['leverage']}x")
            print(f"    Timeframe:          {row['timeframe']}")
            print(f"    ST ATR Period:      {int(row['st_atr_period'])}")
            print(f"    ST Multiplier:      {row['st_atr_multiplier']:.1f}")
            print(f"    SL Multiplier:      {row['stop_loss_atr_multiplier']:.1f}")
            if row.get('enable_trailing_take_profit', False):
                print(f"    Trailing TP:        Enabled ({row.get('trailing_take_profit_drawdown_pct', 0):.1f}% drawdown)")
            else:
                print(f"    Trailing TP:        Disabled")
        
        print("\n" + "="*50)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Strategy optimizer for the Supertrend Bot.")
    parser.add_argument('--start', required=True, help="Start date in YYYY-MM-DD format")
    parser.add_argument('--end', required=True, help="End date in YYYY-MM-DD format")
    parser.add_argument('--timeframes', required=True, help="A list of timeframes, separated by spaces")
    parser.add_argument('--symbols', nargs='+', help="One or more trading pairs (e.g., BTC ETH SOL)")
    parser.add_argument('--leverage', type=float, help="Optional leverage (e.g., 10)")
    parser.add_argument('--sl_multiplier', type=float, help="Optional Stop-Loss ATR multiplier (e.g., 1.5)")
    args = parser.parse_args()

    run_optimization(args.start, args.end, args.timeframes, args.symbols, args.leverage, args.sl_multiplier)
