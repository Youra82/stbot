# code/analysis/optimizer.py
import os
import sys
import json
import pandas as pd
import argparse
import numpy as np
import time 
import multiprocessing # NEU: Import für prozesssichere Listen
from geneticalgorithm import geneticalgorithm as ga

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from utilities.strategy_logic import calculate_signals
from analysis.backtest import run_backtest, load_data_for_backtest, LOWER_TF_MAP

# Globale Variable, um statische Daten zu halten und nicht immer neu zu laden
BACKTEST_DATA = {}

def parse_range(range_str, var_type=float):
    """Wandelt einen String 'min-max' in eine Liste [min, max] um."""
    try:
        min_val, max_val = map(var_type, range_str.split('-'))
        return [min_val, max_val]
    except:
        raise argparse.ArgumentTypeError(f"Ungültiger Bereich: '{range_str}'")

def f(X):
    """
    Dies ist die Fitness-Funktion für den genetischen Algorithmus.
    Sie nimmt ein Numpy-Array 'X' mit den Parametern und gibt den Profit zurück.
    """
    params = BACKTEST_DATA['base_params'].copy()
    
    # Ordne die Werte aus dem Array X den benannten Parametern zu
    params['supertrend_einstellungen']['st_atr_period'] = int(X[0])
    params['supertrend_einstellungen']['st_atr_multiplier'] = float(X[1])
    params['supertrend_einstellungen']['sl_atr_multiplier'] = float(X[2])
    params['stop_loss_einstellungen']['donchian_period'] = int(X[3])
    params['hebel_einstellungen']['adx_strong_trend_threshold'] = int(X[4])
    params['hebel_einstellungen']['leverage_strong_trend'] = int(X[5])
    params['hebel_einstellungen']['leverage_weak_trend'] = int(X[6])
    params['stop_loss_einstellungen']['enable_donchian_channel_sl'] = bool(int(X[7]))
    params['hebel_einstellungen']['enable_dynamic_leverage'] = bool(int(X[8]))
    
    result = run_backtest(
        data_main_tf=BACKTEST_DATA['data_main'],
        data_lower_tf=BACKTEST_DATA['data_lower'],
        params=params,
        initial_capital=BACKTEST_DATA['capital'],
        verbose=False
    )
    
    min_trades = BACKTEST_DATA['min_trades']
    if result is None or result['trades_count'] < min_trades:
        return 99999.0 
    
    # Speichere jedes gültige Ergebnis in der geteilten Liste
    BACKTEST_DATA['shared_list'].append(result)
    
    profit = result['profit_usdt']
    drawdown = result['max_drawdown_pct']
    fitness = profit * (1 - (drawdown / 100))
    
    return -fitness

