import json
import os

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    settings_path = os.path.join(base_dir, 'settings.json')
    configs_dir = os.path.join(base_dir, 'src', 'stbot', 'strategy', 'configs')
    results_path = os.path.join(base_dir, 'artifacts', 'results', 'optimization_results.json')

    print(f"\n{'STRATEGIE':<35} | {'HEBEL':<5} | {'PFAD'}")
    print("-" * 60)

    try:
        with open(settings_path, 'r') as f:
            settings = json.load(f)
        
        live_settings = settings.get('live_trading_settings', {})
        opt_settings = settings.get('optimization_settings', {})
        use_auto = opt_settings.get('enabled', False)
        
        active_files = []

        if use_auto:
            # Autopilot Modus: Lese aus optimization_results.json
            print(f"(Modus: Autopilot)\n")
            if os.path.exists(results_path):
                with open(results_path, 'r') as f:
                    res = json.load(f)
                    active_files = res.get('optimal_portfolio', [])
            else:
                print("Fehler: optimization_results.json nicht gefunden.")
        else:
            # Manueller Modus: Lese aus settings.json
            print(f"(Modus: Manuell)\n")
            strats = live_settings.get('active_strategies', [])
            for s in strats:
                if isinstance(s, dict) and s.get('active'):
                    # Dateinamen rekonstruieren: config_SYMBOLTIMEFRAME.json
                    symbol_clean = s['symbol'].replace('/', '').replace(':', '')
                    tf = s['timeframe']
                    # Versuche verschiedene Namenskonventionen
                    candidates = [
                        f"config_{symbol_clean}_{tf}.json",
                        f"config_{symbol_clean}_{tf}_macd.json"
                    ]
                    found = False
                    for c in candidates:
                        if os.path.exists(os.path.join(configs_dir, c)):
                            active_files.append(c)
                            found = True
                            break
                    if not found:
                        print(f"WARNUNG: Config für {s['symbol']} {tf} nicht gefunden.")

        # Hebel auslesen
        for filename in active_files:
            full_path = os.path.join(configs_dir, filename)
            try:
                with open(full_path, 'r') as f:
                    config_data = json.load(f)
                    leverage = config_data.get('risk', {}).get('leverage', 'N/A')
                    # Strategie-Name schön formatieren
                    display_name = filename.replace('config_', '').replace('.json', '')
                    print(f"{display_name:<35} | {leverage:<5}x | {filename}")
            except Exception as e:
                print(f"Fehler bei {filename}: {e}")

    except Exception as e:
        print(f"Kritischer Fehler: {e}")
    
    print("-" * 60)

if __name__ == "__main__":
    main()
