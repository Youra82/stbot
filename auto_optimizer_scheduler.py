#!/usr/bin/env python3
"""
StBot Auto-Optimizer Scheduler

Dieses Skript läuft als Hintergrund-Service und startet die Optimierung
automatisch gemäß dem in settings.json definierten Zeitplan.

Verwendung:
    python auto_optimizer_scheduler.py                # Normal starten
    python auto_optimizer_scheduler.py --daemon       # Als Daemon im Hintergrund
    python auto_optimizer_scheduler.py --check-only   # Nur prüfen ob Optimierung fällig ist
    python auto_optimizer_scheduler.py --force        # Sofort optimieren (ignoriert Zeitplan)
"""

import os
import sys
import json
import time
import argparse
import subprocess
from datetime import datetime, timedelta
from pathlib import Path


# Pfade
SCRIPT_DIR = Path(__file__).parent.absolute()
SETTINGS_FILE = SCRIPT_DIR / "settings.json"
SECRET_FILE = SCRIPT_DIR / "secret.json"
CACHE_DIR = SCRIPT_DIR / "data" / "cache"
LAST_RUN_FILE = CACHE_DIR / ".last_optimization_run"
LOG_FILE = SCRIPT_DIR / "logs" / "scheduler.log"

# Stellt sicher, dass der src-Ordner im Pfad ist
sys.path.insert(0, str(SCRIPT_DIR / "src"))


def log(message: str, also_print: bool = True):
    """Loggt eine Nachricht in die Logdatei und optional auf die Konsole."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}"
    
    if also_print:
        print(log_entry)
    
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_entry + "\n")
    except Exception as e:
        print(f"Warnung: Konnte nicht in Logdatei schreiben: {e}")


def load_settings() -> dict:
    """Lädt die settings.json Datei."""
    if not SETTINGS_FILE.exists():
        log(f"Fehler: {SETTINGS_FILE} nicht gefunden!")
        return {}
    
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_secrets() -> dict:
    """Lädt die secret.json Datei."""
    if not SECRET_FILE.exists():
        return {}
    
    with open(SECRET_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_symbols_timeframes(settings: dict, extract_type: str) -> list:
    """
    Extrahiert Symbole oder Timeframes aus active_strategies wenn 'auto' gesetzt ist.
    
    Args:
        settings: Die geladenen Settings
        extract_type: "symbols" oder "timeframes"
    
    Returns:
        Liste der Symbole oder Timeframes
    """
    opt_settings = settings.get("optimization_settings", {})
    live_settings = settings.get("live_trading_settings", {})
    strategies = live_settings.get("active_strategies", [])
    
    if extract_type == "symbols":
        setting_value = opt_settings.get("symbols_to_optimize", "auto")
        if setting_value == "auto" or not setting_value:
            # Extrahiere aus active_strategies
            symbols = set()
            for s in strategies:
                if s.get("active", False):
                    sym = s.get("symbol", "").split("/")[0]
                    if sym:
                        symbols.add(sym)
            return sorted(symbols) if symbols else ["BTC", "ETH"]
        return setting_value if isinstance(setting_value, list) else ["BTC", "ETH"]
    
    elif extract_type == "timeframes":
        setting_value = opt_settings.get("timeframes_to_optimize", "auto")
        if setting_value == "auto" or not setting_value:
            # Extrahiere aus active_strategies
            timeframes = set()
            for s in strategies:
                if s.get("active", False):
                    tf = s.get("timeframe", "")
                    if tf:
                        timeframes.add(tf)
            # Sortiere nach Dauer
            tf_order = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, 
                       "2h": 120, "4h": 240, "6h": 360, "12h": 720, "1d": 1440}
            sorted_tf = sorted(timeframes, key=lambda x: tf_order.get(x, 999))
            return sorted_tf if sorted_tf else ["1h", "4h"]
        return setting_value if isinstance(setting_value, list) else ["1h", "4h"]
    
    return []


def send_telegram(message: str) -> bool:
    """Sendet eine Telegram-Nachricht."""
    try:
        from stbot.utils.telegram import send_message
        
        secrets = load_secrets()
        telegram = secrets.get("telegram", {})
        bot_token = telegram.get("bot_token")
        chat_id = telegram.get("chat_id")
        
        if bot_token and chat_id:
            send_message(bot_token, chat_id, message)
            return True
    except Exception as e:
        log(f"Telegram-Fehler: {e}")
    return False


def get_last_run_time() -> datetime | None:
    """Gibt den Zeitpunkt der letzten Optimierung zurück."""
    if not LAST_RUN_FILE.exists():
        return None
    
    try:
        with open(LAST_RUN_FILE, "r") as f:
            timestamp = int(f.read().strip())
            return datetime.fromtimestamp(timestamp)
    except:
        return None


def save_last_run_time():
    """Speichert den aktuellen Zeitpunkt als letzte Ausführung."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(LAST_RUN_FILE, "w") as f:
        f.write(str(int(time.time())))


