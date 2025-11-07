# Pfad: /home/matola/stbot/master_runner.py
# master_runner.py
import json
import subprocess
import sys
import os
import time

# Pfad anpassen, damit die utils importiert werden können
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = SCRIPT_DIR
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

# *** ÄNDERUNG: Importpfad von titanbot zu stbot ***
from stbot.utils.exchange import Exchange

def main():
    """
    Der Master Runner für den STBot (Voll-Dynamisches Kapital).
    - Liest die settings.json, um den Modus (Autopilot/Manuell) zu bestimmen.
    - Startet für jede als "active" markierte Strategie einen separaten run.py Prozess
      innerhalb der korrekten virtuellen Umgebung.
    """
    settings_file = os.path.join(SCRIPT_DIR, 'settings.json')
    optimization_results_file = os.path.join(SCRIPT_DIR, 'artifacts', 'results', 'optimization_results.json')
    # *** ÄNDERUNG: Pfad zum Bot-Runner von titanbot zu stbot ***
    bot_runner_script = os.path.join(SCRIPT_DIR, 'src', 'stbot', 'strategy', 'run.py')
    secret_file = os.path.join(SCRIPT_DIR, 'secret.json')

    # Finde den exakten Pfad zum Python-Interpreter in der virtuellen Umgebung
    python_executable = os.path.join(SCRIPT_DIR, '.venv', 'bin', 'python3')
    if not os.path.exists(python_executable):
        print(f"Fehler: Python-Interpreter in der venv nicht gefunden unter {python_executable}")
        return

    print("=======================================================")
    # *** ÄNDERUNG: Name ***
    print("STBot Master Runner v1.0")
    print("=======================================================")

    try:
        with open(settings_file, 'r') as f:
            settings = json.load(f)

        with open(secret_file, 'r') as f:
            secrets = json.load(f)

        # Account-Name (optional)
        if not secrets.get('jaegerbot'): # Behalte den Secret-Namen bei
            print("Fehler: Kein 'jaegerbot'-Account in secret.json gefunden.")
            return
        main_account_config = secrets['jaegerbot'][0]

        print(f"Frage Kontostand für Account '{main_account_config.get('name', 'Standard')}' ab...")

        live_settings = settings.get('live_trading_settings', {})
        use_autopilot = live_settings.get('use_auto_optimizer_results', False)

        strategy_list = []
        if use_autopilot:
            print("Modus: Autopilot. Lese Strategien aus den Optimierungs-Ergebnissen...")
            with open(optimization_results_file, 'r') as f:
                strategy_config = json.load(f)
            strategy_list = strategy_config.get('optimal_portfolio', [])
        else:
            print("Modus: Manuell. Lese Strategien aus den manuellen Einstellungen...")
            strategy_list = live_settings.get('active_strategies', [])

        if not strategy_list:
            print("Keine aktiven Strategien zum Ausführen gefunden.")
            return

        print("=======================================================")

        for strategy_info in strategy_list:
            if isinstance(strategy_info, dict) and not strategy_info.get("active", True):
                symbol = strategy_info.get('symbol', 'N/A')
                timeframe = strategy_info.get('timeframe', 'N/A')
                print(f"\n--- Überspringe inaktive Strategie: {symbol} ({timeframe}) ---")
                continue

            symbol, timeframe, use_macd = None, None, None

            if use_autopilot and isinstance(strategy_info, str):
                # Wenn Autopilot die Config-Dateinamen liefert (z.B. config_BTCUSDTUSDT_1h.json)
                config_filename = strategy_info
                if config_filename.endswith('_macd.json'):
                    use_macd = True
                else:
                    use_macd = False

                # Extrahiere Symbol und Timeframe aus dem Dateinamen
                parts = config_filename.split('_')
                if len(parts) >= 3:
                    symbol_tf = parts[1]
                    timeframe = parts[2].split('.')[0]
                    # Füge den :USDT Suffix wieder hinzu, um CCXT-kompatibel zu sein
                    symbol = f"{symbol_tf.replace('USDT', '/')}:USDT"

                    # Behebe den Fall, dass das Symbol XRPUSDTUSDT ist
                    if symbol.startswith('XRP/USDT:USDT'): symbol = 'XRP/USDT:USDT'

            elif isinstance(strategy_info, dict):
                symbol = strategy_info.get('symbol')
                timeframe = strategy_info.get('timeframe')
                # use_macd wird als Dummy-Wert übergeben
                use_macd = strategy_info.get('use_macd_filter', False)

            if not all([symbol, timeframe, use_macd is not None]):
                print(f"Warnung: Unvollständige Strategie-Info: {strategy_info}. Überspringe.")
                continue

            print(f"\n--- Starte Bot für: {symbol} ({timeframe}) ---")

            command = [
                python_executable,
                bot_runner_script,
                "--symbol", symbol,
                "--timeframe", timeframe,
                # Wir übergeben 'use_macd' als Dummy-Argument, da 'run.py' es erwartet
                "--use_macd", str(use_macd)
            ]

            subprocess.Popen(command)
            time.sleep(2)

    except FileNotFoundError as e:
        print(f"Fehler: Eine wichtige Datei wurde nicht gefunden: {e}")
    except Exception as e:
        print(f"Ein unerwarteter Fehler im Master Runner ist aufgetreten: {e}")

if __name__ == "__main__":
    main()
