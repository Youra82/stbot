#!/bin/bash
# rename_to_stbot.sh
set -e

echo "--- Starte Transformation zu StBot ---"

# 1. Ordner umbenennen
if [ -d "src/utbot2" ]; then
    echo "Benenne src/utbot2 in src/stbot um..."
    mv src/utbot2 src/stbot
fi

# 2. Text in allen Dateien ersetzen
echo "Ersetze 'utbot2' durch 'stbot' in allen Dateien..."
grep -rIl "utbot2" . --exclude-dir=.git --exclude-dir=.venv --exclude-dir=__pycache__ --exclude=rename_to_stbot.sh | xargs sed -i 's/utbot2/stbot/g'

echo "Ersetze 'UtBot2' durch 'StBot' in allen Dateien..."
grep -rIl "UtBot2" . --exclude-dir=.git --exclude-dir=.venv --exclude-dir=__pycache__ --exclude=rename_to_stbot.sh | xargs sed -i 's/UtBot2/StBot/g'

echo "Ersetze 'TitanBot' durch 'StBot' in allen Dateien..."
grep -rIl "TitanBot" . --exclude-dir=.git --exclude-dir=.venv --exclude-dir=__pycache__ --exclude=rename_to_stbot.sh | xargs sed -i 's/TitanBot/StBot/g'

# 3. Alte Ichimoku-Dateien und Configs entfernen
echo "Entferne alte Ichimoku-Komponenten..."
rm -f src/stbot/strategy/ichimoku_engine.py
rm -f src/stbot/strategy/configs/config_*.json

# 4. Hauptordner umbenennen (Versuch)
current_dir=$(basename "$PWD")
if [ "$current_dir" == "utbot2" ]; then
    echo "HINWEIS: Bitte führe nach diesem Skript 'cd .. && mv utbot2 stbot && cd stbot' aus."
fi

echo "✅ Umbenennung intern abgeschlossen. Bitte neue Dateien einfügen."
