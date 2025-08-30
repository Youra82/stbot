# code/analysis/optimizer.py
import os
import sys
import json
import pandas as pd
import argparse
from itertools import product
import numpy as np
import concurrent.futures # NEU: Import für die Parallelverarbeitung

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from utilities.strategy_logic import calculate_signals
from analysis.backtest import run_backtest, load_data_for_backtest, LOWER_TF_MAP

# NEU: Eine "Worker"-Funktion, die einen einzelnen Backtest ausführt.
# Diese Funktion wird von den verschiedenen Prozessen aufgerufen.
def process_backtest_combination(args):
    """Führt einen einzelnen Backtest für eine gegebene Parameterkombination aus."""
    params_to_test, run_params_template, data_main, data_lower, capital = args
    
    run_params = run_params_template.copy()
    
    # Parameter für diesen spezifischen Lauf anwenden
    run_params['supertrend_einstellungen']['st_atr_period'] = params_to_test['st_atr_period']
    run_params['supertrend_einstellungen']['st_atr_multiplier'] = params_to_test['st_atr_multiplier']
    if 'sl_atr_multiplier' in params_to_test:
        run_params['supertrend_einstellungen']['sl_atr_multiplier'] = params_to_test['sl_atr_multiplier']
    
    run_params['hebel_einstellungen']['enable_dynamic_leverage'] = params_to_test['enable_dynamic_leverage']
    run_params['hebel_einstellungen']['adx_strong_trend_threshold'] = params_to_test['adx_strong_trend_threshold']
    run_params['hebel_einstellungen']['leverage_strong_trend'] = params_to_test['leverage_strong_trend']
    run_params['hebel_einstellungen']['leverage_weak_trend'] = params_to_test['leverage_weak_trend']

    run_params['stop_loss_einstellungen']['enable_donchian_channel_sl'] = params_to_test['enable_donchian_channel_sl']
    run_params['stop_loss_einstellungen']['donchian_period'] = params_to_test['donchian_period']

    # Führe den Backtest aus und gib das Ergebnis zurück
    result = run_backtest(
        data_main.copy(), 
        data_lower.copy() if data_lower is not None else None, 
        run_params, 
        initial_capital=capital, 
        verbose=False
    )
    return result


