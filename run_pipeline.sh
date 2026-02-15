#!/bin/bash
# =============================================================================
# KBot: Stoch‑RSI - Pipeline
# =============================================================================
# Diese Pipeline optimiert die Parameter der Stoch‑RSI Strategie.
# Kein ML-Training nötig - nur Parameter-Optimierung!
# =============================================================================

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${BLUE}======================================================="
echo "   KBot Stoch‑RSI - Parameter Optimierung"
echo -e "=======================================================${NC}"

# --- Pfade definieren ---
VENV_PATH=".venv/bin/activate"
OPTIMIZER="src/kbot/analysis/optimizer.py"

# --- Umgebung aktivieren ---
if [ ! -f "$VENV_PATH" ]; then
    echo -e "${RED}❌ Virtual Environment nicht gefunden!${NC}"
    echo "Bitte zuerst 'python3 -m venv .venv && pip install -r requirements.txt' ausführen."
    exit 1
fi

source "$VENV_PATH"
echo -e "${GREEN}✔ Virtuelle Umgebung aktiviert.${NC}"

# --- AUFRÄUM-ASSISTENT ---
echo -e "\n${YELLOW}Möchtest du alle alten Konfigurationen vor dem Start löschen?${NC}"
read -p "Dies wird für einen kompletten Neustart empfohlen. (j/n) [Standard: n]: " CLEANUP_CHOICE
CLEANUP_CHOICE=${CLEANUP_CHOICE:-n}

if [[ "${CLEANUP_CHOICE,,}" == "j" ]]; then
    echo -e "${YELLOW}Lösche alte Konfigurationen...${NC}"
    rm -f src/kbot/strategy/configs/config_*.json 2>/dev/null || true
    echo -e "${GREEN}✔ Aufräumen abgeschlossen.${NC}"
fi

# --- Interaktive Abfrage ---
echo ""
read -p "Handelspaar(e) eingeben (ohne /USDT, z.B. BTC ETH): " SYMBOLS
read -p "Zeitfenster eingeben (z.B. 4h 1d): " TIMEFRAMES

# --- Validierung ---
VALID_TIMEFRAMES="1m 5m 15m 30m 1h 2h 4h 6h 12h 1d 1w"
for tf in $TIMEFRAMES; do
    if [[ ! " $VALID_TIMEFRAMES " =~ " $tf " ]]; then
        echo -e "${RED}Fehler: Zeitrahmen '$tf' wird nicht unterstützt!${NC}"
        exit 1
    fi
done

# --- Datums-Empfehlung ---
echo -e "\n${BLUE}--- Empfehlung: Optimaler Rückblick-Zeitraum ---${NC}"
printf "+-------------+--------------------------------+\n"
printf "| Zeitfenster | Empfohlener Rückblick (Tage)   |\n"
printf "+-------------+--------------------------------+\n"
printf "| 15m, 30m    | 30 - 90 Tage                   |\n"
printf "| 1h, 2h      | 180 - 365 Tage                 |\n"
printf "| 4h, 6h      | 365 - 730 Tage                 |\n"
printf "| 1d, 1w      | 730 - 1825 Tage                |\n"
printf "+-------------+--------------------------------+\n"

read -p "Startdatum (JJJJ-MM-TT) oder 'a' für Automatik [Standard: a]: " START_DATE_INPUT
START_DATE_INPUT=${START_DATE_INPUT:-a}

read -p "Enddatum (JJJJ-MM-TT) [Standard: Gestern]: " END_DATE
END_DATE=${END_DATE:-$(date -d '1 day ago' +%F)}

# --- Automatisches Startdatum basierend auf Timeframe ---
if [ "$START_DATE_INPUT" == "a" ]; then
    # Bestimme Lookback basierend auf erstem Timeframe
    FIRST_TF=$(echo $TIMEFRAMES | awk '{print $1}')
    case "$FIRST_TF" in
        15m|30m) LOOKBACK_DAYS=60 ;;
        1h|2h) LOOKBACK_DAYS=365 ;;
        4h|6h) LOOKBACK_DAYS=730 ;;
        1d|1w) LOOKBACK_DAYS=1095 ;;
        *) LOOKBACK_DAYS=365 ;;
    esac
    START_DATE=$(date -d "$LOOKBACK_DAYS days ago" +%F)
    echo -e "${CYAN}Automatisches Startdatum: $START_DATE (${LOOKBACK_DAYS} Tage zurück)${NC}"
