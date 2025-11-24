# StBot

Ein vollautomatischer Trading-Bot für Krypto-Futures auf der Bitget-Börse, basierend auf der **Support & Resistance Dynamic v2 (SRv2)** Strategie.

Dieses System wurde für den Betrieb auf einem Ubuntu-Server entwickelt und umfasst neben dem Live-Trading-Modul eine hochentwickelte, automatisierte Pipeline zur Parameter-Optimierung (Optuna) und Portfolio-Zusammenstellung.

## Kernstrategie 🧱

Der Bot implementiert eine Breakout-Strategie, die dynamische Unterstützungs- und Widerstandszonen identifiziert und handelt, wenn der Preis diese durchbricht.

* **Dynamische Pivot-Punkte:** Der Algorithmus scannt kontinuierlich nach lokalen Hochs und Tiefs über einen definierten Zeitraum (`pivot_period`).
* **S/R-Cluster Bildung:**
    * Die gefundenen Pivot-Punkte werden gruppiert. Wenn mehrere Pivots in einem engen Preisbereich (`channel_width`) liegen, bildet sich eine Zone.
    * **Stärke-Filter:** Nur Zonen, die eine Mindestanzahl an Berührungen (`min_strength`) aufweisen, werden als valide angesehen.
* **Breakout-Signale:**
    * **Long (Buy):** Ein Trade wird eröffnet, wenn eine **Widerstandszone (Resistance)** nach oben durchbrochen wird.
    * **Short (Sell):** Ein Trade wird eröffnet, wenn eine **Unterstützungszone (Support)** nach unten durchbrochen wird.
* **Ausstieg & Risikomanagement:**
    * **Positionsgröße:** Dynamisch berechnet basierend auf einem festen Prozentsatz (`risk_per_trade_pct`) des aktuellen Kontostandes.
    * **Dynamischer Stop Loss:** Der Stop Loss basiert auf der Volatilität (**ATR**) oder einem prozentualen Mindestabstand zum Entry.
    * **Trailing Stop:** Sobald der Trade in den Gewinn läuft, wird ein Trailing-Stop aktiviert, um Gewinne bei Trendumkehr zu sichern.

## Architektur & Arbeitsablauf

Der Bot arbeitet mit einem präzisen, automatisierten und ressourcenschonenden System.

1.  **Der Cronjob (Der Wecker):** Ein einziger, simpler Cronjob läuft in einem kurzen Intervall (z.B. alle 15 Minuten). Er hat nur eine Aufgabe: den intelligenten Master-Runner zu starten.

2.  **Der Master-Runner (Der Dirigent):** Das `master_runner.py`-Skript ist das Herz der Automatisierung. Bei jedem Aufruf:
    * Liest es alle aktiven Strategien aus der `settings.json` (oder dem optimierten Portfolio).
    * Prüft es für jede Strategie, ob ein **neuer, exakter Zeit-Block** begonnen hat.
    * Nur wenn eine Strategie an der Reihe ist, startet es den eigentlichen Handelsprozess für diese eine Strategie.
    * Es **sammelt die komplette Log-Ausgabe** und schreibt sie in die zentrale `cron.log`.

3.  **Der Handelsprozess (Der Agent):**
    * Die `run.py` wird für eine spezifische Strategie gestartet.
    * Der **Guardian-Decorator** führt zuerst Sicherheits-Checks durch.
    * Die Kernlogik in `trade_manager.py` wird ausgeführt:
        1.  Abruf historischer Daten.
        2.  Berechnung der Pivots und S/R-Zonen (**SREngine**).
        3.  Prüfung auf Breakout-Signale (Durchbruch durch valide Zone).
        4.  Ausführung der Order bei Bitget inkl. SL/TP.

---

## Installation 🚀

Führe die folgenden Schritte auf einem frischen Ubuntu-Server (oder lokal) aus.

#### 1. Projekt klonen

