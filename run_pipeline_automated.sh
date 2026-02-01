#!/bin/bash

# --- Pfade und Skripte ---
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
VENV_PATH="$SCRIPT_DIR/.venv/bin/activate"
SETTINGS_FILE="$SCRIPT_DIR/settings.json"
SECRET_FILE="$SCRIPT_DIR/secret.json"
# *** Korrigierte Pfade, Trainer entfernt ***
OPTIMIZER="src/stbot/analysis/optimizer.py"
CACHE_DIR="$SCRIPT_DIR/data/cache"
TIMESTAMP_FILE="$CACHE_DIR/.last_cleaned"
LAST_RUN_FILE="$CACHE_DIR/.last_optimization_run"

# --- Umgebung aktivieren ---
# Sicherstellen, dass die venv existiert
if [ ! -f "$VENV_PATH" ]; then
    echo "Fehler: Virtuelle Umgebung nicht gefunden unter $VENV_PATH. Bitte install.sh ausführen."
    exit 1
fi
source "$VENV_PATH"

echo "--- Starte automatischen Pipeline-Lauf (StBot SRv2) ---"

# --- Prüfen ob settings.json existiert ---
if [ ! -f "$SETTINGS_FILE" ]; then
    echo "Fehler: settings.json nicht gefunden."
    deactivate
    exit 1
fi

# --- Python-Helper zum sicheren Auslesen der JSON-Datei ---
get_setting() {
    python3 -c "import json, sys; f=open('$SETTINGS_FILE'); settings=json.load(f); keys=$1; current=settings; path_ok=True
for k in keys: 
    current=current.get(k)
    if current is None: path_ok=False; break
print(current if path_ok else ''); f.close()" 2>/dev/null
}

# --- Telegram-Funktion ---
send_telegram() {
    local message="$1"
    if [ -f "$SECRET_FILE" ]; then
        python3 -c "
import json
import sys
sys.path.insert(0, '$SCRIPT_DIR/src')
from stbot.utils.telegram import send_message

with open('$SECRET_FILE') as f:
    secrets = json.load(f)
telegram = secrets.get('telegram', {})
bot_token = telegram.get('bot_token')
chat_id = telegram.get('chat_id')
if bot_token and chat_id:
    send_message(bot_token, chat_id, '''$message''')
" 2>/dev/null
    fi
}

# --- Standardwerte für Einstellungen definieren ---
DEFAULT_LOOKBACK=365
DEFAULT_START_CAPITAL=1000
DEFAULT_CORES=-1
DEFAULT_TRIALS=200
DEFAULT_MAX_DD=30
DEFAULT_MIN_WR=55
DEFAULT_MIN_PNL=0
DEFAULT_OPTIM_MODE="strict"
DEFAULT_CACHE_DAYS=0

# --- Automatisches Cache-Management ---
CACHE_DAYS=$(get_setting "['optimization_settings', 'auto_clear_cache_days']")
CACHE_DAYS=${CACHE_DAYS:-$DEFAULT_CACHE_DAYS}

