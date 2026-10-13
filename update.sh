#!/bin/bash
set -e

echo "--- Sicheres Update wird ausgeführt (Robuste Version) ---"

# 1. Sichere die lokal wichtigen Dateien/Werte
echo "1. Erstelle ein Backup von 'secret.json' und dem aktiven Portfolio..."
cp secret.json secret.json.bak

# active_strategies ist das ECHTE, vom woechentlichen Portfolio-Optimizer auf
# diesem VPS geschriebene Live-Team -- es wird nie nach GitHub gepusht. Ohne
# diese Sicherung setzt 'git reset --hard' das Team auf den (ggf. wochenalten)
# Repo-Stand zurueck (gleiches Muster wie ltbbot, dort 2026-09-24 live beobachtet).
SAVED_STRATEGIES=""
if [ -f settings.json ]; then
    SAVED_STRATEGIES=$(python3 -c "import json; s=json.load(open('settings.json')); print(json.dumps(s.get('live_trading_settings',{}).get('active_strategies',[])))" 2>/dev/null || true)
fi

# 2. Hole die neuesten Daten von GitHub
echo "2. Hole den neuesten Stand von GitHub..."
git fetch origin

# 3. Setze das lokale Verzeichnis hart auf den Stand von GitHub zurück
echo "3. Setze alle Dateien auf den neuesten Stand zurück und verwerfe lokale Änderungen..."
git reset --hard origin/main

# 4. Stelle die API-Schlüssel und das aktive Portfolio wieder her
echo "4. Stelle 'secret.json' und das aktive Portfolio wieder her..."
cp secret.json.bak secret.json
rm secret.json.bak

if [ -n "$SAVED_STRATEGIES" ] && [ "$SAVED_STRATEGIES" != "[]" ]; then
    SAVED_STRATEGIES="$SAVED_STRATEGIES" python3 -c "
import json, os
strategies = json.loads(os.environ['SAVED_STRATEGIES'])
s = json.load(open('settings.json'))
s.setdefault('live_trading_settings', {})['active_strategies'] = strategies
json.dump(s, open('settings.json', 'w'), indent=4)
" && echo "   ✅ active_strategies ($(echo "$SAVED_STRATEGIES" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))") Strategien) wiederhergestellt."
fi

# 5. Lösche den Python-Cache, um alte Code-Versionen zu entfernen
echo "5. Lösche alten Python-Cache für einen sauberen Neustart..."
find . -type f -name "*.pyc" -delete
find . -type d -name "__pycache__" -delete

# 6. Setze die Ausführungsrechte für alle Skripte
echo "6. Setze Ausführungsrechte für alle .sh-Skripte..."
chmod +x *.sh

echo "✅ Update erfolgreich abgeschlossen. Dein Bot ist jetzt auf dem neuesten Stand."
