# master_runner.py
import json
import subprocess
import sys
import os
import time
from datetime import datetime, timedelta

# Pfad anpassen, damit die utils importiert werden können
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = SCRIPT_DIR
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from stbot.utils.exchange import Exchange


def check_and_run_optimizer():
    """
    Prüft ob die automatische Optimierung fällig ist und führt sie ggf. aus.
    
    Wird bei jedem Cron-Job Aufruf einmal geprüft. Die Logik ist tolerant gegenüber
    Cron-Intervallen: Wenn der geplante Zeitpunkt in der Vergangenheit liegt (aber
    noch am selben Tag in der geplanten Stunde), wird die Optimierung gestartet.
    
    Beispiel: Geplant 16:17, Cron läuft 16:15 → nicht fällig
              Geplant 16:17, Cron läuft 16:30 → fällig (16:17 ist vorbei)
    """
    now = datetime.now()
    
    try:
        settings_file = os.path.join(SCRIPT_DIR, 'settings.json')
        with open(settings_file, 'r') as f:
            settings = json.load(f)
        
        opt_settings = settings.get('optimization_settings', {})
        
        # Prüfe ob aktiviert
        if not opt_settings.get('enabled', False):
            return False
        
        schedule = opt_settings.get('schedule', {})
        day_of_week = schedule.get('day_of_week', 0)
        hour = schedule.get('hour', 3)
        minute = schedule.get('minute', 0)
        interval_days = schedule.get('interval_days', 7)
        
        # Prüfe ob heute der richtige Tag ist
        if now.weekday() != day_of_week:
            return False
        
        # Prüfe ob wir in der geplanten Stunde sind (oder danach, aber am gleichen Tag)
        if now.hour < hour:
            return False  # Noch nicht so weit
        
        # Wenn wir in der richtigen Stunde sind, prüfe ob die Minute erreicht wurde
        if now.hour == hour and now.minute < minute:
            return False  # Noch nicht so weit
        
        # Ab hier: Wir sind am richtigen Tag und der geplante Zeitpunkt ist erreicht oder überschritten
        
        # Prüfe ob heute schon gelaufen (oder innerhalb des Intervalls)
        cache_dir = os.path.join(SCRIPT_DIR, 'data', 'cache')
        cache_file = os.path.join(cache_dir, '.last_optimization_run')
        
        if os.path.exists(cache_file):
            with open(cache_file, 'r') as f:
                last_run = datetime.fromtimestamp(int(f.read().strip()))
                
                # Wenn heute schon gelaufen, nicht nochmal
                if last_run.date() == now.date():
                    return False
                
                # Wenn innerhalb des Intervalls, nicht nochmal
                if (now - last_run).days < interval_days:
                    return False
        
        # Zeit für Optimierung!
        print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] 🔄 Auto-Optimizer: Geplanter Zeitpunkt erreicht!")
        print(f"    Geplant war: {['Mo','Di','Mi','Do','Fr','Sa','So'][day_of_week]} {hour:02d}:{minute:02d}")
        print(f"    Starte Optimierung...")
        
        python_executable = os.path.join(SCRIPT_DIR, '.venv', 'bin', 'python3')
        optimizer_script = os.path.join(SCRIPT_DIR, 'auto_optimizer_scheduler.py')
        log_file = os.path.join(SCRIPT_DIR, 'logs', 'optimizer_output.log')
        
        if os.path.exists(optimizer_script):
            # Stelle sicher, dass logs/ Verzeichnis existiert
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            
            # Starte den Optimizer SYNCHRON (wartet auf Ende)
            # So wird die Telegram-Nachricht garantiert gesendet bevor wir weitermachen
            print(f"    Starte Optimizer im Hintergrund...")
            with open(log_file, 'a') as log:
                # Starte als Hintergrundprozess - Bots haben Priorität!
                subprocess.Popen(
                    [python_executable, optimizer_script, '--force'],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    cwd=SCRIPT_DIR,  # Wichtig: Arbeitsverzeichnis setzen!
                    start_new_session=True  # Läuft unabhängig weiter
                )
            return True
        else:
            print(f"    Fehler: {optimizer_script} nicht gefunden!")
            return False
        
    except Exception as e:
        print(f"Optimizer-Check Fehler: {e}")
        return False

