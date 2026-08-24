# src/stbot/analysis/portfolio_optimizer.py (Portfolio-Optimierer mit MaxDD Constraint & Coin-Kollisionsschutz)
import pandas as pd
import itertools
import threading
import time
from tqdm import tqdm
import sys
import os
import json # Fürs Speichern
import numpy as np # Für np.nan

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from stbot.analysis.portfolio_simulator import run_portfolio_simulation

try:
    import resource  # POSIX-only (Linux/WSL) -- auf dem produktiven VPS immer verfuegbar
    def _current_rss_mb():
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
except ImportError:
    def _current_rss_mb():  # z.B. bei lokalen Tests unter Windows
        return 0.0

# Sicherheitsgrenze fuer den eigenen Speicherverbrauch. Live beobachtet
# (2026-08-24, Mini-PC/VPS mit 6.3GB RAM): dieser Optimierer lief auf ~5.7GB
# RSS ein, WAEHREND parallel per Cron alle 5 Minuten ein Fleet aus 9 weiteren
# Live-Bots (master_runner.py je Bot) anspringt -- der kombinierte Speicher-
# druck loeste den Kernel-OOM-Killer aus, der den kompletten VPS inkl. aller
# offenen Terminalfenster mitgerissen hat. Da dieser Optimierer zwingend
# automatisiert/unbeaufsichtigt (woechentlich per Cron) auf genau diesem VPS
# laufen soll, MUSS er sich selbst begrenzen, statt auf einen harten Kernel-
# Abbruch zu vertrauen: ueberschreitet der eigene Prozess dieses Limit,
# bricht die Suche kontrolliert mit dem bisher besten Ergebnis ab, statt den
# ganzen VPS mitzureissen. 3500MB laesst auf 6.3GB Gesamt-RAM ausreichend
# Puffer fuer OS + das parallele Bot-Fleet (das selbst nur ca. 150-200MB
# braucht, siehe ps-aux-Messung vom selben Tag).
MAX_RSS_MB = 3500


def _check_memory_available(min_available_mb=1500, max_wait_s=180, poll_interval_s=15):
    """Vorflug-Check: liest MemAvailable aus /proc/meminfo (Linux/WSL) und
    wartet kurz, falls das parallele Bot-Fleet gerade selbst einen Lastpeak
    hat. Gibt True zurueck, wenn genug Speicher frei ist (oder der Check auf
    einem Nicht-Linux-System schlicht nicht moeglich ist -- dann optimistisch
    weiterlaufen), sonst False nach Ablauf von max_wait_s."""
    meminfo_path = '/proc/meminfo'
    if not os.path.exists(meminfo_path):
        return True
    waited = 0
    while waited <= max_wait_s:
        try:
            with open(meminfo_path) as f:
                for line in f:
                    if line.startswith('MemAvailable:'):
                        available_mb = int(line.split()[1]) / 1024
                        if available_mb >= min_available_mb:
                            return True
                        break
        except Exception:
            return True  # Check selbst fehlgeschlagen -- nicht blockieren
        if waited >= max_wait_s:
            break
        time.sleep(poll_interval_s)
        waited += poll_interval_s
    return False


class _LiveTicker:
    """Haelt eine tqdm-Leiste am Leben (aktualisiert die {elapsed}-Anzeige),
    auch waehrend eines einzelnen, mehrere Minuten dauernden Simulations-
    aufrufs zwischen zwei Iterationen -- ohne das faellt die Leiste zwischen
    zwei Schritten in eine eingefrorene Anzeige. Schreibt auf dieselbe Zeile
    (kein Zeilen-Spam). Optional laesst sich per status_fn() bei jedem Tick
    ein echter Fortschritts-Status ins Postfix schreiben (z.B. welcher Tag
    an Feindaten gerade bei LazyFineData nachgeladen wird) -- ohne das sieht
    ein einzelner, minutenlanger Simulationsaufruf (z.B. bei Bitget-Rate-
    Limiting) wie ein Haenger aus, obwohl er nur langsam vorankommt."""
    def __init__(self, pbar, status_fn=None, interval=0.5):
        self._pbar = pbar
        self._status_fn = status_fn
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.wait(self._interval):
            if self._status_fn is not None:
                try:
                    status = self._status_fn()
                    if status:
                        self._pbar.set_postfix_str(status)
                except Exception:
                    pass
            self._pbar.refresh()

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=1)

