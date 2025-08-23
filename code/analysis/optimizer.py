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
from analysis.backtest import run_backtest, load_data_for_backtest, LOWER_TF_MAP

def run_optimization(start_date, end_date, timeframes_str, symbols_list, sl_multiplier=None, capital=1000.0):
    print("Loading base configuration...")
    config_path = os.path.join(os.path.dirname(__file__), '..', 'strategies', 'envelope', 'config.json')
    with open(config_path, 'r') as f:
        default_params = json.load(f)

    symbols_to_optimize = symbols_list or [default_params['symbol']]
    overall_best_results = []

    for symbol_arg in symbols_to_optimize:
        base_params = default_params.copy()
        if sl_multiplier: base_params['stop_loss_atr_multiplier'] = sl_multiplier
        
        raw_symbol = symbol_arg
        if '/' not in raw_symbol: base_params['symbol'] = f"{raw_symbol.upper()}/USDT:USDT"
        else: base_params['symbol'] = raw_symbol.upper()
        
        print(f"\n\n#################### STARTING OPTIMIZATION FOR: {base_params['symbol']} ####################")
        
        timeframes_to_test = timeframes_str.split()
        param_grid = {
            'leverage': [1, 3, 5, 7, 10],
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
        
        total_runs = sum(1 for p in param_combinations if not (not p['enable_trailing_take_profit'] and p['trailing_take_profit_drawdown_pct'] != param_grid['trailing_take_profit_drawdown_pct'][0])) * len(timeframes_to_test)
        current_run = 0
        
        print(f"\nStarting optimization run for '{base_params['symbol']}' on {len(timeframes_to_test)} timeframes with a total of {total_runs} combinations...")

        for timeframe in timeframes_to_test:
            print(f"\n--- Processing timeframe: {timeframe} ---")
            
            data_main = load_data_for_backtest(base_params['symbol'], timeframe, start_date, end_date)
            if data_main is None or data_main.empty:
                print(f"No main timeframe data for {timeframe}. Skipping.")
                continue

            lower_timeframe = LOWER_TF_MAP.get(timeframe)
            data_lower = None
            if lower_timeframe:
                data_lower = load_data_for_backtest(base_params['symbol'], lower_timeframe, start_date, end_date)
                if data_lower is None or data_lower.empty:
                    print(f"Warning: Could not load lower TF data for {lower_timeframe}. Running simple simulation for {timeframe}.")

            for params_to_test in param_combinations:
                if not params_to_test['enable_trailing_take_profit'] and params_to_test['trailing_take_profit_drawdown_pct'] != param_grid['trailing_take_profit_drawdown_pct'][0]:
                    continue
                current_run += 1
                print(f"\rTesting combination {current_run}/{total_runs}...", end="")

                current_params = base_params.copy()
                current_params.update(params_to_test)
                current_params['timeframe'] = timeframe
                
                result = run_backtest(data_main.copy(), data_lower.copy() if data_lower is not None else None, current_params, initial_capital=capital, verbose=False)
                all_results.append(result)

        if not all_results:
            print(f"\nNo results for {base_params['symbol']}.")
            continue
            
        print("\n\n--- Optimization finished ---")
        results_df = pd.DataFrame(all_results)
        if results_df.empty:
            print(f"\nNo valid results for {base_params['symbol']}.")
            continue
            
        params_df = pd.json_normalize(results_df['params'])
        results_df = pd.concat([results_df.drop(['params', 'trade_log'], axis=1), params_df], axis=1)
        
        sorted_results = results_df.sort_values(by=['profit_usdt', 'win_rate', 'trades_count'], ascending=[False, False, False])
        if not sorted_results.empty:
            overall_best_results.append(sorted_results.iloc[0].to_dict())

        top_10_results = sorted_results.head(10)
        print(f"\nBest results for {base_params['symbol']} (Top 10 across all timeframes, Startkapital: ${capital:,.2f}):")
        
        for i, row in top_10_results.reset_index(drop=True).iterrows():
            print("\n" + "="*35)
            print(f"         --- RANK {i + 1} ---")
            print("="*35)
            print("\n  PERFORMANCE:")
            print(f"    Gewinn (USDT):          ${row.get('profit_usdt', 0):,.2f}")
            print(f"    Gewinn (Prozent):       {row.get('total_pnl_pct', 0):.2f} %")
            print(f"    Win Rate:               {row.get('win_rate', 0):.2f} %")
            print(f"    Anzahl Trades:          {int(row.get('trades_count', 0))}")

            print("\n  RISIKO-ANALYSE:")
            print(f"    Max. Portfolio-Rückgang: {row.get('max_drawdown_pct', 0):.2f} % (der tatsächliche, gehebelte Verlust)")
            safe_leverage = row.get('max_safe_leverage', np.inf)
            leverage_text = f"{safe_leverage:.2f}x" if safe_leverage != np.inf else "Keine Verluste"
            print(f"    Theoret. max. Hebel:    {leverage_text} (um den schlimmsten Rückgang zu überleben)")

            print("\n  GEWÄHLTE PARAMETER:")
            print(f"    Angewandter Hebel:      {int(row.get('leverage', 0))}x")
            print(f"    Timeframe:              {row.get('timeframe', 'N/A')}")
            print(f"    Prüfintervall (sim.):   {row.get('simulation_tf', 'N/A')}")
            print(f"    ST ATR Period:          {int(row.get('st_atr_period', 0))}")
            print(f"    ST Multiplier:          {row.get('st_atr_multiplier', 0):.1f}")
            print(f"    SL Multiplier:          {row.get('stop_loss_atr_multiplier', 0):.1f}")
            if row.get('enable_trailing_take_profit', False):
                print(f"    Trailing TP:            Enabled ({row.get('trailing_take_profit_drawdown_pct', 0):.1f}% drawdown)")
            else:
                print(f"    Trailing TP:            Disabled")
        
        if not sorted_results.empty:
            best_run_params_dict = sorted_results.iloc[0].to_dict()
            best_params_config = best_run_params_dict 

            print("\n" + "="*70)
            print("         TRADE-PROTOKOLL FÜR BESTE KONFIGURATION (RANK 1)")
            print("="*70)
            
            best_tf = best_params_config.get('timeframe')
            
            if best_tf:
                data_for_final_run = load_data_for_backtest(base_params['symbol'], best_tf, start_date, end_date)
                data_lower_for_final_run = None
                lower_tf_final = LOWER_TF_MAP.get(best_tf)
                if lower_tf_final:
                    data_lower_for_final_run = load_data_for_backtest(base_params['symbol'], lower_tf_final, start_date, end_date)

                if data_for_final_run is not None:
                    final_run_params = {
                        'symbol': base_params['symbol'], 
                        'timeframe': best_tf, 'leverage': best_params_config.get('leverage'),
                        'st_atr_period': best_params_config.get('st_atr_period'),
                        'st_atr_multiplier': best_params_config.get('st_atr_multiplier'),
                        'stop_loss_atr_multiplier': best_params_config.get('stop_loss_atr_multiplier'),
                        'enable_trailing_take_profit': best_params_config.get('enable_trailing_take_profit'),
                        'trailing_take_profit_drawdown_pct': best_params_config.get('trailing_take_profit_drawdown_pct')
                    }
                    
                    final_result = run_backtest(data_for_final_run.copy(), data_lower_for_final_run.copy() if data_lower_for_final_run is not None else None, final_run_params, initial_capital=capital, verbose=False)
                    trade_log = final_result['trade_log']
                    
                    if trade_log:
                        print(f"\n{'-'*75}")
                        print(f"{'Datum':<20} | {'Richtung':>8} | {'Typ':<12} | {'PnL (USDT)':>15} | {'Kontostand':>15}")
                        print(f"{'-'*75}")
                        for trade in trade_log:
                            print(f"{trade['exit_time'].strftime('%Y-%m-%d %H:%M'):<20} | {trade['side'].capitalize():>8} | {trade['reason']:<12} | ${trade['pnl_usdt']:>14,.2f} | ${trade['capital_after']:>14,.2f}")
                        print(f"{'-'*75}\n")
                    else:
                        print("\nKeine Trades im besten Lauf ausgeführt.")
            else:
                print("\nFehler: Timeframe für den finalen Lauf konnte nicht ermittelt werden.")
        
        print(f"#################### END OF OPTIMIZATION FOR: {base_params['symbol']} ####################\n")

    if len(overall_best_results) > 1:
        print("\n\n#################### FINAL OVERALL SUMMARY (BEST RUN PER COIN) ####################")
        summary_df = pd.DataFrame(overall_best_results)
        final_ranking = summary_df.sort_values(by=['profit_usdt'], ascending=False).reset_index(drop=True)

        print(f"\nRanking der Handelspaare nach bestem Ergebnis (Startkapital: ${capital:,.2f}):")

        for i, row in final_ranking.iterrows():
            print("\n" + "="*50)
            print(f"                 --- OVERALL RANK {i + 1} ---")
            print("="*50)
            print(f"\n  TRADING PAIR: {row.get('symbol')}")
            print("\n  PERFORMANCE:")
            print(f"    Gewinn (USDT):          ${row.get('profit_usdt', 0):,.2f}")
            print(f"    Gewinn (Prozent):       {row.get('total_pnl_pct', 0):.2f} %")
            
            print("\n  RISIKO-ANALYSE:")
            print(f"    Max. Portfolio-Rückgang: {row.get('max_drawdown_pct', 0):.2f} % (gehebelter Verlust vom Höchststand)")
            safe_leverage = row.get('max_safe_leverage', np.inf)
            leverage_text = f"{safe_leverage:.2f}x" if safe_leverage != np.inf else "Keine Verluste"
            print(f"    Theoret. max. Hebel:    {leverage_text} (um den schlimmsten Rückgang zu überleben)")

            print("\n  BESTE PARAMETER FÜR DIESEN COIN:")
            print(f"    Angewandter Hebel:      {int(row.get('leverage', 0))}x")
            print(f"    Timeframe:              {row.get('timeframe', 'N/A')}")
            print(f"    Prüfintervall (sim.):   {row.get('simulation_tf', 'N/A')}")
            print(f"    ST ATR Period:          {int(row.get('st_atr_period', 0))}")
            print(f"    ST Multiplier:          {row.get('st_atr_multiplier', 0):.1f}")
            print(f"    SL Multiplier:          {row.get('stop_loss_atr_multiplier', 0):.1f}")
            if row.get('enable_trailing_take_profit', False):
                print(f"    Trailing TP:            Enabled ({row.get('trailing_take_profit_drawdown_pct', 0):.1f}% drawdown)")
            else:
                print(f"    Trailing TP:            Disabled")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Strategy optimizer for the Supertrend Bot.")
    parser.add_argument('--capital', type=float, default=1000.0, help="Initial capital for the backtest in USDT.")
    parser.add_argument('--start', required=True, help="Start date in YYYY-MM-DD format")
    parser.add_argument('--end', required=True, help="End date in YYYY-MM-DD format")
    parser.add_argument('--timeframes', required=True, help="A list of timeframes, separated by spaces")
    parser.add_argument('--symbols', nargs='+', help="One or more trading pairs (e.g., BTC ETH SOL)")
    parser.add_argument('--sl_multiplier', type=float, help="Optional Stop-Loss ATR multiplier (e.g., 1.5)")
    args = parser.parse_args()

    run_optimization(args.start, args.end, args.timeframes, args.symbols, args.sl_multiplier, args.capital)