def should_run_now(settings: dict, force: bool = False) -> tuple[bool, str]:
    """
    Prüft ob die Optimierung jetzt ausgeführt werden soll.
    
    Returns:
        tuple: (should_run: bool, reason: str)
    """
    opt_settings = settings.get("optimization_settings", {})
    
    # Prüfe ob aktiviert
    if not opt_settings.get("enabled", False):
        return False, "Automatische Optimierung ist deaktiviert"
    
    if force:
        return True, "Erzwungene Ausführung (--force)"
    
    schedule = opt_settings.get("schedule", {})
    day_of_week = schedule.get("day_of_week", 0)  # 0 = Montag
    hour = schedule.get("hour", 3)
    minute = schedule.get("minute", 0)
    interval_days = schedule.get("interval_days", 7)
    
    now = datetime.now()
    current_day = now.weekday()  # 0 = Montag
    current_hour = now.hour
    current_minute = now.minute
    
    # Prüfe Tag und Stunde
    if current_day != day_of_week:
        return False, f"Falscher Tag (heute: {current_day}, geplant: {day_of_week})"
    
    if current_hour != hour:
        return False, f"Falsche Stunde (jetzt: {current_hour}:xx, geplant: {hour}:{minute:02d})"
    
    # Prüfe Minute (mit 5 Minuten Toleranz)
    if abs(current_minute - minute) > 5:
        return False, f"Falsche Minute (jetzt: xx:{current_minute:02d}, geplant: xx:{minute:02d})"
    
    # Prüfe Intervall
    last_run = get_last_run_time()
    if last_run:
        days_since = (now - last_run).days
        if days_since < interval_days:
            return False, f"Intervall nicht erreicht ({days_since} von {interval_days} Tagen)"
    
    return True, "Geplanter Zeitpunkt erreicht"


def run_optimization() -> bool:
    """Führt die Optimierung aus."""
    log("Starte Optimierung...")
    
    # Unter Windows nutzen wir Python direkt statt Bash
    if sys.platform == "win32":
        return run_optimization_windows()
    else:
        return run_optimization_unix()


