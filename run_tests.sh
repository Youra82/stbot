#!/bin/bash
# Dieses Skript führt das komplette Test-Sicherheitsnetz aus.
echo "--- Starte KBot-Sicherheitsnetz ---"

# Prüfe ob virtuelle Umgebung existiert
if [ ! -f ".venv/bin/activate" ]; then
    echo "Fehler: Virtuelle Umgebung nicht gefunden. Bitte install.sh ausführen."
    exit 1
fi

# Aktiviere die virtuelle Umgebung
source .venv/bin/activate

# Führe pytest aus
echo "Führe Pytest aus (Stoch‑RSI Tests)..."
if python3 -m pytest tests/ -v -s; then
    echo "Pytest erfolgreich durchgelaufen. Alle Tests bestanden."
    EXIT_CODE=0
else
    PYTEST_EXIT_CODE=$?
    if [ $PYTEST_EXIT_CODE -eq 5 ]; then
        echo "Pytest beendet: Keine Tests zum Ausführen gefunden."
        EXIT_CODE=0
    else
        echo "Pytest fehlgeschlagen (Exit Code: $PYTEST_EXIT_CODE)."
        EXIT_CODE=$PYTEST_EXIT_CODE
    fi
fi

# Deaktiviere die Umgebung wieder
deactivate

echo "--- Sicherheitscheck abgeschlossen ---"
exit $EXIT_CODE
