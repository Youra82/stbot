#!/bin/bash
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'
# venv activation not required — we call the venv python directly when available

# Determine repository root (script may be run from any cwd)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Locate the appropriate show_results.py for this bot (script-root first, then workspace-root)
if [ -f "$SCRIPT_DIR/src/kbot/analysis/show_results.py" ]; then
    RESULTS_SCRIPT="$SCRIPT_DIR/src/kbot/analysis/show_results.py"
elif [ -f "$SCRIPT_DIR/src/stbot/analysis/show_results.py" ]; then
    RESULTS_SCRIPT="$SCRIPT_DIR/src/stbot/analysis/show_results.py"
elif [ -f "$ROOT_DIR/src/kbot/analysis/show_results.py" ]; then
    RESULTS_SCRIPT="$ROOT_DIR/src/kbot/analysis/show_results.py"
elif [ -f "$ROOT_DIR/src/stbot/analysis/show_results.py" ]; then
    RESULTS_SCRIPT="$ROOT_DIR/src/stbot/analysis/show_results.py"
else
    RESULTS_SCRIPT=""
fi

# Prefer the project's venv Python (Windows or UNIX layout), else fallback to system python
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
elif [ -x "$ROOT_DIR/.venv/Scripts/python.exe" ]; then
    PYTHON_BIN="$ROOT_DIR/.venv/Scripts/python.exe"
else
    PYTHON_BIN="python3"
fi

# --- MODUS-MENÜ ---
echo -e "\n${YELLOW}Wähle einen Analyse-Modus:${NC}"
echo "  1) Einzel-Analyse (jede Strategie wird isoliert getestet)"
echo "  2) Manuelle Portfolio-Simulation (du wählst das Team)"
echo "  3) Automatische Portfolio-Optimierung (der Bot wählt das beste Team)"
echo "  4) Interaktive Charts (Entry/Exit-Signale nur, keine Indikatoren)"
read -p "Auswahl (1-4) [Standard: 1]: " MODE
MODE=${MODE:-1}

# *** NEU: Max Drawdown Abfrage für Modus 3 ***
TARGET_MAX_DD=30 # Standardwert
if [ "$MODE" == "3" ]; then
    read -p "Gewünschter maximaler Drawdown in % für die Optimierung [Standard: 30]: " DD_INPUT
    # Prüfe, ob eine gültige Zahl eingegeben wurde, sonst nimm Standard
    if [[ "$DD_INPUT" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
        TARGET_MAX_DD=$DD_INPUT
    else
        echo "Ungültige Eingabe, verwende Standard: ${TARGET_MAX_DD}%"
    fi
fi
# *** ENDE NEU ***

if [ ! -f "$RESULTS_SCRIPT" ]; then
    echo -e "${RED}Fehler: Die Analyse-Datei '$RESULTS_SCRIPT' wurde nicht gefunden.${NC}"
    exit 1
fi

# *** Übergebe Mode und Max DD an das Python Skript (inkl. Mode 4) ***
# Run using the chosen Python. If the Python is a Windows exe and the script path is a Unix-style path (WSL/MSYS), convert it to a Windows path first.
PY_SCRIPT_PATH="$RESULTS_SCRIPT"
if [[ "$PYTHON_BIN" == *.exe || "$PYTHON_BIN" == [A-Za-z]:\\* ]]; then
  if command -v wslpath >/dev/null 2>&1; then
    PY_SCRIPT_PATH="$(wslpath -w "$RESULTS_SCRIPT")"
  elif command -v cygpath >/dev/null 2>&1; then
    PY_SCRIPT_PATH="$(cygpath -w "$RESULTS_SCRIPT")"
  else
    # best-effort conversion from /mnt/c/... to C:\... for Windows Python
    PY_SCRIPT_PATH="$(echo "$RESULTS_SCRIPT" | sed -E 's#^/mnt/([a-zA-Z])/#\1:/#; s#/#\\#g')"
  fi
fi

"$PYTHON_BIN" "$PY_SCRIPT_PATH" --mode "$MODE" --target_max_drawdown "$TARGET_MAX_DD"

# --- OPTION 4: INTERAKTIVE CHARTS (Behandelt direkt in show_results.py) ---
if [ "$MODE" == "4" ]; then
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ Interaktive Charts wurden generiert!${NC}"
    else
        echo -e "${RED}❌ Fehler beim Generieren der Charts.${NC}"
    fi
        exit 0
    fi
