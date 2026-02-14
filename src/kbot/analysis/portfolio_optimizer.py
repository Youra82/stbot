# src/kbot/analysis/portfolio_optimizer.py (Version für KBot StochRSI mit MaxDD Constraint & Coin-Kollisionsschutz)
import pandas as pd
import itertools
from tqdm import tqdm
import sys
import os
import json # Fürs Speichern
import numpy as np # Für np.nan

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from kbot.analysis.portfolio_simulator import run_portfolio_simulation

# *** Angepasst: Nimmt target_max_dd entgegen ***
def run_portfolio_optimizer(start_capital, strategies_data, start_date, end_date, target_max_dd: float):
    """
    Findet die Kombination von StochRSI-Strategien, die das höchste Endkapital liefert,
    während der maximale Drawdown unter dem Zielwert (`target_max_dd`) bleibt UND jeder Coin nur einmal vorkommt.
    Verwendet einen modifizierten Greedy-Algorithmus.
    """
    print(f"\n--- Starte automatische Portfolio-Optimierung (StochRSI) mit Max DD <= {target_max_dd:.2f}% & ohne Coin-Kollisionen ---")
    target_max_dd_decimal = target_max_dd / 100.0 # Umrechnung in Dezimalzahl für Vergleiche

    if not strategies_data:
        print("Keine Strategien zum Optimieren gefunden.")
        return None

    # --- 1. Analysiere Einzel-Performance & filtere nach Max DD ---
    print("1/3: Analysiere Einzel-Performance & filtere nach Max DD...")
    single_strategy_results = []

    for filename, strat_data in tqdm(strategies_data.items(), desc="Bewerte Einzelstrategien"):
        strategy_key = f"{strat_data['symbol']}_{strat_data['timeframe']}"
        sim_data = {strategy_key: strat_data}
        if 'data' not in strat_data or strat_data['data'].empty:
            print(f"WARNUNG: Keine Daten für {filename} in Einzelanalyse.")
            continue

        result = run_portfolio_simulation(start_capital, sim_data, start_date, end_date)

        if result and not result.get("liquidation_date"):
            actual_max_dd = result.get('max_drawdown_pct', 100.0) / 100.0
            if actual_max_dd <= target_max_dd_decimal:
                single_strategy_results.append({
                    'filename': filename,
                    'result': result
                })

    if not single_strategy_results:
        print(f"Keine einzige Strategie erfüllte die Bedingung Max DD <= {target_max_dd:.2f}%. Portfolio-Optimierung nicht möglich.")
        return {"optimal_portfolio": [], "final_result": None}

    # --- 2. Finde den "Star-Spieler" basierend auf HÖCHSTEM PROFIT unter den gefilterten ---
    single_strategy_results.sort(key=lambda x: x['result']['end_capital'], reverse=True)

    best_portfolio_files = [single_strategy_results[0]['filename']]
    best_portfolio_result = single_strategy_results[0]['result']
    best_end_capital = best_portfolio_result['end_capital']

    candidate_pool = [res['filename'] for res in single_strategy_results[1:]]

    print(f"2/3: Beste Einzelstrategie (unter Max DD): {best_portfolio_files[0]} (Endkapital: {best_end_capital:.2f} USDT, Max DD: {best_portfolio_result['max_drawdown_pct']:.2f}%)")
    print("3/3: Suche die besten Team-Kollegen...")

    selected_coins = set()
    if best_portfolio_files:
        initial_best_strat_data = strategies_data.get(best_portfolio_files[0])
        if initial_best_strat_data:
            initial_coin = initial_best_strat_data['symbol'].split('/')[0]
            selected_coins.add(initial_coin)

    while True:
        best_next_addition = None
        best_capital_with_addition = best_end_capital
        current_best_result_for_addition = best_portfolio_result

        for candidate_file in tqdm(candidate_pool, desc=f"Teste Team mit {len(best_portfolio_files)+1} Mitgliedern"):
            candidate_strat_data = strategies_data.get(candidate_file)
            if not candidate_strat_data:
                continue

            candidate_coin = candidate_strat_data['symbol'].split('/')[0]
            if candidate_coin in selected_coins:
                continue

            current_team_files = best_portfolio_files + [candidate_file]

            unique_check = set()
            is_valid_team = True
            for f in current_team_files:
                strat_info = strategies_data.get(f)
                if not strat_info: is_valid_team = False; break
                key = strat_info['symbol'] + strat_info['timeframe']
                if key in unique_check: is_valid_team = False; break
                unique_check.add(key)
            if not is_valid_team: continue

            current_team_data = {}
            valid_data_for_sim = True
            for fname in current_team_files:
                strat_d = strategies_data.get(fname)
                if strat_d and 'data' in strat_d and not strat_d['data'].empty:
                    sim_key = f"{strat_d['symbol']}_{strat_d['timeframe']}"
                    current_team_data[sim_key] = strat_d
                else:
                    valid_data_for_sim = False; break
            if not valid_data_for_sim: continue

            result = run_portfolio_simulation(start_capital, current_team_data, start_date, end_date)

            if result and not result.get("liquidation_date"):
                actual_max_dd = result.get('max_drawdown_pct', 100.0) / 100.0
                if actual_max_dd <= target_max_dd_decimal and result['end_capital'] > best_capital_with_addition:
                    best_capital_with_addition = result['end_capital']
                    best_next_addition = candidate_file
                    current_best_result_for_addition = result

        if best_next_addition:
            print(f"-> Füge hinzu: {best_next_addition} (Neues Kapital: {best_capital_with_addition:.2f} USDT, Max DD: {current_best_result_for_addition['max_drawdown_pct']:.2f}%)")
            best_portfolio_files.append(best_next_addition)

            added_strat_data = strategies_data.get(best_next_addition)
            if added_strat_data:
                added_coin = added_strat_data['symbol'].split('/')[0]
                selected_coins.add(added_coin)

            best_end_capital = best_capital_with_addition
            best_portfolio_result = current_best_result_for_addition
            candidate_pool.remove(best_next_addition)
        else:
            print("Keine weitere Verbesserung des Profits (unter Einhaltung des Max DD & ohne Coin-Kollision) durch Hinzufügen von Strategien gefunden. Optimierung beendet.")
            break

    try:
        results_dir = os.path.join(PROJECT_ROOT, 'artifacts', 'results')
        os.makedirs(results_dir, exist_ok=True)
        output_path = os.path.join(results_dir, 'optimization_results.json')
        save_data = {"optimal_portfolio": best_portfolio_files}
        with open(output_path, 'w') as f:
            json.dump(save_data, f, indent=4)
        print(f"Optimales Portfolio (Max DD <= {target_max_dd:.2f}%) in '{output_path}' gespeichert.")
    except Exception as e:
        print(f"Fehler beim Speichern der Optimierungsergebnisse: {e}")

    return {"optimal_portfolio": best_portfolio_files, "final_result": best_portfolio_result}