def run_optimization(start_date, end_date, timeframes_str, symbols_list, sl_multiplier=None, capital=1000.0):
    print("Lade Basiskonfiguration...")
    config_path = os.path.join(os.path.dirname(__file__), '..', 'strategies', 'envelope', 'config.json')
    with open(config_path, 'r') as f:
        default_params = json.load(f)

    symbols_to_optimize = symbols_list or [default_params['symbol']]
    overall_best_results = []

    for symbol_arg in symbols_to_optimize:
        base_params = default_params.copy()
        raw_symbol = symbol_arg
        if '/' not in raw_symbol: base_params['symbol'] = f"{raw_symbol.upper()}/USDT:USDT"
        else: base_params['symbol'] = raw_symbol.upper()
        
        print(f"\n\n#################### STARTE OPTIMIERUNG FÜR: {base_params['symbol']} ####################")
        
        timeframes_to_test = timeframes_str.split()
        
        param_grid = {
            'st_atr_period': [10, 14, 21],
            'st_atr_multiplier': [2.5, 3.0, 3.5],
            'sl_atr_multiplier': [1.0, 1.5, 2.0],
            'enable_dynamic_leverage': [True, False],
            'adx_strong_trend_threshold': [22, 25, 28],
            'leverage_strong_trend': [15, 20, 25],
            'leverage_weak_trend': [5, 8, 10],
            'enable_donchian_channel_sl': [True, False],
            'donchian_period': [20, 30, 40]
        }
        
        if sl_multiplier is not None:
            base_params['supertrend_einstellungen']['sl_atr_multiplier'] = sl_multiplier
            del param_grid['sl_atr_multiplier']

        keys, values = zip(*param_grid.items())
        param_combinations = [dict(zip(keys, v)) for v in product(*values)]
        all_results = []
        
        total_runs = len(param_combinations) * len(timeframes_to_test)
        
        print(f"\nStarte Optimierung für '{base_params['symbol']}' auf {len(timeframes_to_test)} Timeframes mit {len(param_combinations)} Kombinationen pro TF (Total: {total_runs} Läufe)...")

        for timeframe in timeframes_to_test:
            print(f"\n--- Bearbeite Timeframe: {timeframe} ---")
            
            data_main = load_data_for_backtest(base_params['symbol'], timeframe, start_date, end_date)
            if data_main is None or data_main.empty:
                print(f"Keine Daten für {timeframe}. Überspringe."); continue

            lower_timeframe = LOWER_TF_MAP.get(timeframe)
            data_lower = load_data_for_backtest(base_params['symbol'], lower_timeframe, start_date, end_date) if lower_timeframe else None

            # GEÄNDERT: Die for-Schleife wird durch einen ProcessPoolExecutor ersetzt.
            
            # 1. Bereite eine Liste aller "Aufgaben" vor. Jede Aufgabe enthält alle nötigen Argumente.
            run_params_template = base_params.copy()
            run_params_template['timeframe'] = timeframe
            
            tasks = [
                (params, run_params_template, data_main, data_lower, capital)
                for params in param_combinations
            ]
            
            # 2. Starte den Pool mit 2 Prozessen (für deine 2 vCPUs)
            print(f"Verarbeite {len(tasks)} Kombinationen für {timeframe} auf 2 CPU-Kernen...")
            with concurrent.futures.ProcessPoolExecutor(max_workers=2) as executor:
                # 3. Führe die Aufgaben parallel aus und sammle die Ergebnisse
                # executor.map wendet die Funktion 'process_backtest_combination' auf jedes Element in 'tasks' an
                results_for_timeframe = list(executor.map(process_backtest_combination, tasks))
                all_results.extend(results_for_timeframe)

            print(f"Timeframe {timeframe} abgeschlossen.")


        if not all_results:
            print(f"\nKeine Ergebnisse für {base_params['symbol']}."); continue
            
        print("\n\n--- Optimierung abgeschlossen ---")
        results_df = pd.DataFrame(all_results)
        if results_df.empty:
            print(f"\nKeine validen Ergebnisse für {base_params['symbol']}.")
            continue
            
        params_df = pd.json_normalize(results_df['params'])
        results_df = pd.concat([results_df.drop(['params', 'trade_log'], axis=1), params_df], axis=1)
        
        sorted_results = results_df.sort_values(by=['profit_usdt'], ascending=[False])
        
        top_10_results = sorted_results.head(10)
        print(f"\nBeste Ergebnisse für {base_params['symbol']} (Top 10, Startkapital: ${capital:,.2f}):")
        
        for i, row in top_10_results.reset_index(drop=True).iterrows():
            print("\n" + "="*35)
            print(f"                 --- RANK {i + 1} ---")
            print("="*35)
            print(f"  Gewinn (USDT): ${row.get('profit_usdt', 0):,.2f}")
            print(f"  Gewinn (%):    {row.get('total_pnl_pct', 0):.2f}%")
            print(f"  Win Rate:      {row.get('win_rate', 0):.2f}%")
            print(f"  Anzahl Trades: {int(row.get('trades_count', 0))}")
            print(f"  Max Drawdown:  {row.get('max_drawdown_pct', 0):.2f}%")
            print("\n  PARAMETER:")
            print(f"    Timeframe: {row.get('timeframe', 'N/A')}")
            # Hier könnten weitere Ausgaben der besten Parameter hinzugefügt werden

# ... (der restliche Code ab 'if __name__ == "__main__":' bleibt unverändert)
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Strategy optimizer for the Supertrend Bot.")
    parser.add_argument('--capital', type=float, default=1000.0)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--timeframes', required=True)
    parser.add_argument('--symbols', nargs='+')
    parser.add_argument('--sl_multiplier', type=float)
    args = parser.parse_args()

    run_optimization(
        start_date=args.start,
        end_date=args.end,
        timeframes_str=args.timeframes,
        symbols_list=args.symbols,
        sl_multiplier=args.sl_multiplier,
        capital=args.capital
    )
