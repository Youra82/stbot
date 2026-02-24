#!/usr/bin/env python3
"""
auto_optimizer_scheduler.py

Prüft bei jedem Aufruf, ob eine automatische Optimierung fällig ist,
und führt die Pipeline aus (bash oder Python-Fallback).

Aufruf:
  python3 auto_optimizer_scheduler.py           # normale Prüfung
  python3 auto_optimizer_scheduler.py --force   # erzwingt sofortige Optimierung
"""

import os
import sys
import json
import time
import subprocess
import argparse
from datetime import datetime, date, timedelta

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

CACHE_DIR        = os.path.join(PROJECT_ROOT, 'data', 'cache')
LOG_DIR          = os.path.join(PROJECT_ROOT, 'logs')
SETTINGS_FILE    = os.path.join(PROJECT_ROOT, 'settings.json')
OPTIMIZER_SCRIPT = os.path.join(PROJECT_ROOT, 'src', 'stbot', 'analysis', 'optimizer.py')
LAST_RUN_FILE    = os.path.join(CACHE_DIR, '.last_optimization_run')
IN_PROGRESS_FILE = os.path.join(CACHE_DIR, '.optimization_in_progress')
TRIGGER_LOG      = os.path.join(LOG_DIR, 'auto_optimizer_trigger.log')


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _log(msg: str):
    os.makedirs(LOG_DIR, exist_ok=True)
    line = f"{datetime.now().isoformat()} AUTO-OPTIMIZER {msg}"
    with open(TRIGGER_LOG, 'a', encoding='utf-8') as f:
        f.write(line + '\n')
    print(line, flush=True)


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _get_last_run() -> datetime | None:
    if not os.path.exists(LAST_RUN_FILE):
        return None
    with open(LAST_RUN_FILE, 'r') as f:
        s = f.read().strip()
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _set_last_run():
    os.makedirs(CACHE_DIR, exist_ok=True)
    now_str = datetime.now().isoformat()
    with open(LAST_RUN_FILE, 'w') as f:
        f.write(now_str)
    _log(f"LAST_RUN updated={now_str}")


def _is_due(schedule: dict) -> tuple[bool, str]:
    """Gibt (fällig, grund) zurück."""

    # Läuft gerade?
    if os.path.exists(IN_PROGRESS_FILE):
        _log("SKIP already_in_progress")
        return False, None

    last_run = _get_last_run()

    # Noch nie gelaufen → sofort
    if last_run is None:
        return True, 'forced'

    # --- Intervall-Prüfung (hat Vorrang) ---
    interval_cfg = schedule.get('interval', {})
    value = int(interval_cfg.get('value', 7))
    unit  = interval_cfg.get('unit', 'days')
    multipliers = {'minutes': 60, 'hours': 3600, 'days': 86400, 'weeks': 604800}
    interval_seconds = value * multipliers.get(unit, 86400)

    elapsed = (datetime.now() - last_run).total_seconds()
    if elapsed >= interval_seconds:
        return True, 'interval'

    # --- Wochentag/Uhrzeit-Prüfung ---
    now = datetime.now()
    dow    = int(schedule.get('day_of_week', 0))
    hour   = int(schedule.get('hour', 3))
    minute = int(schedule.get('minute', 0))

    if now.weekday() == dow and now.hour == hour and minute <= now.minute < minute + 15:
        if last_run.date() < now.date():
            return True, 'scheduled'

    return False, None


# ---------------------------------------------------------------------------
# Pipeline-Ausführung
# ---------------------------------------------------------------------------

def _run_bash_pipeline() -> int:
    cmd = ['bash', '-lc', f"cd '{PROJECT_ROOT}' && ./run_pipeline_automated.sh"]
    _log(f"PIPELINE_EXEC method=bash cmd={cmd}")
    result = subprocess.run(cmd)
    rc = result.returncode
    _log(f"PIPELINE_EXIT rc={rc}")
    return rc