```bash
git clone [https://github.com/Youra82/stbot.git](https://github.com/Youra82/stbot.git)
````

*(Hinweis: Passe die URL an, falls das Repo noch anders heißt)*

#### 2\. Installations-Skript ausführen

```bash
cd stbot
```

Installation aktivieren (einmalig):

```bash
chmod +x install.sh
```

Installation ausführen:

```bash
bash ./install.sh
```

#### 3\. API-Schlüssel eintragen

Erstelle eine Kopie der Vorlage und trage deine Schlüssel ein.

```bash
cp secret.json.example secret.json
nano secret.json
```

*(Achte darauf, dass der Hauptschlüssel in der JSON-Datei `"stbot"` heißt).*

Speichere mit `Strg + X`, dann `Y`, dann `Enter`.

-----

## Konfiguration & Automatisierung

#### 1\. Strategien finden (Pipeline)

Führe die interaktive Pipeline aus, um die besten SRv2-Parameter (Pivot-Perioden, Cluster-Breite) für bestimmte Coins zu finden.

Skripte aktivieren (einmalig):

```bash
chmod +x *.sh
```

Pipeline starten:

```bash
./run_pipeline.sh
```

#### 2\. Ergebnisse analysieren

Nach der Optimierung kannst du die Ergebnisse auswerten und Portfolios simulieren.

```bash
./show_results.sh
```

  * **Modus 1:** Einzelstrategien prüfen.
  * **Modus 2:** Manuelles Portfolio zusammenstellen.
  * **Modus 3:** Automatische Portfolio-Optimierung (findet die beste Kombi für z.B. max. 30% Drawdown).

Ergebnisse an Telegram senden:

```bash
./send_report.sh optimal_portfolio_equity.csv
./show_chart.sh optimal_portfolio_equity.csv
```

Aufräumen (Alte Configs löschen für Neustart):

```bash
rm -f src/stbot/strategy/configs/config_*.json
rm artifacts/db/*.db
```

#### 3\. Strategien für den Handel aktivieren

Bearbeite die `settings.json`. Du kannst entweder Strategien manuell eintragen oder den Bot anweisen, automatisch das optimierte Portfolio zu nutzen.

```bash
nano settings.json
```

**Empfohlene Einstellung (Autopilot):**

```json
{
    "live_trading_settings": {
        "use_auto_optimizer_results": true,
        "active_strategies": []
    },
    "optimization_settings": {
        "enabled": false
    }
}
```

#### 4\. Automatisierung per Cronjob einrichten

Richte den automatischen Prozess für den Live-Handel ein.

```bash
crontab -e
```

Füge die folgende Zeile am Ende ein (Pfad anpassen, z.B. `/root/stbot`):

```bash
# Starte den StBot Master-Runner alle 15 Minuten
*/15 * * * * /usr/bin/flock -n /root/stbot/stbot.lock /bin/sh -c "cd /root/stbot && /root/stbot/.venv/bin/python3 /root/stbot/master_runner.py >> /root/stbot/logs/cron.log 2>&1"
```

Logverzeichnis anlegen:

```bash
mkdir -p /root/stbot/logs
```

-----

## Tägliche Verwaltung & Wichtige Befehle ⚙️

#### Logs ansehen

Die zentrale `cron.log` enthält alle Aktivitäten.

  * **Logs live mitverfolgen:**
    ```bash
    tail -f logs/cron.log
    ```
  * **Nach Fehlern suchen:**
    ```bash
    grep -i "ERROR" logs/cron.log
    ```
  * **Individuelle Strategie-Logs:**
    ```bash
    tail -n 100 logs/stbot_BTCUSDTUSDT_4h.log
    ```

#### Manueller Start (Test)

Um den `master_runner` sofort auszuführen, ohne auf den Cronjob zu warten:

```bash
python3 master_runner.py
```

#### Bot aktualisieren

Um den neuesten Code von GitHub zu laden und die Umgebung sauber zu halten:

```bash
./update.sh
```

## Qualitätssicherung & Tests 🛡️

Um sicherzustellen, dass die SR-Logik und die API-Verbindung korrekt funktionieren, nutze das Test-System.

**Wann ausführen?** Nach jedem Update oder Code-Änderungen.

```bash
./run_tests.sh
```

  * **Erfolgreich:** Alle Tests `PASSED` (Grün).
  * **Fehler:** Tests `FAILED` (Rot). Der Bot sollte nicht live gehen.

-----

## Git Management

Projekt hochladen (Backup):

```bash
git add .
git commit -m "Update StBot Konfiguration"
git push --force origin main
```

Projektstatus prüfen:

```bash
./show_status.sh
```

-----

### ⚠️ Disclaimer

Dieses Material dient ausschließlich zu Bildungs- und Unterhaltungszwecken. Es handelt sich nicht um eine Finanzberatung. Der Nutzer trägt die alleinige Verantwortung für alle Handlungen. Der Autor haftet nicht für etwaige Verluste. Trading mit Krypto-Futures beinhaltet ein hohes Risiko.

```
```
