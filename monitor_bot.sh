#!/bin/bash

# --- Dynamische Pfadermittlung ---
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)

# Pfade
CONFIG_FILE="$SCRIPT_DIR/code/strategies/envelope/config.json"
PYTHON_VENV="$SCRIPT_DIR/code/.venv/bin/python3"
OPTIMIZER_SCRIPT="$SCRIPT_DIR/code/analysis/optimizer.py"
CACHE_DIR="$SCRIPT_DIR/historical_data"

# --- Farbcodes ---
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'

function run_optimizer() {
    local script_args=""

    echo -e "${CYAN}=======================================================${NC}"
    echo -e "${CYAN}        GENETISCHER STRATEGIE-OPTIMIERER             ${NC}"
    echo -e "${CYAN}=======================================================${NC}"

    # --- Grundeinstellungen ---
    read -p "Startkapital in USDT (Standard: 1000): " START_CAPITAL
    script_args="--capital ${START_CAPITAL:-1000}"

    read -p "Zeitraum (z.B. 2025-01-01 to 2025-08-30): " date_range_input
    START_DATE=$(echo $date_range_input | awk '{print $1}')
    END_DATE=$(echo $date_range_input | awk '{print $3}')
    script_args="$script_args --start $START_DATE --end $END_DATE"

    read -p "Timeframes (z.B. 1h 4h): " TIMEFRAMES
    script_args="$script_args --timeframes \"$TIMEFRAMES\""

    read -p "Handelspaare (z.B. BTC PEPE): " SYMBOLS
    if [ -n "$SYMBOLS" ]; then script_args="$script_args --symbols $SYMBOLS"; fi
    
    echo -e "\n${YELLOW}--- EINSTELLUNGEN FÜR DEN GENETISCHEN ALGORITHMUS ---${NC}"
    read -p "Populationsgröße pro Generation (z.B. 50): " population_size
    script_args="$script_args --population_size ${population_size:-50}"
    
    read -p "Anzahl der Generationen (z.B. 100): " generation_count
    script_args="$script_args --generation_count ${generation_count:-100}"
    
    read -p "Mindestanzahl an Trades (z.B. 10): " min_trades
    script_args="$script_args --min_trades ${min_trades:-10}"

    echo -e "\n${YELLOW}--- PARAMETER-BEREICHE FÜR DIE OPTIMIERUNG ---${NC}"
    echo "Geben Sie für jeden Parameter einen Bereich im Format 'min-max' an."
    
    # --- Parameter-Bereiche Abfragen ---
    read -p "Supertrend ATR Periode (Bereich, z.B. 7-21): " st_atr_period
    script_args="$script_args --st_atr_period $st_atr_period"
    
    read -p "Supertrend Multiplikator (Bereich, z.B. 2.0-4.0): " st_atr_multiplier
    script_args="$script_args --st_atr_multiplier $st_atr_multiplier"

    read -p "Stop-Loss ATR Multiplikator (Bereich, z.B. 1.0-3.0): " sl_atr_multiplier
    script_args="$script_args --sl_atr_multiplier $sl_atr_multiplier"

    # NEU: Abfrage für Donchian SL
    read -p "Donchian Channel SL testen? [A]ktiviert / [D]eaktiviert / [B]eides: " donchian_choice
    local donchian_mode_range
    case "$donchian_choice" in
        [aA]) donchian_mode_range="1-1" ;;
        [dD]) donchian_mode_range="0-0" ;;
        [bB]) donchian_mode_range="0-1" ;;
        *)    donchian_mode_range="0-1" ;; # Standard: beides testen
    esac
    script_args="$script_args --donchian_sl_mode $donchian_mode_range"

    read -p "  -> Donchian Periode (Bereich, z.B. 10-40): " donchian_period
    script_args="$script_args --donchian_period $donchian_period"

    # NEU: Abfrage für Dynamischen Hebel
    read -p "Dynamischen Hebel testen? [A]ktiviert / [D]eaktiviert / [B]eides: " dyn_leverage_choice
    local dyn_leverage_mode_range
    case "$dyn_leverage_choice" in
        [aA]) dyn_leverage_mode_range="1-1" ;;
        [dD]) dyn_leverage_mode_range="0-0" ;;
        [bB]) dyn_leverage_mode_range="0-1" ;;
        *)    dyn_leverage_mode_range="0-1" ;; # Standard: beides testen
    esac
    script_args="$script_args --dyn_leverage_mode $dyn_leverage_mode_range"

    read -p "  -> ADX Schwellenwert (Bereich, z.B. 20-30): " hebel_adx_strong_trend_threshold
    script_args="$script_args --hebel_adx_strong_trend_threshold $hebel_adx_strong_trend_threshold"
    
    read -p "  -> Hebel für starken Trend (Bereich, z.B. 15-30): " hebel_leverage_strong_trend
    script_args="$script_args --hebel_leverage_strong_trend $hebel_leverage_strong_trend"

    read -p "  -> Hebel für schwachen Trend (Bereich, z.B. 5-15): " hebel_leverage_weak_trend
    script_args="$script_args --hebel_leverage_weak_trend $hebel_leverage_weak_trend"
    
    echo -e "\n${YELLOW}Starte Optimierung...${NC}"
    eval "$PYTHON_VENV $OPTIMIZER_SCRIPT $script_args"
    exit 0
}

# --- MODUS-AUSWAHL ---
case "$1" in
    optimize)
        run_optimizer
        ;;
    clear-cache)
        echo -e "${YELLOW}Cache löschen? (${CYAN}$CACHE_DIR${YELLOW})${NC}"
        read -p "Bestätige mit [j/N]: " response
        if [[ "$response" =~ ^([jJ][aA]|[jJ])$ ]]; then
            rm -rf "$CACHE_DIR" && echo -e "${GREEN}✔ Cache wurde erfolgreich gelöscht.${NC}"
        else
            echo -e "${RED}Aktion abgebrochen.${NC}"
        fi
        exit 0
        ;;
    *)
        echo -e "${CYAN}=======================================================${NC}"
        echo -e "${CYAN}           SUPERTREND TRADING BOT MONITORING           ${NC}"
        echo -e "${CYAN}=======================================================${NC}"
        echo "Verwende './monitor_bot.sh <mode>', Modi: ${GREEN}optimize, clear-cache${NC}"
        ;;
esac