def run_optimization(args):
    global BACKTEST_DATA

    print("Lade Basiskonfiguration...")
    config_path = os.path.join(os.path.dirname(__file__), '..', 'strategies', 'envelope', 'config.json')
    with open(config_path, 'r') as config_file:
        default_params = json.load(config_file)

    # Erstelle eine prozesssichere Liste, um Ergebnisse zu sammeln
    manager = multiprocessing.Manager()
    shared_results_list = manager.list()

    symbols_to_optimize = args.symbols or [default_params['symbol']]
    
    for symbol_arg in symbols_to_optimize:
        base_params = default_params.copy()
        raw_symbol = symbol_arg
        if '/' not in raw_symbol: 
            base_params['symbol'] = f"{raw_symbol.upper()}/USDT:USDT"
        else: 
            base_params['symbol'] = raw_symbol.upper()
        
        print(f"\n\n#################### STARTE OPTIMIERUNG FÜR: {base_params['symbol']} ####################")
        
        shared_results_list[:] = [] # Leere die Liste für jeden neuen Symbol-Lauf
        
        timeframes_to_test = args.timeframes.split()
        
        for timeframe in timeframes_to_test:
            print(f"\n--- Bearbeite Timeframe: {timeframe} ---")
            
            data_main = load_data_for_backtest(base_params['symbol'], timeframe, args.start, args.end)
            if data_main is None or data_main.empty:
                print(f"Keine Daten für {timeframe}. Überspringe."); continue

            lower_timeframe = LOWER_TF_MAP.get(timeframe)
            data_lower = load_data_for_backtest(base_params['symbol'], lower_timeframe, args.start, args.end) if lower_timeframe else None

            BACKTEST_DATA = {
                'data_main': data_main.copy(),
                'data_lower': data_lower.copy() if data_lower is not None else None,
                'base_params': default_params.copy(),
                'capital': args.capital,
                'min_trades': args.min_trades,
                'shared_list': shared_results_list # Füge die Liste den geteilten Daten hinzu
            }
            BACKTEST_DATA['base_params']['timeframe'] = timeframe

            varbound = np.array([
                parse_range(args.st_atr_period, int),
                parse_range(args.st_atr_multiplier, float),
                parse_range(args.sl_atr_multiplier, float),
                parse_range(args.donchian_period, int),
                parse_range(args.hebel_adx_strong_trend_threshold, int),
                parse_range(args.hebel_leverage_strong_trend, int),
                parse_range(args.hebel_leverage_weak_trend, int),
                parse_range(args.donchian_sl_mode, int),
                parse_range(args.dyn_leverage_mode, int)
            ])
            
            vartype = np.array([
                ['int'], ['real'], ['real'], ['int'], ['int'], ['int'], ['int'], ['int'], ['int']
            ])

            print("Führe Benchmark für eine einzelne Berechnung durch...")
            benchmark_params = default_params.copy()
            benchmark_params['timeframe'] = timeframe
            benchmark_params['supertrend_einstellungen']['st_atr_period'] = np.random.randint(varbound[0][0], varbound[0][1] + 1)
            start_benchmark = time.time()
            run_backtest(data_main, data_lower, benchmark_params, args.capital, verbose=False)
            end_benchmark = time.time()
            duration_one_run = end_benchmark - start_benchmark
            total_evaluations = args.population_size * args.generation_count
            total_estimated_seconds = duration_one_run * total_evaluations
            est_minutes = int(total_estimated_seconds // 60)
            est_seconds = int(total_estimated_seconds % 60)
            print(f"Benchmark abgeschlossen. Geschätzte Gesamtdauer: {est_minutes} Minuten und {est_seconds} Sekunden.")

            algorithm_param = {
                'max_num_iteration': args.generation_count, 'population_size': args.population_size,
                'elit_ratio': 0.01, 'parents_portion': 0.3, 'crossover_probability': 0.5,
                'mutation_probability': 0.1, 'crossover_type':'uniform', 'max_iteration_without_improv': None
            }
            
            model = ga(function=f, dimension=len(varbound), variable_type_mixed=vartype,
                       variable_boundaries=varbound, algorithm_parameters=algorithm_param,
                       function_timeout=60) 

            print(f"Starte genetische Optimierung für {args.generation_count} Generationen mit einer Population von {args.population_size}...")
            print(f"Mindestanzahl an Trades für gültige Ergebnisse: {args.min_trades}")
            
            model.run()
        
        # Verarbeitung der gesammelten Ergebnisse am Ende des Symbol-Laufs
        all_results = list(shared_results_list)
        print(f"\n--- Optimierung für {base_params['symbol']} abgeschlossen ---")
        print(f"Insgesamt {len(all_results)} gültige Strategien gefunden.")

        if not all_results:
            print("Keine Strategie hat die Mindestanforderungen erfüllt.")
            continue

        results_df = pd.DataFrame(all_results)
        # Entferne Duplikate
        json_params = results_df['params'].apply(lambda p: json.dumps(p, sort_keys=True))
        results_df = results_df.loc[json_params.drop_duplicates().index]

        sorted_results = results_df.sort_values(by='profit_usdt', ascending=False)
        top_10_results = sorted_results.head(10)

        print(f"\nBeste Ergebnisse für {base_params['symbol']} (Top 10, Startkapital: ${args.capital:,.2f}):")

        for i, row in top_10_results.reset_index(drop=True).iterrows():
            params = row['params']
            print("\n" + "="*80)
            print(f"                                --- RANK {i + 1} ---")
            print("="*80)
            print(f"  Gesamtgewinn: ${row['profit_usdt']:>10,.2f}  ({row['total_pnl_pct']:.2f}%)")
            print(f"  Win Rate:     {row['win_rate']:>10.2f}%         | Trades: {row['trades_count']}")
            print(f"  Max Drawdown: {row['max_drawdown_pct']:>10.2f}%")
            print("-" * 80)
            print("  OPTIMALE PARAMETER:")
            print(f"  - Supertrend: ATR {params['supertrend_einstellungen']['st_atr_period']} / Multiplikator {params['supertrend_einstellungen']['st_atr_multiplier']:.2f}")
            print(f"  - Stop-Loss:  ATR Multiplikator {params['supertrend_einstellungen']['sl_atr_multiplier']:.2f}")

            if params['stop_loss_einstellungen']['enable_donchian_channel_sl']:
                print(f"  - Donchian SL: Aktiviert (Periode: {params['stop_loss_einstellungen']['donchian_period']})")
            else:
                print("  - Donchian SL: Deaktiviert")
            
            if params['hebel_einstellungen']['enable_dynamic_leverage']:
                print(f"  - Dyn. Hebel:  Aktiviert (ADX Schwelle: {params['hebel_einstellungen']['adx_strong_trend_threshold']})")
                print(f"    - Hebel (stark/schwach): {params['hebel_einstellungen']['leverage_strong_trend']}x / {params['hebel_einstellungen']['leverage_weak_trend']}x")
            else:
                print("  - Dyn. Hebel:  Deaktiviert")
            
            trade_log = row['trade_log']
            if trade_log:
                print("-" * 80)
                print("  TRADE-PROTOKOLL:")
                print(f"  {'Datum':<20} | {'Typ':<6} | {'Gewinn (USDT)':>15} | {'Kontostand (USDT)':>18}")
                print(f"  {'-'*20} | {'-'*6} | {'-'*15} | {'-'*18}")
                
                def print_trade_line(trade):
                    exit_time_str = trade['exit_time'].strftime('%Y-%m-%d %H:%M')
                    side_str = trade['side'].upper()
                    pnl_str = f"{trade['pnl_usdt']:>15,.2f}"
                    capital_str = f"{trade['capital_after']:>18,.2f}"
                    print(f"  {exit_time_str:<20} | {side_str:<6} | {pnl_str} | {capital_str}")

                num_trades = len(trade_log)
                max_log_trades = 20
                if num_trades > max_log_trades:
                    for trade in trade_log[:10]: print_trade_line(trade)
                    hidden_trades = num_trades - 20
                    print(f"  ... ({hidden_trades} weitere Trades werden nicht angezeigt) ...".center(80))
                    for trade in trade_log[-10:]: print_trade_line(trade)
                else:
                    for trade in trade_log: print_trade_line(trade)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Genetic Strategy Optimizer.")
    parser.add_argument('--capital', type=float, required=True)
    parser.add_argument('--start', type=str, required=True)
    parser.add_argument('--end', type=str, required=True)
    parser.add_argument('--timeframes', type=str, required=True)
    parser.add_argument('--symbols', type=str, nargs='+')
    parser.add_argument('--population_size', type=int, default=50)
    parser.add_argument('--generation_count', type=int, default=100)
    parser.add_argument('--min_trades', type=int, default=10)
    parser.add_argument('--st_atr_period', type=str, required=True)
    parser.add_argument('--st_atr_multiplier', type=str, required=True)
    parser.add_argument('--sl_atr_multiplier', type=str, required=True)
    parser.add_argument('--donchian_period', type=str, required=True)
    parser.add_argument('--hebel_adx_strong_trend_threshold', type=str, required=True)
    parser.add_argument('--hebel_leverage_strong_trend', type=str, required=True)
    parser.add_argument('--hebel_leverage_weak_trend', type=str, required=True)
    parser.add_argument('--donchian_sl_mode', type=str, required=True)
    parser.add_argument('--dyn_leverage_mode', type=str, required=True)
    args = parser.parse_args()
    
    run_optimization(args)
