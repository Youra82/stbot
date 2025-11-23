#!/bin/bash
# rename_to_stbot.sh

echo "--- Starte Umbenennung von StBot zu StBot ---"

# 1. Ordner umbenennen
if [ -d "src/titanbot" ]; then
    echo "Benenne Ordner src/titanbot in src/stbot um..."
    mv src/titanbot src/stbot
else
    echo "Ordner src/titanbot nicht gefunden (vielleicht schon umbenannt?)."
fi

# 2. Text in allen Dateien ersetzen (titanbot -> stbot)
echo "Ersetze 'titanbot' durch 'stbot' in allen Dateien..."
grep -rIl "titanbot" . --exclude-dir=.git --exclude-dir=.venv --exclude=rename_to_stbot.sh | xargs sed -i 's/titanbot/stbot/g'

# 3. Text ersetzen (StBot -> StBot für Log-Ausgaben/Titel)
echo "Ersetze 'StBot' durch 'StBot' in allen Dateien..."
grep -rIl "StBot" . --exclude-dir=.git --exclude-dir=.venv --exclude=rename_to_stbot.sh | xargs sed -i 's/StBot/StBot/g'

# 4. Hauptordner umbenennen (optional, falls du im root bist)
current_dir=$(basename "$PWD")
if [ "$current_dir" == "titanbot" ]; then
    echo "HINWEIS: Dein aktueller Ordner heißt noch 'titanbot'. Du solltest cd .. und mv titanbot stbot machen."
fi

echo "✅ Umbenennung abgeschlossen!"
