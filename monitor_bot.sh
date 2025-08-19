#!/bin/bash

# Pfade zu wichtigen Dateien und Verzeichnissen
CONFIG_FILE="/home/ubuntu/utbot2/code/strategies/envelope/config.json"
LOG_FILE="/home/ubuntu/utbot2/logs/envelope.log"
PYTHON_VENV="/home/ubuntu/utbot2/code/.venv/bin/python3"
BACKTEST_SCRIPT="/home/ubuntu/utbot2/code/analysis/backtest.py"
OPTIMIZER_SCRIPT="/home/ubuntu/utbot2/code/analysis/optimizer.py"
CACHE_DIR="/home/ubuntu/utbot2/code/analysis/historical_data"

# --- Farbcodes für eine schönere Ausgabe ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# --- Funktion für interaktive Analyse-Modi ---
function run_analysis() {
    local script_path=$1
    local mode_name=$2
    local script_args=""

    echo -e "${CYAN}=======================================================${NC}"
    echo -e "${CYAN}               ENVELOPE BOT - $mode_name MODUS               ${NC}"
    echo -e "${CYAN}=======================================================${NC}"

    read -p "Bitte geben Sie den Zeitraum ein (z.B. 2024-01-01 to 2024-06-30): " date_range_input
    START_DATE=$(echo $date_range_input | awk '{print $1}')
    END_DATE=$(echo $date_range_input | awk '{print $3}')

    if [ -z "$START_DATE" ] || [ -z "$END_DATE" ]; then
        echo -e "${RED}Fehler: Ungültiges Datum.${NC}"
        exit 1
    fi
    script_args="--start $START_DATE --end $END_DATE"

    if [ "$mode_name" == "BACKTEST" ]; then
        read -p "Bitte geben Sie den Timeframe ein (z.B. 1h): " TIMEFRAME
        script_args="$script_args --timeframe $TIMEFRAME"
        
        read -p "Handelspaar(e) eingeben (optional, z.B. BTC ETH): " SYMBOLS
        if [ -n "$SYMBOLS" ]; then
            script_args="$script_args --symbols $SYMBOLS"
        fi

    elif [ "$mode_name" == "OPTIMIZER" ]; then
        read -p "Geben Sie die Timeframes getrennt durch Leerzeichen ein (z.B. 15m 1h 4h): " TIMEFRAMES
        script_args="$script_args --timeframes \"$TIMEFRAMES\""

        read -p "Handelspaar(e) eingeben (optional, z.B. BTC ETH): " SYMBOLS
        if [ -n "$SYMBOLS" ]; then
            script_args="$script_args --symbols $SYMBOLS"
        fi
    fi

    # NEUE ABFRAGEN FÜR HEBEL UND SL
    read -p "Hebel eingeben (optional, Enter für Standard): " LEVERAGE
    if [ -n "$LEVERAGE" ]; then
        script_args="$script_args --leverage $LEVERAGE"
    fi
    read -p "SL-Multiplikator eingeben (optional, Enter für Standard): " SL_MULTIPLIER
    if [ -n "$SL_MULTIPLIER" ]; then
        script_args="$script_args --sl_multiplier $SL_MULTIPLIER"
    fi

    echo -e "${YELLOW}Starte $mode_name für $START_DATE bis $END_DATE...${NC}"
    eval "$PYTHON_VENV $script_path $script_args"
    exit 0
}


# --- MODUS-AUSWAHL ---
case "$1" in
    backtest)
        run_analysis $BACKTEST_SCRIPT "BACKTEST"
        ;;
    optimize)
        run_analysis $OPTIMIZER_SCRIPT "OPTIMIZER"
        ;;
    clear-cache)
        echo -e "${YELLOW}Möchtest du den gesamten Daten-Cache löschen? (${CYAN}$CACHE_DIR${YELLOW})${NC}"
        read -p "Bestätige mit [j/N]: " response
        if [[ "$response" =~ ^([jJ][aA]|[jJ])$ ]]; then
            rm -rf "$CACHE_DIR" && echo -e "${GREEN}✔ Cache wurde erfolgreich gelöscht.${NC}"
        else
            echo -e "${RED}Aktion abgebrochen.${NC}"
        fi
        exit 0
        ;;
esac