def main():
    """
    Der Master Runner für den StBot.
    - Liest die settings.json, um den Modus (Autopilot/Manuell) zu bestimmen.
    - Startet für jede als "active" markierte Strategie einen separaten run.py Prozess
      innerhalb der korrekten virtuellen Umgebung.
    """
    settings_file = os.path.join(SCRIPT_DIR, 'settings.json')
    optimization_results_file = os.path.join(SCRIPT_DIR, 'artifacts', 'results', 'optimization_results.json')
    
    # Pfad zum Bot-Runner (angepasst auf stbot Struktur)
    bot_runner_script = os.path.join(SCRIPT_DIR, 'src', 'stbot', 'strategy', 'run.py')
    secret_file = os.path.join(SCRIPT_DIR, 'secret.json')

    # Finde den exakten Pfad zum Python-Interpreter in der virtuellen Umgebung
    python_executable = os.path.join(SCRIPT_DIR, '.venv', 'bin', 'python3')
    if not os.path.exists(python_executable):
        print(f"Fehler: Python-Interpreter in der venv nicht gefunden unter {python_executable}")
        return

    print("=======================================================")
    print("StBot Master Runner v1.0")
    print("=======================================================")

    try:
        with open(settings_file, 'r') as f:
            settings = json.load(f)

        with open(secret_file, 'r') as f:
            secrets = json.load(f)

        # KORREKTUR: Suche nach 'stbot' statt 'jaegerbot'
        if not secrets.get('stbot'): 
            # Fallback für Tests, falls noch utbot2 drin steht
            if secrets.get('utbot2'):
                print("Info: Nutze 'utbot2' Schlüssel als Fallback.")
                account_key = 'utbot2'
            else:
                print("Fehler: Kein 'stbot'-Account in secret.json gefunden.")
                return
        else:
            account_key = 'stbot'

        main_account_config = secrets[account_key][0]

        print(f"Frage Kontostand für Account '{main_account_config.get('name', 'Standard')}' ab...")

        live_settings = settings.get('live_trading_settings', {})
        opt_settings = settings.get('optimization_settings', {})
        use_autopilot = opt_settings.get('enabled', False)

        strategy_list = []
        if use_autopilot:
            print("Modus: Autopilot. Lese Strategien aus den Optimierungs-Ergebnissen...")
            if os.path.exists(optimization_results_file):
                with open(optimization_results_file, 'r') as f:
                    strategy_config = json.load(f)
                strategy_list = strategy_config.get('optimal_portfolio', [])
            else:
                print("Warnung: Keine Optimierungs-Ergebnisse gefunden. Bitte pipeline ausführen.")
        else:
            print("Modus: Manuell. Lese Strategien aus den manuellen Einstellungen...")
            strategy_list = live_settings.get('active_strategies', [])

        if not strategy_list:
            print("Keine aktiven Strategien zum Ausführen gefunden.")
            return

        print("=======================================================")

        for strategy_info in strategy_list:
            # 1. Fall: Strategie kommt aus manueller settings.json (Dict)
            if isinstance(strategy_info, dict):
                if not strategy_info.get("active", True):
                    continue
                
                symbol = strategy_info.get('symbol')
                timeframe = strategy_info.get('timeframe')
                # use_macd wird nur als Dummy übergeben für Kompatibilität
                use_macd = strategy_info.get('use_macd_filter', False)

            # 2. Fall: Strategie kommt aus optimization_results.json (String Dateiname)
            elif isinstance(strategy_info, str):
                # Format: config_BTCUSDTUSDT_1h.json
                # Wir müssen das parsen, um Symbol und Timeframe zu bekommen
                try:
                    # Entferne 'config_' und '.json'
                    clean_name = strategy_info.replace("config_", "").replace(".json", "")
                    # Teile am letzten Unterstrich (Trennung Symbol_Timeframe)
                    parts = clean_name.rsplit("_", 1)
                    if len(parts) != 2:
                        print(f"Warnung: Konnte Dateinamen nicht parsen: {strategy_info}")
                        continue
                    
                    symbol_raw = parts[0] # z.B. BTCUSDTUSDT
                    timeframe = parts[1]  # z.B. 1h
                    
                    # Versuche Symbol wiederherzustellen (etwas hacky, aber funktioniert meistens)
                    # Wir wissen, es endet auf USDTUSDT oder ähnlich.
                    # Besser: Wir laden die Config kurz, um sicherzugehen
                    config_path = os.path.join(SCRIPT_DIR, 'src', 'stbot', 'strategy', 'configs', strategy_info)
                    if os.path.exists(config_path):
                        with open(config_path, 'r') as cf:
                            c_data = json.load(cf)
                            symbol = c_data['market']['symbol']
                            # Timeframe ist in der Config
                            timeframe = c_data['market']['timeframe']
                    else:
                        print(f"Warnung: Config Datei fehlt: {config_path}")
                        continue
                    
                    use_macd = False # Bei optimierten Strategien irrelevant

                except Exception as e:
                    print(f"Fehler beim Parsen der Strategie {strategy_info}: {e}")
                    continue

            else:
                continue

            if not symbol or not timeframe:
                print(f"Warnung: Unvollständige Strategie-Info. Überspringe.")
                continue

            print(f"\n--- Starte Bot für: {symbol} ({timeframe}) ---")

            command = [
                python_executable,
                bot_runner_script,
                "--symbol", symbol,
                "--timeframe", timeframe,
                "--use_macd", str(use_macd)
            ]

            # Starte Prozess im Hintergrund, warte nicht auf Ende
            subprocess.Popen(command)
            time.sleep(2) # Kurze Pause, um API-Rate-Limits beim Start zu schonen

        print("\n=======================================================")
        print("Alle Bots gestartet. Master Runner beendet sich.")
        print("=======================================================\n")

    except FileNotFoundError as e:
        print(f"Fehler: Eine wichtige Datei wurde nicht gefunden: {e}")
    except KeyboardInterrupt:
        print("\nMaster Runner durch Benutzer beendet.")
    except Exception as e:
        print(f"Ein unerwarteter Fehler im Master Runner ist aufgetreten: {e}")

if __name__ == "__main__":
    # ZUERST: Normale Bot-Starts (Trades haben Priorität!)
    main()
    
    # DANACH: Auto-Optimizer Check (läuft im Hintergrund)
    check_and_run_optimizer()
