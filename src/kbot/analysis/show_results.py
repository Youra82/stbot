# /root/kbot/src/kbot/analysis/show_results.py
import os
import sys
import json
import pandas as pd
from datetime import date
import logging
import argparse

logging.getLogger('tensorflow').setLevel(logging.ERROR)
logging.getLogger('absl').setLevel(logging.ERROR)
import warnings
warnings.filterwarnings('ignore', category=UserWarning, module='keras')

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

# KORREKTUR: Import run_backtest statt run_smc_backtest
from kbot.analysis.backtester import load_data, run_backtest
from kbot.analysis.portfolio_simulator import run_portfolio_simulation
from kbot.analysis.portfolio_optimizer import run_portfolio_optimizer
from kbot.utils.telegram import send_document

# --- Einzel-Analyse ---
def run_single_analysis(start_date, end_date, start_capital):
    print("--- KBot Ergebnis-Analyse (Einzel-Modus) ---")
    configs_dir = os.path.join(PROJECT_ROOT, 'src', 'kbot', 'strategy', 'configs')
    all_results = []
    
    if not os.path.exists(configs_dir):
        print(f"Konfigurationsverzeichnis nicht gefunden: {configs_dir}")
        return

    config_files = sorted([f for f in os.listdir(configs_dir) if f.startswith('config_') and f.endswith('.json')])
    
    if not config_files:
        print("\nKeine gültigen Konfigurationen zum Analysieren gefunden."); return
    
    # --- NEU: Konfigurationsauswahl ---
    print("\n" + "="*60)
    print("Verfügbare Konfigurationen:")
    print("="*60)
    for i, filename in enumerate(config_files, 1):
        display_name = filename.replace('config_', '').replace('.json', '')
        print(f"{i:2}) {display_name}")
    print("="*60)
    
    print("\nWähle Konfiguration(en) zum Anzeigen:")
    print("  Einzeln: z.B. '1' oder '5'")
    print("  Mehrfach: z.B. '1,3,5' oder '1 3 5'")
    print("  Alle: 'alle'")
    
    selection = input("\nAuswahl: ").strip()
    
    selected_files = []
    try:
        if selection.lower() == 'alle':
            selected_files = config_files
        else:
            # Unterstütze sowohl Komma- als auch Leerzeichen-getrennte Eingaben
            indices = selection.replace(',', ' ').split()
            selected_files = [config_files[int(i) - 1] for i in indices]
    except (ValueError, IndexError):
        print("Ungültige Auswahl. Breche ab.")
        return
    
    if not selected_files:
        print("Keine Konfigurationen ausgewählt.")
        return
    # --- ENDE NEU ---
    
    print(f"\nZeitraum: {start_date} bis {end_date} | Startkapital: {start_capital} USDT")
    
    for filename in selected_files:
        config_path = os.path.join(configs_dir, filename)
        if not os.path.exists(config_path): continue
        try:
            with open(config_path, 'r') as f: config = json.load(f)
            symbol = config['market']['symbol']
            timeframe = config['market']['timeframe']
            strategy_name = f"{symbol} ({timeframe})"
            
            print(f"\nAnalysiere Ergebnisse für: {filename}...")
            data = load_data(symbol, timeframe, start_date, end_date)
            if data.empty:
                print(f"--> WARNUNG: Konnte keine Daten laden für {strategy_name}. Überspringe."); continue

            strategy_params = config.get('strategy', {})
            risk_params = config.get('risk', {})

            # Parameter für den Backtester vorbereiten
            strategy_params['symbol'] = symbol
            strategy_params['timeframe'] = timeframe
            strategy_params['htf'] = config['market'].get('htf')

            # KORREKTUR: Aufruf von run_backtest statt run_smc_backtest
            result = run_backtest(data.copy(), strategy_params, risk_params, start_capital, verbose=False)
            
            all_results.append({
                "Strategie": strategy_name,
                "Trades": result.get('trades_count', 0),
                "Win Rate %": result.get('win_rate', 0),
                "PnL %": result.get('total_pnl_pct', -100),
                "Max DD %": result.get('max_drawdown_pct', 1.0) * 100,
                "Endkapital": result.get('end_capital', start_capital)
            })
        except Exception as e:
            print(f"--> FEHLER bei der Analyse von {filename}: {e}")
            continue
            
    if not all_results:
        print("\nKeine gültigen Ergebnisse zum Anzeigen gefunden."); return
        
    results_df = pd.DataFrame(all_results)
    results_df = results_df.sort_values(by="PnL %", ascending=False)
    
    pd.set_option('display.width', 1000); pd.set_option('display.max_columns', None)
    print("\n\n=========================================================================================");
    print(f"                        Zusammenfassung aller Einzelstrategien");
    print("=========================================================================================")
    pd.set_option('display.float_format', '{:.2f}'.format);
    print(results_df.to_string(index=False));
    print("=========================================================================================")
