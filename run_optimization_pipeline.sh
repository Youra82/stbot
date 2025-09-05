#!/bin/bash

# Pfad zum Projektverzeichnis dynamisch ermitteln
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
VENV_PATH="$SCRIPT_DIR/code/.venv/bin/activate"
GLOBAL_OPTIMIZER="$SCRIPT_DIR/code/analysis/global_optimizer_pymoo.py"
LOCAL_REFINER="$SCRIPT_DIR/code/analysis/local_refiner_optuna.py"
BACKTESTER="$SCRIPT_DIR/code/analysis/run_backtest.py" # NEU
CANDIDATES_FILE="$SCRIPT_DIR/code/analysis/optimization_candidates.json"
CACHE_DIR="$SCRIPT_DIR/code/analysis/historical_data"

# --- Farbcodes ---
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

# --- Virtuelle Umgebung aktivieren ---
if [ -f "$VENV_PATH" ]; then
    source "$VENV_PATH"
else
    echo -e "${RED}Fehler: Virtuelle Umgebung nicht gefunden. Bitte 'install.sh' ausführen.${NC}"
    exit 1
fi

# --- Hauptmenü ---
echo -e "${BLUE}======================================================="
echo "       Analyse- & Optimierungs-Werkzeuge"
echo -e "=======================================================${NC}"
echo "Wähle einen Modus:"
echo "  1) Komplette Optimierungs-Pipeline starten (Pymoo & Optuna)"
echo "  2) Einzel-Backtest der aktuellen Live-Konfiguration starten"
echo "  3) Daten-Cache löschen"
read -p "Auswahl (1-3): " mode

case "$mode" in
    1)
        echo -e "\n${GREEN}>>> Modus: Optimierungs-Pipeline gewählt.${NC}"
        read -p "Mit wie vielen CPU-Kernen soll optimiert werden? (Standard: 1): " N_CORES
        N_CORES=${N_CORES:-1}

        echo -e "${GREEN}>>> STARTE STUFE 1: Globale Suche mit Pymoo...${NC}"
        python3 "$GLOBAL_OPTIMIZER" --jobs "$N_CORES"

        if [ ! -f "$CANDIDATES_FILE" ]; then
            echo -e "${RED}Fehler: Stufe 1 hat keine Ergebnisse geliefert. Breche ab.${NC}"
            deactivate
            exit 1
        fi

        echo -e "\n${GREEN}>>> STARTE STUFE 2: Lokale Verfeinerung mit Optuna...${NC}"
        python3 "$LOCAL_REFINER" --jobs "$N_CORES"
        ;;
    2)
        echo -e "\n${GREEN}>>> Modus: Einzel-Backtest gewählt.${NC}"
        python3 "$BACKTESTER"
        ;;
    3)
        echo -e "\n${GREEN}>>> Modus: Cache löschen gewählt.${NC}"
        read -p "Möchtest du den gesamten Daten-Cache wirklich löschen? [j/N]: " response
        if [[ "$response" =~ ^([jJ][aA]|[jJ])$ ]]; then
            rm -rfv "$CACHE_DIR"/*
            echo -e "${GREEN}✔ Cache wurde erfolgreich gelöscht.${NC}"
        else
            echo -e "${RED}Aktion abgebrochen.${NC}"
        fi
        ;;
    *)
        echo -e "${RED}Ungültige Auswahl. Skript wird beendet.${NC}"
        ;;
esac

deactivate
echo -e "\n${BLUE}Aktion abgeschlossen.${NC}"