if [[ "$CACHE_DAYS" =~ ^[0-9]+$ ]] && [ "$CACHE_DAYS" -gt 0 ]; then
    mkdir -p "$CACHE_DIR"
    if [ ! -f "$TIMESTAMP_FILE" ]; then touch "$TIMESTAMP_FILE"; fi
    if find "$TIMESTAMP_FILE" -mtime +$((CACHE_DAYS - 1)) -print -quit | grep -q .; then
        echo "Cache ist älter als $CACHE_DAYS Tage. Leere den Cache..."
        rm -rf "$CACHE_DIR"/*
        touch "$TIMESTAMP_FILE"
    else
        echo "Cache ist aktuell. Keine Reinigung notwendig."
    fi
else
    echo "Automatisches Cache-Management deaktiviert oder ungültiger Wert ($CACHE_DAYS)."
fi

# --- Lese Pipeline-Einstellungen ---
ENABLED=$(get_setting "['optimization_settings', 'enabled']")
ENABLED=${ENABLED:-False}

if [ "$ENABLED" != "True" ]; then
    echo "Automatische Optimierung ist in settings.json deaktiviert. Breche ab."
    deactivate
    exit 0
fi

# --- Schedule-Check (wenn nicht manuell gestartet) ---
if [ "$1" != "--force" ]; then
    SCHEDULE_DAY=$(get_setting "['optimization_settings', 'schedule', 'day_of_week']")
    SCHEDULE_HOUR=$(get_setting "['optimization_settings', 'schedule', 'hour']")
    SCHEDULE_MINUTE=$(get_setting "['optimization_settings', 'schedule', 'minute']")
    INTERVAL_DAYS=$(get_setting "['optimization_settings', 'schedule', 'interval_days']")
    
    SCHEDULE_DAY=${SCHEDULE_DAY:-0}
    SCHEDULE_HOUR=${SCHEDULE_HOUR:-3}
    SCHEDULE_MINUTE=${SCHEDULE_MINUTE:-0}
    INTERVAL_DAYS=${INTERVAL_DAYS:-7}
    
    CURRENT_DAY=$(date +%u)  # 1=Montag, 7=Sonntag (wir rechnen 0=Montag)
    CURRENT_HOUR=$(date +%H)
    CURRENT_MINUTE=$(date +%M)
    
    # Konvertiere zu unserem Format (0=Montag)
    CURRENT_DAY=$((CURRENT_DAY - 1))
    
    # Prüfe ob der richtige Tag und die richtige Stunde
    if [ "$CURRENT_DAY" != "$SCHEDULE_DAY" ] || [ "$CURRENT_HOUR" != "$SCHEDULE_HOUR" ]; then
        echo "Nicht der geplante Zeitpunkt (Geplant: Tag $SCHEDULE_DAY, $SCHEDULE_HOUR:$SCHEDULE_MINUTE Uhr)"
        echo "Aktuell: Tag $CURRENT_DAY, $CURRENT_HOUR:$CURRENT_MINUTE Uhr"
        echo "Nutze --force um die Optimierung sofort zu starten."
        deactivate
        exit 0
    fi
    
    # Prüfe Intervall (letzte Ausführung)
    mkdir -p "$CACHE_DIR"
    if [ -f "$LAST_RUN_FILE" ]; then
        LAST_RUN=$(cat "$LAST_RUN_FILE")
        DAYS_SINCE=$((( $(date +%s) - LAST_RUN ) / 86400))
        if [ "$DAYS_SINCE" -lt "$INTERVAL_DAYS" ]; then
            echo "Letzte Optimierung war vor $DAYS_SINCE Tagen (Intervall: $INTERVAL_DAYS Tage)"
            echo "Überspringe diese Ausführung."
            deactivate
            exit 0
        fi
    fi
fi

# Extrahiere Arrays sicher mit jq, falls verfügbar
if command -v jq &> /dev/null; then
    SYMBOLS=$(jq -r '.optimization_settings.symbols_to_optimize | join(" ") // ""' "$SETTINGS_FILE")
    TIMEFRAMES=$(jq -r '.optimization_settings.timeframes_to_optimize | join(" ") // ""' "$SETTINGS_FILE")
else
    echo "WARNUNG: jq nicht gefunden. Lese Arrays unsicher aus."
    SYMBOLS=$(get_setting "['optimization_settings', 'symbols_to_optimize']" | tr -d "[]',\"")
    TIMEFRAMES=$(get_setting "['optimization_settings', 'timeframes_to_optimize']" | tr -d "[]',\"")
fi
SYMBOLS=${SYMBOLS:-"BTC ETH"}
TIMEFRAMES=${TIMEFRAMES:-"1h 4h"}

LOOKBACK_DAYS=$(get_setting "['optimization_settings', 'lookback_days']")
LOOKBACK_DAYS=${LOOKBACK_DAYS:-$DEFAULT_LOOKBACK}
START_CAPITAL=$(get_setting "['optimization_settings', 'start_capital']")
START_CAPITAL=${START_CAPITAL:-$DEFAULT_START_CAPITAL}
N_CORES=$(get_setting "['optimization_settings', 'cpu_cores']")
N_CORES=${N_CORES:-$DEFAULT_CORES}
N_TRIALS=$(get_setting "['optimization_settings', 'num_trials']")
N_TRIALS=${N_TRIALS:-$DEFAULT_TRIALS}

MAX_DD=$(get_setting "['optimization_settings', 'constraints', 'max_drawdown_pct']")
MAX_DD=${MAX_DD:-$DEFAULT_MAX_DD}
MIN_WR=$(get_setting "['optimization_settings', 'constraints', 'min_win_rate_pct']")
MIN_WR=${MIN_WR:-$DEFAULT_MIN_WR}
MIN_PNL=$(get_setting "['optimization_settings', 'constraints', 'min_pnl_pct']")
MIN_PNL=${MIN_PNL:-$DEFAULT_MIN_PNL}

SEND_TELEGRAM=$(get_setting "['optimization_settings', 'send_telegram_on_completion']")
SEND_TELEGRAM=${SEND_TELEGRAM:-True}

START_DATE=$(date -d "$LOOKBACK_DAYS days ago" +%F)
END_DATE=$(date +%F)
OPTIM_MODE_ARG=${OPTIM_MODE_ARG:-$DEFAULT_OPTIM_MODE}

# --- Pipeline starten ---
echo "Optimierung ist aktiviert. Starte Prozesse..."
echo "Verwende Daten der letzten $LOOKBACK_DAYS Tage ($START_DATE bis $END_DATE)."
echo "Symbole: $SYMBOLS | Zeitfenster: $TIMEFRAMES"
echo "Trials: $N_TRIALS | Kerne: $N_CORES | Startkapital: $START_CAPITAL"

START_TIME=$(date +%s)

echo ">>> Starte Handelsparameter-Optimierung (SRv2)..."
python3 "$OPTIMIZER" \
    --symbols "$SYMBOLS" \
    --timeframes "$TIMEFRAMES" \
    --start_date "$START_DATE" \
    --end_date "$END_DATE" \
    --jobs "$N_CORES" \
    --max_drawdown "$MAX_DD" \
    --start_capital "$START_CAPITAL" \
    --min_win_rate "$MIN_WR" \
    --trials "$N_TRIALS" \
    --min_pnl "$MIN_PNL" \
    --mode "$OPTIM_MODE_ARG"

OPTIMIZER_EXIT_CODE=$?
END_TIME=$(date +%s)
DURATION=$(( (END_TIME - START_TIME) / 60 ))

# Speichere Zeitpunkt der Ausführung
mkdir -p "$CACHE_DIR"
date +%s > "$LAST_RUN_FILE"

if [ $OPTIMIZER_EXIT_CODE -ne 0 ]; then
    echo "Fehler im Optimierer-Skript. Pipeline wurde abgebrochen."
    if [ "$SEND_TELEGRAM" == "True" ]; then
        send_telegram "❌ StBot Auto-Optimierung FEHLGESCHLAGEN

Dauer: ${DURATION} Minuten
Symbole: $SYMBOLS
Zeitfenster: $TIMEFRAMES
Fehlercode: $OPTIMIZER_EXIT_CODE"
    fi
    deactivate
    exit 1
fi

# --- Erfolgs-Telegram senden ---
if [ "$SEND_TELEGRAM" == "True" ]; then
    # Zähle generierte Configs
    CONFIG_COUNT=$(ls -1 "$SCRIPT_DIR/src/stbot/strategy/configs/config_"*.json 2>/dev/null | wc -l)
    
    send_telegram "✅ StBot Auto-Optimierung ABGESCHLOSSEN

Dauer: ${DURATION} Minuten
Symbole: $SYMBOLS
Zeitfenster: $TIMEFRAMES
Generierte Configs: $CONFIG_COUNT
Trials pro Kombination: $N_TRIALS
Lookback: $LOOKBACK_DAYS Tage

Nächste Optimierung in $INTERVAL_DAYS Tagen."
fi

deactivate
echo "--- Automatischer Pipeline-Lauf abgeschlossen (${DURATION} Minuten) ---"
