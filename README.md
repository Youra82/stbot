Absolut. Hier ist die angepasste README.md-Datei für deinen neuen stbot.
Ich habe die Strategiebeschreibung, die Architektur-Details und alle relevanten Befehle aktualisiert, um deinen neuen Bot exakt widerzuspiegeln, während die von dir gewünschte Ordnerstruktur beibehalten wird.
Stoch RSI Trading Bot (stbot)
Dies ist ein vollautomatischer Trading-Bot für Krypto-Futures auf der Bitget-Börse. Das System wurde für den Betrieb auf einem Ubuntu-Server entwickelt und umfasst neben dem Live-Trading-Modul eine hochentwickelte Pipeline zur Strategie-Optimierung und -Analyse.
Kernstrategie
Der Bot implementiert eine Stochastik RSI Umkehrstrategie (Reversal Strategy).
Handelsthese: Nachdem der Preis eines Assets einen überkauften oder überverkauften Zustand erreicht hat, neigt er dazu, eine Gegenbewegung zum entgegengesetzten Extrem einzuleiten. Der Bot versucht, diese vollständige Bewegung von einem Extrem zum anderen zu handeln.
Signale:
 * Long-Einstieg: Der Stoch RSI ist im überverkauften Bereich (Standard: < 20) UND die %K-Linie kreuzt die %D-Linie von unten nach oben.
 * Short-Einstieg: Der Stoch RSI ist im überkauften Bereich (Standard: > 80) UND die %K-Linie kreuzt die %D-Linie von oben nach unten.
 * Ausstieg:
   * Take-Profit: Eine Long-Position wird geschlossen, sobald der Markt den überkauften Bereich erreicht (> 80). Eine Short-Position wird geschlossen, sobald der Markt den überverkauften Bereich erreicht (< 20).
   * Stop-Loss: Jede Position wird durch einen Stop-Loss abgesichert, der knapp unter/über dem letzten lokalen Tief-/Hochpunkt ("Swing Point") platziert wird.
Dynamisches Risiko & Schutzfilter:
 * Dynamischer Hebel: Der eingesetzte Hebel wird automatisch auf Basis der aktuellen Marktvolatilität (gemessen durch die ATR - Average True Range) angepasst, um das Risiko zu steuern.
 * Optionale Schutzfilter: Die Strategie enthält zwei zuschaltbare Filter zur Verbesserung der Signalqualität:
   * EMA-Trendfilter: Verhindert Trades gegen den übergeordneten Trend (definiert durch den 200er EMA).
   * Seitwärts-Filter: Erkennt unruhige Seitwärtsphasen mit vielen Fehlsignalen und pausiert den Handel.
Systemarchitektur
Das Projekt ist in drei Kernkomponenten unterteilt:
 * Live-Trading-Modul (/code/strategies/envelope)
   * Hinweis: Der Ordnername ist aus historischen Gründen envelope, enthält aber die komplette Logik für den stbot.
   * Der eigentliche Bot (run.py), der per Cronjob ausgeführt wird.
   * Verwaltet seinen Zustand über eine lokale SQLite-Datenbank.
   * Kommuniziert über ein Utility-Modul (bitget_futures.py) mit der Bitget API.
   * Versendet Status-Updates und kritische Fehler per Telegram.
 * Optimierungs- & Analyse-Pipeline (/code/analysis)
   * Ein leistungsstarkes Werkzeug (run_optimization_pipeline.sh), um die besten Strategie-Parameter für den stbot zu finden.
   * Zweistufige Optimierung:
     * Globale Suche (Pymoo): Ein genetischer Algorithmus durchsucht den gesamten Parameterraum.
     * Lokale Verfeinerung (Optuna): Die besten Kandidaten werden perfektioniert.
   * Filter-Abfrage: Fragt vor der Optimierung, welche Schutzfilter (Trend/Seitwärts) aktiviert sein sollen.
   * Dedizierter Backtest-Modus: Ermöglicht das gezielte Testen einer config.json-Datei gegen historische Daten.
 * Performance-Analyse (/code/utilities/tax_endpoint_analysis.py)
   * Ein Jupyter Notebook (run_pnl.ipynb) nutzt dieses Modul, um die tatsächliche Handels-Performance direkt vom Steuer-Endpunkt der Börse abzurufen.
Installation
Führe die folgenden Schritte auf einem frischen Ubuntu-Server (empfohlen: 22.04 LTS) aus.
 * Projekt klonen
   git clone https://github.com/Youra82/LiveTradingBots.git

 * Installations-Skript ausführen
   cd LiveTradingBots
chmod +x install.sh
./install.sh

 * API-Schlüssel eintragen
   nano secret.json

   Fülle die Felder für envelope (deine Live-Bitget-Keys) und optional für telegram aus. Speichere mit Strg + X, dann Y, dann Enter.
 * Strategie finden und konfigurieren
   chmod +x run_optimization_pipeline.sh
./run_optimization_pipeline.sh

   * Wähle im Menü Option 1.
   * Folge den Anweisungen (Handelspaar, Zeitraum, Filter-Aktivierung etc.).
   * Kopiere am Ende die ausgegebene Konfiguration in die Datei code/strategies/envelope/config.json.
 * Automatisierung per Cronjob einrichten
   crontab -e

   Füge die folgende Zeile am Ende der Datei ein. Wichtig: Der Log-Dateiname wurde zu stbot.log geändert.
   */5 * * * * flock -n /home/ubuntu/LiveTradingBots/bot.lock bash /home/ubuntu/LiveTradingBots/run_envelope.sh >> /home/ubuntu/LiveTradingBots/logs/stbot.log 2>&1

   Speichere und schließe die Datei. Der Bot ist nun live.
Bot-Verwaltung & Analyse
Diese Befehle werden im Hauptverzeichnis /home/ubuntu/LiveTradingBots ausgeführt.
 * Bot-Code aktualisieren
   Dieses Skript lädt die neueste Version des Codes von GitHub herunter.
   chmod +x update_bot.sh  # (einmalig)
./update_bot.sh

 * Strategien finden & testen
   Deine Steuerzentrale für alle Offline-Analysen.
   ./run_optimization_pipeline.sh

   * Option 1: Startet die komplette 2-Stufen-Optimierung für den stbot.
   * Option 2: Startet einen Einzel-Backtest mit der aktuellen Strategie in config.json.
   * Option 3: Löscht die zwischengespeicherten Marktdaten.
 * Live-Logs ansehen
   Zeigt die Aktivitäten des stbot in Echtzeit an.
   tail -f logs/stbot.log

   Mit Strg + C beendest du die Anzeige.
 * Automatisierung stoppen/starten
   crontab -e

   Setze ein #-Zeichen an den Anfang der Zeile, um den Bot zu pausieren. Entferne es, um ihn wieder zu starten.
 * (Optional) Ersten Lauf manuell starten
   bash run_envelope.sh

✅ Requirements
Python 3.10+
See requirements.txt for the specific Python packages.
📃 License
This project is licensed under the GNU General Public License - see the LICENSE file for details.
⚠️ Disclaimer
All this material is for educational and entertainment purposes only. It is not financial advice nor an endorsement of any provider, product or service. The user bears sole responsibility for any actions taken based on this information, and the authors will not be held liable for any losses or damages resulting from its use.