else
    START_DATE=$START_DATE_INPUT
fi

# --- Weitere Parameter ---
read -p "Startkapital in USDT [Standard: 1000]: " START_CAPITAL
START_CAPITAL=${START_CAPITAL:-1000}

read -p "Anzahl Trials [Standard: 100]: " N_TRIALS
N_TRIALS=${N_TRIALS:-100}

read -p "CPU-Kerne [Standard: -1 für alle]: " N_CORES
N_CORES=${N_CORES:--1}

# --- Optimierungs-Modus ---
echo -e "\n${YELLOW}Wähle einen Optimierungs-Modus:${NC}"
echo "  1) Strenger Modus (Min Win-Rate, Min PnL, Max DD)"
echo "  2) 'Finde das Beste'-Modus (nur Max DD als Constraint)"
read -p "Auswahl (1-2) [Standard: 2]: " OPTIM_MODE
OPTIM_MODE=${OPTIM_MODE:-2}

read -p "Max Drawdown % [Standard: 30]: " MAX_DD
MAX_DD=${MAX_DD:-30}

if [ "$OPTIM_MODE" == "1" ]; then
    OPTIM_MODE_ARG="strict"
    read -p "Min Win-Rate % [Standard: 50]: " MIN_WR
    MIN_WR=${MIN_WR:-50}
    read -p "Min PnL % [Standard: 0]: " MIN_PNL
    MIN_PNL=${MIN_PNL:-0}
else
    OPTIM_MODE_ARG="best_profit"
    MIN_WR=0
    MIN_PNL=-99999
fi

# --- Zusammenfassung ---
echo -e "\n${BLUE}=======================================================${NC}"
echo -e "${BLUE}  OPTIMIERUNGS-PARAMETER${NC}"
echo -e "${BLUE}=======================================================${NC}"
echo -e "  Symbole:       ${CYAN}$SYMBOLS${NC}"
echo -e "  Timeframes:    ${CYAN}$TIMEFRAMES${NC}"
echo -e "  Zeitraum:      ${CYAN}$START_DATE bis $END_DATE${NC}"
echo -e "  Startkapital:  ${CYAN}$START_CAPITAL USDT${NC}"
echo -e "  Trials:        ${CYAN}$N_TRIALS${NC}"
echo -e "  Modus:         ${CYAN}$OPTIM_MODE_ARG${NC}"
echo -e "  Max Drawdown:  ${CYAN}$MAX_DD%${NC}"
echo -e "${BLUE}=======================================================${NC}"

read -p "Starten? (Enter zum Fortfahren, Ctrl+C zum Abbrechen): " _

# --- Optimierung starten ---
echo -e "\n${GREEN}🚀 Starte Optimierung...${NC}\n"

python3 "$OPTIMIZER" \
    --symbols "$SYMBOLS" \
    --timeframes "$TIMEFRAMES" \
    --start_date "$START_DATE" \
    --end_date "$END_DATE" \
    --trials "$N_TRIALS" \
    --jobs "$N_CORES" \
    --max_drawdown "$MAX_DD" \
    --min_win_rate "$MIN_WR" \
    --min_pnl "$MIN_PNL" \
    --start_capital "$START_CAPITAL" \
    --mode "$OPTIM_MODE_ARG"

if [ $? -eq 0 ]; then
    echo -e "\n${GREEN}✅ Optimierung erfolgreich abgeschlossen!${NC}"
    echo -e "\n${YELLOW}Nächste Schritte:${NC}"
    echo "  1) ./show_results.sh        # Ergebnisse anzeigen"
    echo "  2) python master_runner.py  # Bot starten"
else
    echo -e "\n${RED}❌ Fehler bei der Optimierung.${NC}"
fi

deactivate
