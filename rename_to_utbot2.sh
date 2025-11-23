#!/bin/bash
# rename_to_utbot2.sh

echo "--- Starte Umbenennung von TitanBot zu UtBot2 ---"

# 1. Ordner umbenennen
if [ -d "src/titanbot" ]; then
    echo "Benenne Ordner src/titanbot in src/utbot2 um..."
    mv src/titanbot src/utbot2
else
    echo "Ordner src/titanbot nicht gefunden (vielleicht schon umbenannt?)."
fi

# 2. Text in allen Dateien ersetzen (titanbot -> utbot2)
echo "Ersetze 'titanbot' durch 'utbot2' in allen Dateien..."
grep -rIl "titanbot" . --exclude-dir=.git --exclude-dir=.venv --exclude=rename_to_utbot2.sh | xargs sed -i 's/titanbot/utbot2/g'

# 3. Text ersetzen (TitanBot -> UtBot2 für Log-Ausgaben/Titel)
echo "Ersetze 'TitanBot' durch 'UtBot2' in allen Dateien..."
grep -rIl "TitanBot" . --exclude-dir=.git --exclude-dir=.venv --exclude=rename_to_utbot2.sh | xargs sed -i 's/TitanBot/UtBot2/g'

# 4. Hauptordner umbenennen (optional, falls du im root bist)
current_dir=$(basename "$PWD")
if [ "$current_dir" == "titanbot" ]; then
    echo "HINWEIS: Dein aktueller Ordner heißt noch 'titanbot'. Du solltest cd .. und mv titanbot utbot2 machen."
fi

echo "✅ Umbenennung abgeschlossen!"
