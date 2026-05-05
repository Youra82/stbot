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
git clone https://github.com/Youra82/stbot.git
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
*/15 * * * * /usr/bin/flock -n /root/stbot/stbot.lock /bin/sh -c "cd /root/stbot && .venv/bin/python3 master_runner.py >> /root/stbot/logs/cron.log 2>&1"
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
cd /root/stbot && .venv/bin/python3 master_runner.py
```

#### Bot aktualisieren

Um den neuesten Code von GitHub zu laden und die Umgebung sauber zu halten:

```bash
./update.sh
```

## 🔄 Auto-Optimizer Verwaltung

Der Bot verfügt über einen automatischen Optimizer, der wöchentlich die besten Parameter für alle aktiven Strategien sucht (Support/Resistance SRv2). Die folgenden Befehle helfen beim manuellen Triggern, Debugging und Monitoring des Optimizers.

### Optimizer manuell triggern

Um eine sofortige Optimierung zu starten (ignoriert das Zeitintervall):

```bash
# Letzten Optimierungszeitpunkt löschen (erzwingt Neustart)
rm ~/stbot/data/cache/.last_optimization_run

# Master Runner starten (prüft ob Optimierung fällig ist)
cd ~/stbot && .venv/bin/python3 master_runner.py
```

Oder direkt per `--force`:

```bash
cd ~/stbot && .venv/bin/python3 auto_optimizer_scheduler.py --force
```

### Replot — Charts neu generieren (ohne Re-Optimierung)

Das aktive Portfolio erneut simulieren und Equity-Chart + Trades-Excel via Telegram senden — ohne die komplette Optimierung neu durchzuführen:

```bash
cd ~/stbot && .venv/bin/python3 run_portfolio_optimizer.py --replot
```

Optionale Parameter (werden sonst aus `settings.json` gelesen):
```bash
.venv/bin/python3 run_portfolio_optimizer.py --replot --capital 200 --start-date 2024-01-01 --end-date 2025-01-01
```

### Optimizer-Logs überwachen

```bash
# Optimizer-Log live mitverfolgen
tail -f ~/stbot/logs/auto_optimizer_trigger.log

# Letzte 50 Zeilen des Optimizer-Logs anzeigen
tail -50 ~/stbot/logs/auto_optimizer_trigger.log
```

### Optimierungsergebnisse ansehen

```bash
# Beste gefundene Parameter anzeigen (erste 50 Zeilen)
cat ~/stbot/artifacts/results/optimization_results.json | head -50
```

### Optimizer-Prozess überwachen

```bash
# Prüfen ob Optimizer gerade läuft (aktualisiert jede Sekunde)
watch -n 1 "ps aux | grep optimizer"
```

### Optimizer stoppen

```bash
# Alle Optimizer-Prozesse auf einmal stoppen
pkill -f "auto_optimizer_scheduler" ; pkill -f "run_pipeline_automated" ; pkill -f "optimizer.py"

# Prüfen ob alles gestoppt ist
pgrep -fa "optimizer" && echo "Noch aktiv!" || echo "Alle gestoppt."