def _run_python_fallback(opt_settings: dict) -> int:
    """Direkter Python-Aufruf des Optimizers (Fallback wenn bash nicht verfügbar)."""
    python_exe = sys.executable
    _log(f"FALLBACK method=python interpreter={python_exe}")

    lookback = int(opt_settings.get('lookback_days', 365))
    start_date = (date.today() - timedelta(days=lookback)).strftime('%Y-%m-%d')
    end_date   = date.today().strftime('%Y-%m-%d')

    symbols    = opt_settings.get('symbols_to_optimize', ['BTC', 'ETH'])
    timeframes = opt_settings.get('timeframes_to_optimize', ['1h', '4h'])
    if isinstance(symbols, list):
        symbols = ' '.join(symbols)
    if isinstance(timeframes, list):
        timeframes = ' '.join(timeframes)

    constraints = opt_settings.get('constraints', {})

    cmd = [
        python_exe, OPTIMIZER_SCRIPT,
        '--symbols',       symbols,
        '--timeframes',    timeframes,
        '--start_date',    start_date,
        '--end_date',      end_date,
        '--jobs',          str(opt_settings.get('cpu_cores', -1)),
        '--max_drawdown',  str(constraints.get('max_drawdown_pct', 30)),
        '--start_capital', str(opt_settings.get('start_capital', 1000)),
        '--min_win_rate',  str(constraints.get('min_win_rate_pct', 50)),
        '--trials',        str(opt_settings.get('num_trials', 100)),
        '--min_pnl',       str(constraints.get('min_pnl_pct', 0)),
        '--mode',          opt_settings.get('mode', 'strict'),
    ]

    result = subprocess.run(cmd)
    rc = result.returncode
    _log(f"PIPELINE_EXIT rc={rc}")
    return rc


def run_optimization(schedule: dict, opt_settings: dict, reason: str):
    """Führt die Optimierungs-Pipeline aus."""
    os.makedirs(CACHE_DIR, exist_ok=True)

    _log(f"START reason={reason} scheduled={json.dumps(schedule)} last_run={_get_last_run()}")

    # In-Progress-Marker setzen
    with open(IN_PROGRESS_FILE, 'w') as f:
        f.write(datetime.now().isoformat())

    start_time = time.time()
    success    = False

    try:
        rc = _run_bash_pipeline()

        if rc != 0:
            _log("PIPELINE_WARNING Bash exit != 0 — attempting Python fallback")
            rc = _run_python_fallback(opt_settings)

        success = (rc == 0)

    except Exception as e:
        _log(f"ERROR {e}")

    finally:
        if os.path.exists(IN_PROGRESS_FILE):
            os.remove(IN_PROGRESS_FILE)
            print(f"DEBUG: cleared in-progress marker {IN_PROGRESS_FILE}", flush=True)

    elapsed = round(time.time() - start_time, 1)

    if success:
        _set_last_run()
        _log(f"FINISH result=success elapsed_s={elapsed}")
        print("Optimizer finished successfully; updated last-run timestamp.", flush=True)
    else:
        _log(f"FINISH result=failed elapsed_s={elapsed}")


# ---------------------------------------------------------------------------
# Einstiegspunkt
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='StBot Auto-Optimizer Scheduler')
    parser.add_argument('--force', action='store_true',
                        help='Optimierung sofort erzwingen (ignoriert Zeitplan)')
    args = parser.parse_args()

    try:
        with open(SETTINGS_FILE, 'r') as f:
            settings = json.load(f)
    except Exception as e:
        print(f"Fehler beim Lesen der settings.json: {e}")
        return

    opt_settings = settings.get('optimization_settings', {})

    if not opt_settings.get('enabled', False) and not args.force:
        print("Auto-Optimierung deaktiviert (optimization_settings.enabled=false).")
        return

    schedule = opt_settings.get('schedule', {
        '_info':       'day_of_week: 0=Montag, 6=Sonntag | hour: 0-23 (24h Format)',
        'day_of_week': 0,
        'hour':        3,
        'minute':      0,
        'interval': {
            '_info': 'Einheit: minutes | hours | days | weeks',
            'value': 7,
            'unit':  'days',
        },
    })

    if args.force:
        reason = 'forced'
    else:
        due, reason = _is_due(schedule)
        if not due:
            print("Optimierung noch nicht fällig.")
            return

    run_optimization(schedule, opt_settings, reason)


if __name__ == '__main__':
    main()