# --- STANDARD-MONITORING-ANZEIGE ---
echo -e "${CYAN}=======================================================${NC}"
echo -e "${CYAN}               ENVELOPE TRADING BOT MONITORING               ${NC}"
echo -e "${CYAN}=======================================================${NC}"
echo "Verwende './monitor_bot.sh <mode>', Modi: ${GREEN}backtest, optimize, clear-cache${NC}"
echo -e "Letzte Aktualisierung: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# --- Konfiguration & Strategie ---
echo -e "${YELLOW}--- KONFIGURATION & STRATEGIE ---${NC}"
if command -v jq &> /dev/null; then
    SYMBOL=$(jq -r '.symbol' $CONFIG_FILE)
    TIMEFRAME=$(jq -r '.timeframe' $CONFIG_FILE)
    LEVERAGE=$(jq -r '.leverage' $CONFIG_FILE)
    echo "Symbol: $SYMBOL, Timeframe: $TIMEFRAME, Hebel: ${LEVERAGE}x"
    
    ATR_PERIOD=$(jq -r '.ut_atr_period' $CONFIG_FILE)
    KEY_VALUE=$(jq -r '.ut_key_value' $CONFIG_FILE)
    echo "UT Bot: ATR Periode $ATR_PERIOD / Key Value $KEY_VALUE"

    if [[ $(jq -r '.use_adx_filter' $CONFIG_FILE) == "true" ]]; then
        ADX_WIN=$(jq -r '.adx_window' $CONFIG_FILE)
        ADX_THRES=$(jq -r '.adx_threshold' $CONFIG_FILE)
        echo -e "ADX Filter: ${GREEN}Aktiv${NC} (Window: $ADX_WIN, Threshold: $ADX_THRES)"
    else
        echo -e "ADX Filter: ${RED}Inaktiv${NC}"
    fi
else
    echo -e "${RED}Fehler: 'jq' ist nicht installiert. Bitte mit 'sudo apt-get install jq' installieren.${NC}"
fi
echo ""

# --- Bot-Statistiken aus dem Log ---
echo -e "${YELLOW}--- BOT-STATISTIKEN (seit Log-Start) ---${NC}"
if [ -f "$LOG_FILE" ]; then
    TRADES_OPENED=$(grep -c "Position eröffnet" "$LOG_FILE")
    TRADES_CLOSED=$(grep -c "Position geschlossen" "$LOG_FILE")
    echo "Eröffnete Trades: ${GREEN}$TRADES_OPENED${NC}, Geschlossene Trades: ${GREEN}$TRADES_CLOSED${NC}"
else
    echo "Log-Datei nicht gefunden."
fi
echo ""

# --- Aktuelle Position & Risiko ---
echo -e "${YELLOW}--- AKTUELLE POSITION & RISIKO ---${NC}"
if [ -f "$LOG_FILE" ]; then
    LAST_OPEN_LINE=$(grep "Position bei" "$LOG_FILE" | tail -n 1)
    LAST_CLOSE_LINE=$(grep "Position geschlossen" "$LOG_FILE" | tail -n 1)

    if [ -n "$LAST_OPEN_LINE" ] && [ "$(echo -e "$LAST_OPEN_LINE\n$LAST_CLOSE_LINE" | sort | tail -n 1)" == "$LAST_OPEN_LINE" ]; then
        POSITION_INFO=$(echo "$LAST_OPEN_LINE" | sed 's/.*UTC: //')
        ENTRY_SIDE=$(echo "$POSITION_INFO" | awk '{print $1}')
        ENTRY_PRICE=$(echo "$POSITION_INFO" | grep -oP '@ \K[0-9.]+')
        STOP_LOSS_PRICE=$(echo "$POSITION_INFO" | grep -oP 'Stop-Loss bei \K[0-9.]+')
        
        echo -e "Status: ${GREEN}Position offen${NC}"
        echo -e "Seite: ${GREEN}${ENTRY_SIDE}${NC}, Einstieg: ${GREEN}${ENTRY_PRICE}${NC}, SL: ${RED}${STOP_LOSS_PRICE:-N/A}${NC}"
    else
        echo -e "Status: ${CYAN}Keine Position offen${NC}"
    fi
else
    echo "Log-Datei nicht gefunden."
fi
echo ""

# --- System-Status ---
echo -e "${YELLOW}--- SYSTEM-STATUS ---${NC}"
if [ -f "$LOG_FILE" ]; then
    if [ -s "$LOG_FILE" ]; then
        LAST_LOG_SECONDS=$(date -d "$(tail -n 1 "$LOG_FILE" | cut -d ' ' -f 1,2)" +%s)
        MINUTES_AGO=$((( $(date +%s) - LAST_LOG_SECONDS) / 60))
        echo "Letzte Aktivität: ${GREEN}vor $MINUTES_AGO Minuten${NC}"
    else
        echo "Letzte Aktivität: ${YELLOW}Log-Datei ist leer.${NC}"
    fi
    
    ERROR_COUNT=$(grep -c -iE "Fehler|error" "$LOG_FILE")
    [ "$ERROR_COUNT" -gt 0 ] && echo -e "Fehlerzähler: ${RED}${ERROR_COUNT} Fehler protokolliert${NC}" || echo -e "Fehlerzähler: ${GREEN}Keine Fehler${NC}"
    
    if [ "$ERROR_COUNT" -gt 0 ]; then
        echo "" 
        echo -e "${YELLOW}--- LETZTE FEHLERMELDUNGEN ---${NC}"
        grep -iE "Fehler|error" "$LOG_FILE" | tail -n 5 | while IFS= read -r line; do
            echo -e "${RED}- $line${NC}"
        done
    fi

else
    echo -e "${RED}Keine Log-Datei gefunden unter $LOG_FILE${NC}"
fi

echo -e "${CYAN}=======================================================${NC}"