# In-Progress-Marker aufräumen (sauberer Neustart danach)
rm -f ~/stbot/data/cache/.optimization_in_progress
```

-----

## Qualitätssicherung & Tests 🛡️

Um sicherzustellen, dass die SR-Logik und die API-Verbindung korrekt funktionieren, nutze das Test-System.

**Wann ausführen?** Nach jedem Update oder Code-Änderungen.

```bash
./run_tests.sh
```

  * **Erfolgreich:** Alle Tests `PASSED` (Grün).
  * **Fehler:** Tests `FAILED` (Rot). Der Bot sollte nicht live gehen.

---

## Coin & Timeframe Empfehlungen

StBot ist eine **S/R-Breakout-Strategie** — er sucht dynamische Unterstützungs- und Widerstandszonen aus Pivot-Hochs/-Tiefs und tradet den Ausbruch. Benötigt: klare Pivot-Struktur, ausreichend Volumen für Bestätigung und erkennbaren Trend für den MTF-EMA-Bias-Filter (EMA20 vs EMA50).

### Effektive Zeitspannen je Timeframe

| TF | Pivot(20) — S/R Zonen | ATR(14) — SL | Vol-MA(20) | EMA20/50 — Bias | Geeignet |
|---|---|---|---|---|---|
| 15m | 5h | 3.5h | 5h | 5h / 12.5h | ❌ |
| 30m | 10h | 7h | 10h | 10h / 25h | ⚠️ |
| 1h | 20h | 14h | 20h | 20h / 50h | ✅ |
| **2h** | **40h** | **28h** | **40h** | **40h / 100h** | **✅✅** |
| **4h** | **80h** | **56h** | **80h** | **80h / 200h** | **✅✅** |
| 6h | 120h | 84h | 120h | 120h / 300h | ✅ |
| 1d | 20d | 14d | 20d | 20d / 50d | ✅ |

Auf 15m entstehen zu viele Fake-Pivots. Ab 2h haben S/R-Zonen ausreichend Testhistorie und Volumen-Bestätigung ist aussagekräftig. Die EMA20/50-Bias-Werte auf 4h (80h / 200h) haben industrielle Relevanz.

### Coin-Eignung

| Coin | S/R Struktur | Breakout-Qualität | Vol.-Zuverlässigkeit | Bewertung |
|---|---|---|---|---|
| **BTC** | Exzellent — institutionelle S/R Levels | Klare, bestätigte Breakouts | Sehr hohe Liquidität | ✅✅ Beste Wahl |
| **ETH** | Exzellent — ähnlich BTC | Saubere Ausbrüche | Sehr hohe Liquidität | ✅✅ Sehr gut |
| **SOL** | Sehr gut — klare Swing-Hochs/-Tiefs | Starke Breakouts mit Volumen | Gute Liquidität | ✅ Gut |
| **XRP** | Gut — klare historische Levels | Solide Breakouts | Hohe Liquidität | ✅ Gut |
| **BNB** | Gut — stabile Zonen | Moderate Breakout-Stärke | Gute Liquidität | ✅ Gut |
| **AVAX** | Gut — klare Swing-Struktur | Gute Breakouts in Bullphasen | Ausreichend | ✅ Gut |
| **ARB** | Gut — ETH-korreliert | Solide Ausbrüche | Ausreichend | ✅ Gut |
| **LTC** | Mittel — folgt BTC | Ausbrüche weniger dynamisch | Gut | ⚠️ Mittel |
| **LINK** | Mittel — S/R-Zonen vorhanden | Breakouts oft kurzlebig | Mittel | ⚠️ Mittel |
| **ADA** | Mittel — viele False Breakouts | Niedriges Volumen bei Ausbrüchen | Mittel | ⚠️ Mittel |
| **DOT** | Mittel — in Bullphasen klar | Phasenabhängig | Mittel | ⚠️ Mittel |
| **AAVE** | Schwach — kleine S/R-Zonen | Breakouts oft Noise | Niedrig | ⚠️ Schwach |
| **DOGE** | Schlecht — sentiment-getrieben | Zufällige Ausbrüche | Unzuverlässig | ❌ Schlecht |
| **SHIB/PEPE** | Nicht vorhanden | Keine echten S/R Zonen | Nicht verwendbar | ❌❌ Nicht geeignet |

### Empfohlene Kombinationen (Ranking)

| Rang | Kombination | Begründung |
|---|---|---|
| 🥇 1 | **BTC 4h** | Klarste institutionelle S/R-Levels, starke Breakouts mit Volumen |
| 🥇 1 | **ETH 4h** | Ähnlich BTC, gute MTF-EMA-Bias |
| 🥈 2 | **BTC 2h** | Mehr Trades als 4h, S/R noch ausreichend strukturiert |
| 🥈 2 | **SOL 2h / 4h** | Explosive Breakouts, klare Pivot-Struktur |
| 🥉 3 | **XRP 4h** | Klare historische S/R Levels |
| 4 | **AVAX 4h** | Gute Breakout-Qualität in Trending-Phasen |
| 4 | **ARB 4h** | ETH-korreliert, solide Ausbrüche |
| 4 | **BTC 1d** | Wenige aber sehr zuverlässige Breakouts |
| ❌ | **Alles auf 15m / 30m** | Zu viele Fake-Pivots, Volumen-Bestätigung bedeutungslos |
| ❌ | **DOGE / SHIB** | Keine validen S/R-Zonen vorhanden |

> **Hinweis:** Das Volumen-Filter (120% des 20-Kerzen-Durchschnitts) ist auf niedrig-liquiden Coins unzuverlässig. BTC und ETH liefern die konstanteste Signalqualität.


---

## Coin & Timeframe Empfehlungen

StBot ist eine **S/R-Breakout-Strategie** — er sucht dynamische Unterstützungs- und Widerstandszonen aus Pivot-Hochs/-Tiefs und tradet den Ausbruch. Benötigt: klare Pivot-Struktur, ausreichend Volumen für Bestätigung und erkennbaren Trend für den MTF-EMA-Bias-Filter (EMA20 vs EMA50).

### Effektive Zeitspannen je Timeframe

| TF | Pivot(20) — S/R Zonen | ATR(14) — SL | Vol-MA(20) | EMA20/50 — Bias | Geeignet |
|---|---|---|---|---|---|
| 15m | 5h | 3.5h | 5h | 5h / 12.5h | ❌ |
| 30m | 10h | 7h | 10h | 10h / 25h | ⚠️ |
| 1h | 20h | 14h | 20h | 20h / 50h | ✅ |
| **2h** | **40h** | **28h** | **40h** | **40h / 100h** | **✅✅** |
| **4h** | **80h** | **56h** | **80h** | **80h / 200h** | **✅✅** |
| 6h | 120h | 84h | 120h | 120h / 300h | ✅ |
| 1d | 20d | 14d | 20d | 20d / 50d | ✅ |

Auf 15m entstehen zu viele Fake-Pivots. Ab 2h haben S/R-Zonen ausreichend Testhistorie und Volumen-Bestätigung ist aussagekräftig. Die EMA20/50-Bias-Werte auf 4h (80h / 200h) haben industrielle Relevanz.

### Coin-Eignung

| Coin | S/R Struktur | Breakout-Qualität | Vol.-Zuverlässigkeit | Bewertung |
|---|---|---|---|---|
| **BTC** | Exzellent — institutionelle S/R Levels | Klare, bestätigte Breakouts | Sehr hohe Liquidität | ✅✅ Beste Wahl |
| **ETH** | Exzellent — ähnlich BTC | Saubere Ausbrüche | Sehr hohe Liquidität | ✅✅ Sehr gut |
| **SOL** | Sehr gut — klare Swing-Hochs/-Tiefs | Starke Breakouts mit Volumen | Gute Liquidität | ✅ Gut |
| **XRP** | Gut — klare historische Levels | Solide Breakouts | Hohe Liquidität | ✅ Gut |
| **BNB** | Gut — stabile Zonen | Moderate Breakout-Stärke | Gute Liquidität | ✅ Gut |
| **AVAX** | Gut — klare Swing-Struktur | Gute Breakouts in Bullphasen | Ausreichend | ✅ Gut |
| **ARB** | Gut — ETH-korreliert | Solide Ausbrüche | Ausreichend | ✅ Gut |
| **LTC** | Mittel — folgt BTC | Ausbrüche weniger dynamisch | Gut | ⚠️ Mittel |
| **LINK** | Mittel — S/R-Zonen vorhanden | Breakouts oft kurzlebig | Mittel | ⚠️ Mittel |
| **ADA** | Mittel — viele False Breakouts | Niedriges Volumen bei Ausbrüchen | Mittel | ⚠️ Mittel |
| **DOT** | Mittel — in Bullphasen klar | Phasenabhängig | Mittel | ⚠️ Mittel |
| **AAVE** | Schwach — kleine S/R-Zonen | Breakouts oft Noise | Niedrig | ⚠️ Schwach |
| **DOGE** | Schlecht — sentiment-getrieben | Zufällige Ausbrüche | Unzuverlässig | ❌ Schlecht |
| **SHIB/PEPE** | Nicht vorhanden | Keine echten S/R Zonen | Nicht verwendbar | ❌❌ Nicht geeignet |

### Empfohlene Kombinationen (Ranking)

| Rang | Kombination | Begründung |
|---|---|---|
| 🥇 1 | **BTC 4h** | Klarste institutionelle S/R-Levels, starke Breakouts mit Volumen |
| 🥇 1 | **ETH 4h** | Ähnlich BTC, gute MTF-EMA-Bias |
| 🥈 2 | **BTC 2h** | Mehr Trades als 4h, S/R noch ausreichend strukturiert |
| 🥈 2 | **SOL 2h / 4h** | Explosive Breakouts, klare Pivot-Struktur |
| 🥉 3 | **XRP 4h** | Klare historische S/R Levels |
| 4 | **AVAX 4h** | Gute Breakout-Qualität in Trending-Phasen |
| 4 | **ARB 4h** | ETH-korreliert, solide Ausbrüche |
| 4 | **BTC 1d** | Wenige aber sehr zuverlässige Breakouts |
| ❌ | **Alles auf 15m / 30m** | Zu viele Fake-Pivots, Volumen-Bestätigung bedeutungslos |
| ❌ | **DOGE / SHIB** | Keine validen S/R-Zonen vorhanden |

> **Hinweis:** Das Volumen-Filter (120% des 20-Kerzen-Durchschnitts) ist auf niedrig-liquiden Coins unzuverlässig. BTC und ETH liefern die konstanteste Signalqualität.


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