# *** Angepasst: Nimmt target_max_dd entgegen ***
def run_portfolio_optimizer(start_capital, strategies_data, start_date, end_date, target_max_dd: float):
    """
    Findet die Kombination von Strategien, die das höchste Endkapital liefert,
    während der maximale Drawdown unter dem Zielwert (`target_max_dd`) bleibt UND jeder Coin nur einmal vorkommt.
    Verwendet einen modifizierten Greedy-Algorithmus.
    """
    print(f"\n--- Starte automatische Portfolio-Optimierung mit Max DD <= {target_max_dd:.2f}% & ohne Coin-Kollisionen ---")
    target_max_dd_decimal = target_max_dd / 100.0 # Umrechnung in Dezimalzahl für Vergleiche

    if not strategies_data:
        print("Keine Strategien zum Optimieren gefunden.")
        return None

    # Vorflug-Check: laeuft das parallele Bot-Fleet (Cron, alle 5 Min) gerade
    # selbst in einem Lastpeak, warte kurz statt direkt in eine bereits knappe
    # Speichersituation hineinzustarten (siehe MAX_RSS_MB-Kommentar oben).
    if not _check_memory_available():
        print("  Zu wenig freier Speicher (< 1.5GB) nach Wartezeit -- "
              "Portfolio-Optimierung diese Woche uebersprungen, bestehendes "
              "Portfolio bleibt unveraendert.")
        return {"optimal_portfolio": [], "final_result": None, "skipped_low_memory": True}

    # --- 1. Analysiere Einzel-Performance & filtere nach Max DD ---
    print("1/3: Analysiere Einzel-Performance & filtere nach Max DD...")
    single_strategy_results = []

    pbar = tqdm(strategies_data.items(), desc="Bewerte Einzelstrategien")
    _ctx = {}
    def _status():
        fd = _ctx.get('fine_data')
        if fd is not None and getattr(fd, 'current_day', None):
            return f"{_ctx.get('label', '')} | Feindaten {fd.current_day} ({fd.days_loaded} Tage geladen)"
        return _ctx.get('label', '')
    with _LiveTicker(pbar, status_fn=_status):
      for filename, strat_data in pbar:
        if _current_rss_mb() > MAX_RSS_MB:
            print(f"\n  Speicherlimit ({MAX_RSS_MB}MB) erreicht -- breche Einzelanalyse "
                  f"vorzeitig ab (bisherige {len(single_strategy_results)} Ergebnisse bleiben erhalten).")
            break
        _ctx['label'] = f"{strat_data['symbol']} {strat_data['timeframe']}"
        _ctx['fine_data'] = strat_data.get('fine_data')
        pbar.set_postfix_str(_ctx['label'])
        strategy_key = f"{strat_data['symbol']}_{strat_data['timeframe']}"
        sim_data = {strategy_key: strat_data}
        if 'data' not in strat_data or strat_data['data'].empty:
            print(f"WARNUNG: Keine Daten für {filename} in Einzelanalyse.")
            continue

        result = run_portfolio_simulation(start_capital, sim_data, start_date, end_date, verbose=False)

        if result and not result.get("liquidation_date"):
            # Max DD aus Ergebnis holen (als Dezimalzahl)
            # Nutze 1.0 (100%) als Fallback, wenn Schlüssel fehlt
            actual_max_dd = result.get('max_drawdown_pct', 100.0) / 100.0

            # *** NEU: Filter nach target_max_dd ***
            if actual_max_dd <= target_max_dd_decimal:
                # Füge nur Strategien hinzu, die die Bedingung erfüllen
                single_strategy_results.append({
                    'filename': filename,
                    'result': result # Speichere das vollständige Ergebnis
                })
            # else:
                # Optional: Logge verworfene Strategien
                # print(f"Info: Einzelstrategie {filename} verworfen (Max DD {actual_max_dd*100:.2f}% > {target_max_dd:.2f}%)")
        # else:
            # Optional: Logge liquidierte Strategien
            # print(f"Info: Einzelstrategie {filename} führte zur Liquidation.")


    if not single_strategy_results:
        print(f"Keine einzige Strategie erfüllte die Bedingung Max DD <= {target_max_dd:.2f}%. Portfolio-Optimierung nicht möglich.")
        return {"optimal_portfolio": [], "final_result": None} # Gebe leeres Ergebnis zurück

    # --- 2. Finde den "Star-Spieler" basierend auf HÖCHSTEM PROFIT unter den gefilterten ---
    # Sortiere nach Endkapital (absteigend)
    single_strategy_results.sort(key=lambda x: x['result']['end_capital'], reverse=True)

    best_portfolio_files = [single_strategy_results[0]['filename']]
    best_portfolio_result = single_strategy_results[0]['result']
    best_end_capital = best_portfolio_result['end_capital'] # Merke dir das beste Kapital

    # Pool der verbleibenden Kandidaten (alle, außer dem besten)
    candidate_pool = [res['filename'] for res in single_strategy_results[1:]]

    print(f"2/3: Beste Einzelstrategie (unter Max DD): {best_portfolio_files[0]} (Endkapital: {best_end_capital:.2f} USDT, Max DD: {best_portfolio_result['max_drawdown_pct']:.2f}%)")
    print("3/3: Suche die besten Team-Kollegen...")

    # --- 3. Greedy-Algorithmus: Füge schrittweise die Strategie hinzu, die den Profit MAXIMIERT, ohne Max DD zu verletzen UND ohne Coin-Kollision ---

    selected_coins = set() # NEU: Set für bereits ausgewählte Coins
    # Füge den Coin der besten Einzelstrategie hinzu (falls vorhanden)
    if best_portfolio_files: # NEU
        initial_best_strat_data = strategies_data.get(best_portfolio_files[0]) # NEU
        if initial_best_strat_data: # NEU
            # Extrahiere Coin-Symbol (z.B. BTC aus BTC/USDT:USDT)
            initial_coin = initial_best_strat_data['symbol'].split('/')[0] # NEU
            selected_coins.add(initial_coin) # NEU

    while True:
        if _current_rss_mb() > MAX_RSS_MB:
            print(f"\n  Speicherlimit ({MAX_RSS_MB}MB) erreicht -- breche Team-Suche vorzeitig ab "
                  f"(bisheriges Team bleibt: {best_portfolio_files}).")
            break

        best_next_addition = None
        best_capital_with_addition = best_end_capital # Starte mit dem Kapital des aktuellen besten Portfolios
        current_best_result_for_addition = best_portfolio_result # Merke dir das Ergebnis dieser Runde

        progress_bar = tqdm(candidate_pool, desc=f"Teste Team mit {len(best_portfolio_files)+1} Mitgliedern")
        _ctx = {}
        def _status():
            fd = _ctx.get('fine_data')
            if fd is not None and getattr(fd, 'current_day', None):
                return f"{_ctx.get('label', '')} | Feindaten {fd.current_day} ({fd.days_loaded} Tage geladen)"
            return _ctx.get('label', '')
        ticker = _LiveTicker(progress_bar, status_fn=_status)
        ticker.__enter__()
        for candidate_file in progress_bar:
            if _current_rss_mb() > MAX_RSS_MB:
                print(f"\n  Speicherlimit ({MAX_RSS_MB}MB) erreicht -- breche diese Runde vorzeitig ab.")
                break

            # --- START: NEUER CODE ZUR KOLLISIONSPRÜFUNG ---
            candidate_strat_data = strategies_data.get(candidate_file)
            if not candidate_strat_data:
                continue # Überspringe, falls Daten für Kandidat fehlen

            _ctx['label'] = f"{candidate_strat_data['symbol']} {candidate_strat_data['timeframe']}"
            _ctx['fine_data'] = candidate_strat_data.get('fine_data')
            progress_bar.set_postfix_str(_ctx['label'])
            candidate_coin = candidate_strat_data['symbol'].split('/')[0]

            # Prüfe, ob der Coin dieses Kandidaten bereits im Portfolio ist
            if candidate_coin in selected_coins:
                continue # Überspringe diesen Kandidaten, da der Coin schon vorhanden ist
            # --- ENDE: NEUER CODE ---

            # Bestehender Code:
            current_team_files = best_portfolio_files + [candidate_file]

            # Eindeutigkeitsprüfung (gleicher Coin/Timeframe - sollte durch obige Prüfung unnötig sein, aber sicher ist sicher)
            unique_check = set()
            is_valid_team = True
            for f in current_team_files:
                strat_info = strategies_data.get(f)
                if not strat_info: is_valid_team = False; break
                key = strat_info['symbol'] + strat_info['timeframe']
                if key in unique_check: is_valid_team = False; break
                unique_check.add(key)
            if not is_valid_team: continue

            # Daten für Simulator zusammenstellen
            current_team_data = {}
            valid_data_for_sim = True
            for fname in current_team_files:
                strat_d = strategies_data.get(fname)
                if strat_d and 'data' in strat_d and not strat_d['data'].empty:
                    sim_key = f"{strat_d['symbol']}_{strat_d['timeframe']}"
                    current_team_data[sim_key] = strat_d
                else:
                    valid_data_for_sim = False; break
            if not valid_data_for_sim: continue

            # Portfolio simulieren
            result = run_portfolio_simulation(start_capital, current_team_data, start_date, end_date, verbose=False)

            # Prüfen ob Ergebnis gültig UND Max DD eingehalten wird
            if result and not result.get("liquidation_date"):
                actual_max_dd = result.get('max_drawdown_pct', 100.0) / 100.0

                # *** NEUE BEDINGUNG: Prüfe Max DD UND ob Endkapital besser ist ***
                if actual_max_dd <= target_max_dd_decimal and result['end_capital'] > best_capital_with_addition:
                    # Dieses Team ist besser als das bisher beste dieser Runde
                    best_capital_with_addition = result['end_capital']
                    best_next_addition = candidate_file
                    current_best_result_for_addition = result # Aktualisiere das beste Ergebnis dieser Runde

        ticker.__exit__(None, None, None)

        # Prüfe, ob eine Verbesserung gefunden wurde (best_next_addition ist nicht None)
        if best_next_addition:
            # Eine bessere Kombination wurde gefunden
            print(f"-> Füge hinzu: {best_next_addition} (Neues Kapital: {best_capital_with_addition:.2f} USDT, Max DD: {current_best_result_for_addition['max_drawdown_pct']:.2f}%)")
            best_portfolio_files.append(best_next_addition)

            # --- START: NEUER CODE ZUM AKTUALISIEREN DES SETS ---
            added_strat_data = strategies_data.get(best_next_addition)
            if added_strat_data:
                added_coin = added_strat_data['symbol'].split('/')[0]
                selected_coins.add(added_coin)
            # --- ENDE: NEUER CODE ---

            # Bestehender Code:
            best_end_capital = best_capital_with_addition # Aktualisiere globales bestes Kapital
            best_portfolio_result = current_best_result_for_addition # Übernehme das beste Ergebnis
            candidate_pool.remove(best_next_addition) # Entferne aus Kandidaten
        else:
            # Keine weitere Verbesserung durch Hinzufügen möglich oder alle Kandidaten verletzen Max DD/Coin-Constraint
            print("Keine weitere Verbesserung des Profits (unter Einhaltung des Max DD & ohne Coin-Kollision) durch Hinzufügen von Strategien gefunden. Optimierung beendet.")
            break # Verlasse die while-Schleife

    # --- Ergebnisse speichern ---
    try:
        results_dir = os.path.join(PROJECT_ROOT, 'artifacts', 'results')
        os.makedirs(results_dir, exist_ok=True)
        output_path = os.path.join(results_dir, 'optimization_results.json')
        # Speichere die Dateinamen des finalen Portfolios
        save_data = {"optimal_portfolio": best_portfolio_files}
        with open(output_path, 'w') as f:
            json.dump(save_data, f, indent=4)
        print(f"Optimales Portfolio (Max DD <= {target_max_dd:.2f}%) in '{output_path}' gespeichert.")
    except Exception as e:
        print(f"Fehler beim Speichern der Optimierungsergebnisse: {e}")


    # Gib das finale beste Portfolio und sein Ergebnis zurück
    return {"optimal_portfolio": best_portfolio_files, "final_result": best_portfolio_result}