def run_optimization_unix() -> bool:
    """Führt die Optimierung unter Unix aus."""
    script_path = SCRIPT_DIR / "run_pipeline_automated.sh"
    
    if not script_path.exists():
        log(f"Fehler: {script_path} nicht gefunden!")
        return False
    
    try:
        result = subprocess.run(
            ["bash", str(script_path), "--force"],
            cwd=str(SCRIPT_DIR),
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            log("Optimierung erfolgreich abgeschlossen")
            return True
        else:
            log(f"Optimierung fehlgeschlagen (Exit-Code: {result.returncode})")
            log(f"Stderr: {result.stderr[:500] if result.stderr else 'N/A'}")
            return False
    except Exception as e:
        log(f"Fehler bei der Ausführung: {e}")
        return False


def run_optimization_windows() -> bool:
    """Führt die Optimierung unter Windows aus (direkt Python)."""
    settings = load_settings()
    opt_settings = settings.get("optimization_settings", {})
    
    symbols = extract_symbols_timeframes(settings, "symbols")
    timeframes = extract_symbols_timeframes(settings, "timeframes")
    lookback_days = opt_settings.get("lookback_days", 365)
    start_capital = opt_settings.get("start_capital", 1000)
    n_cores = opt_settings.get("cpu_cores", -1)
    n_trials = opt_settings.get("num_trials", 200)
    
    constraints = opt_settings.get("constraints", {})
    max_dd = constraints.get("max_drawdown_pct", 30)
    min_wr = constraints.get("min_win_rate_pct", 55)
    min_pnl = constraints.get("min_pnl_pct", 0)
    
    start_time = time.time()
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    
    optimizer_path = SCRIPT_DIR / "src" / "stbot" / "analysis" / "optimizer.py"
    
    if not optimizer_path.exists():
        log(f"Fehler: {optimizer_path} nicht gefunden!")
        return False
    
    cmd = [
        sys.executable, str(optimizer_path),
        "--symbols", " ".join(symbols),
        "--timeframes", " ".join(timeframes),
        "--start_date", start_date,
        "--end_date", end_date,
        "--jobs", str(n_cores),
        "--max_drawdown", str(max_dd),
        "--start_capital", str(start_capital),
        "--min_win_rate", str(min_wr),
        "--trials", str(n_trials),
        "--min_pnl", str(min_pnl),
        "--mode", "strict"
    ]
    
    log(f"Führe aus: {' '.join(cmd[:5])}...")
    log(f"")
    log(f"╔══════════════════════════════════════════════════════════════╗")
    log(f"║  AUTO-OPTIMIZER: {len(symbols)} Symbole × {len(timeframes)} Timeframes = {len(symbols) * len(timeframes)} Kombinationen")
    log(f"║  Symbole: {', '.join(symbols)}")
    log(f"║  Timeframes: {', '.join(timeframes)}")
    log(f"║  Trials pro Kombination: {n_trials}")
    log(f"║  Lookback: {lookback_days} Tage ({start_date} bis {end_date})")
    log(f"╚══════════════════════════════════════════════════════════════╝")
    log(f"")
    
    try:
        # Starte Prozess mit Live-Output statt capture_output
        process = subprocess.Popen(
            cmd,
            cwd=str(SCRIPT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        # Zeige Output in Echtzeit
        output_lines = []
        for line in process.stdout:
            line = line.rstrip()
            print(line)  # Live-Ausgabe
            output_lines.append(line)
        
        process.wait()
        returncode = process.returncode
        
        duration = int((time.time() - start_time) / 60)
        save_last_run_time()
        
        if returncode == 0:
            log(f"")
            log(f"╔══════════════════════════════════════════════════════════════╗")
            log(f"║  ✅ OPTIMIERUNG ERFOLGREICH ABGESCHLOSSEN")
            log(f"║  Dauer: {duration} Minuten")
            log(f"╚══════════════════════════════════════════════════════════════╝")
            
            # Telegram senden
            if opt_settings.get("send_telegram_on_completion", True):
                config_dir = SCRIPT_DIR / "src" / "stbot" / "strategy" / "configs"
                config_count = len(list(config_dir.glob("config_*.json"))) if config_dir.exists() else 0
                
                send_telegram(f"""✅ StBot Auto-Optimierung ABGESCHLOSSEN

Dauer: {duration} Minuten
Symbole: {', '.join(symbols)}
Zeitfenster: {', '.join(timeframes)}
Generierte Configs: {config_count}
Trials pro Kombination: {n_trials}
Lookback: {lookback_days} Tage""")
            
            return True
        else:
            log(f"")
            log(f"╔══════════════════════════════════════════════════════════════╗")
            log(f"║  ❌ OPTIMIERUNG FEHLGESCHLAGEN (Exit-Code: {returncode})")
            log(f"╚══════════════════════════════════════════════════════════════╝")
            
            if opt_settings.get("send_telegram_on_completion", True):
                send_telegram(f"""❌ StBot Auto-Optimierung FEHLGESCHLAGEN

Dauer: {duration} Minuten
Fehlercode: {returncode}
Details in logs/scheduler.log""")
            
            return False
    except Exception as e:
        log(f"Fehler bei der Ausführung: {e}")
        return False


def run_daemon(check_interval: int = 60):
    """Läuft als Daemon und prüft regelmäßig ob Optimierung fällig ist."""
    log("Starte Scheduler-Daemon...")
    log(f"Prüfe alle {check_interval} Sekunden")
    
    settings = load_settings()
    opt_settings = settings.get("optimization_settings", {})
    schedule = opt_settings.get("schedule", {})
    
    log(f"Geplant: Tag {schedule.get('day_of_week', 0)} (0=Mo), "
        f"{schedule.get('hour', 3)}:{schedule.get('minute', 0):02d} Uhr, "
        f"alle {schedule.get('interval_days', 7)} Tage")
    
    while True:
        try:
            settings = load_settings()
            should_run, reason = should_run_now(settings)
            
            if should_run:
                log(f"Optimierung wird gestartet: {reason}")
                run_optimization()
            
            time.sleep(check_interval)
        except KeyboardInterrupt:
            log("Scheduler durch Benutzer beendet")
            break
        except Exception as e:
            log(f"Fehler im Daemon: {e}")
            time.sleep(check_interval)


def main():
    parser = argparse.ArgumentParser(description="StBot Auto-Optimizer Scheduler")
    parser.add_argument("--daemon", action="store_true", 
                       help="Als Hintergrund-Service laufen")
    parser.add_argument("--check-only", action="store_true",
                       help="Nur prüfen ob Optimierung fällig ist")
    parser.add_argument("--force", action="store_true",
                       help="Optimierung sofort starten (ignoriert Zeitplan)")
    parser.add_argument("--interval", type=int, default=60,
                       help="Prüf-Intervall in Sekunden (nur für --daemon)")
    
    args = parser.parse_args()
    
    settings = load_settings()
    
    if args.check_only:
        should_run, reason = should_run_now(settings)
        log(f"Optimierung fällig: {should_run}")
        log(f"Grund: {reason}")
        
        last_run = get_last_run_time()
        if last_run:
            log(f"Letzte Ausführung: {last_run.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            log("Noch keine Optimierung ausgeführt")
        
        sys.exit(0 if should_run else 1)
    
    if args.force:
        log("Erzwinge sofortige Optimierung...")
        success = run_optimization()
        sys.exit(0 if success else 1)
    
    if args.daemon:
        run_daemon(args.interval)
    else:
        # Einmaliger Check und ggf. Ausführung
        should_run, reason = should_run_now(settings)
        log(f"Status: {reason}")
        
        if should_run:
            run_optimization()
        else:
            log("Optimierung nicht fällig. Nutze --force für sofortige Ausführung "
                "oder --daemon für kontinuierlichen Betrieb.")


if __name__ == "__main__":
    main()
