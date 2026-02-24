#!/usr/bin/env python3
"""
auto_optimizer_scheduler.py

Prüft bei jedem Aufruf, ob eine automatische Optimierung fällig ist,
und führt die Pipeline aus (bash oder Python-Fallback).

Aufruf:
  python3 auto_optimizer_scheduler.py           # normale Prüfung
  python3 auto_optimizer_scheduler.py --force   # sofort erzwingen
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
SECRET_FILE      = os.path.join(PROJECT_ROOT, 'secret.json')
LAST_RUN_FILE    = os.path.join(CACHE_DIR, '.last_optimization_run')
IN_PROGRESS_FILE = os.path.join(CACHE_DIR, '.optimization_in_progress')
TRIGGER_LOG      = os.path.join(LOG_DIR, 'auto_optimizer_trigger.log')

# Lookback je Timeframe (wie in run_pipeline.sh)
LOOKBACK_MAP = {
    '5m': 60,  '15m': 60,
    '30m': 365, '1h': 365,
    '2h': 730,  '4h': 730,
    '6h': 1095, '1d': 1095, '1w': 1095,
}


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
# "auto"-Werte auflösen
# ---------------------------------------------------------------------------

def _resolve_symbols(value, live_settings: dict) -> list[str]:
    """'auto' → Symbole aus active_strategies ableiten (nur aktive)."""
    if value != 'auto':
        return value if isinstance(value, list) else [value]

    strategies = live_settings.get('active_strategies', [])
    symbols = []
    for s in strategies:
        if not s.get('active', True):
            continue
        sym = s.get('symbol', '')          # z.B. "BTC/USDT:USDT"
        base = sym.split('/')[0]           # → "BTC"
        if base and base not in symbols:
            symbols.append(base)
    return symbols or ['BTC', 'ETH']


def _resolve_timeframes(value, live_settings: dict) -> list[str]:
    """'auto' → Timeframes aus active_strategies ableiten (nur aktive)."""
    if value != 'auto':
        return value if isinstance(value, list) else [value]

    strategies = live_settings.get('active_strategies', [])
    tfs = []
    for s in strategies:
        if not s.get('active', True):
            continue
        tf = s.get('timeframe', '')
        if tf and tf not in tfs:
            tfs.append(tf)
    return tfs or ['1h', '4h']


def _resolve_lookback(value, timeframes: list[str]) -> int:
    """'auto' → höchsten Lookback-Wert der verwendeten Timeframes nehmen."""
    if value != 'auto':
        return int(value)
    return max((LOOKBACK_MAP.get(tf, 365) for tf in timeframes), default=365)


# ---------------------------------------------------------------------------
# Zeitplan-Prüfung
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

    if os.path.exists(IN_PROGRESS_FILE):
        _log("SKIP already_in_progress")
        return False, None

    last_run = _get_last_run()

    if last_run is None:
        return True, 'forced'

    # Intervall-Prüfung
    interval_cfg = schedule.get('interval', {})
    value = int(interval_cfg.get('value', 7))
    unit  = interval_cfg.get('unit', 'days')
    multipliers = {'minutes': 60, 'hours': 3600, 'days': 86400, 'weeks': 604800}
    interval_seconds = value * multipliers.get(unit, 86400)

    elapsed = (datetime.now() - last_run).total_seconds()
    if elapsed >= interval_seconds:
        return True, 'interval'

    # Wochentag/Uhrzeit-Prüfung
    now    = datetime.now()
    dow    = int(schedule.get('day_of_week', 0))
    hour   = int(schedule.get('hour', 3))
    minute = int(schedule.get('minute', 0))

    if now.weekday() == dow and now.hour == hour and minute <= now.minute < minute + 15:
        if last_run.date() < now.date():
            return True, 'scheduled'

    return False, None


# ---------------------------------------------------------------------------
# Telegram-Benachrichtigung
# ---------------------------------------------------------------------------

def _send_telegram(message: str):
    try:
        with open(SECRET_FILE, 'r') as f:
            secrets = json.load(f)

        account = secrets.get('stbot', [{}])[0]
        bot_token = account.get('telegram_bot_token') or account.get('bot_token')
        chat_id   = account.get('telegram_chat_id')  or account.get('chat_id')

        if not bot_token or not chat_id:
            _log("TELEGRAM SKIP no token/chat_id in secret.json")
            return

        from stbot.utils.telegram import send_message
        send_message(bot_token, chat_id, message)
        _log("TELEGRAM sent")
    except Exception as e:
        _log(f"TELEGRAM ERROR {e}")


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


def _run_python_fallback(symbols: list, timeframes: list, lookback: int,
                         opt_settings: dict) -> int:
    """Direkter Python-Aufruf des Optimizers als Fallback."""
    python_exe = sys.executable
    _log(f"FALLBACK method=python interpreter={python_exe}")

    start_date = (date.today() - timedelta(days=lookback)).strftime('%Y-%m-%d')
    end_date   = date.today().strftime('%Y-%m-%d')
    constraints = opt_settings.get('constraints', {})

    cmd = [
        python_exe, OPTIMIZER_SCRIPT,
        '--symbols',       ' '.join(symbols),
        '--timeframes',    ' '.join(timeframes),
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


def run_optimization(schedule: dict, opt_settings: dict, live_settings: dict,
                     reason: str):
    """Führt die Optimierungs-Pipeline aus."""
    os.makedirs(CACHE_DIR, exist_ok=True)

    # "auto"-Werte auflösen
    symbols    = _resolve_symbols(opt_settings.get('symbols_to_optimize', 'auto'), live_settings)
    timeframes = _resolve_timeframes(opt_settings.get('timeframes_to_optimize', 'auto'), live_settings)
    lookback   = _resolve_lookback(opt_settings.get('lookback_days', 'auto'), timeframes)

    _log(f"START reason={reason} scheduled={json.dumps(schedule)} last_run={_get_last_run()}")
    _log(f"CONFIG symbols={symbols} timeframes={timeframes} lookback_days={lookback}")

    with open(IN_PROGRESS_FILE, 'w') as f:
        f.write(datetime.now().isoformat())

    start_time = time.time()
    success    = False

    try:
        rc = _run_bash_pipeline()

        if rc != 0:
            _log("PIPELINE_WARNING Bash exit != 0 — attempting Python fallback")
            rc = _run_python_fallback(symbols, timeframes, lookback, opt_settings)

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

        if opt_settings.get('send_telegram_on_completion', False):
            _send_telegram(
                f"StBot Auto-Optimizer abgeschlossen\n"
                f"Symbole: {', '.join(symbols)}\n"
                f"Timeframes: {', '.join(timeframes)}\n"
                f"Dauer: {elapsed}s"
            )
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

    opt_settings  = settings.get('optimization_settings', {})
    live_settings = settings.get('live_trading_settings', {})

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

    run_optimization(schedule, opt_settings, live_settings, reason)


if __name__ == '__main__':
    main()
