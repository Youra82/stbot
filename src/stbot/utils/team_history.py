"""Teamhistorie: welche Strategien waren zu welchem Zeitpunkt live aktiv.

master_runner.py ruft record_team() bei jedem Lauf mit dem Team auf, das er tatsaechlich
handelt. Ein neuer Eintrag entsteht nur, wenn sich Team oder Config-Inhalt geaendert hat --
egal ob durch die Wochenauswahl, update.sh oder eine manuelle Aenderung.
Ein Eintrag gilt ab valid_from bis zum valid_from des naechsten Eintrags.

Datei: artifacts/db/team_history.jsonl (gitignored, eine JSON-Zeile je Teamwechsel).
Fehlt sie, wird sie einmalig aus team_history_seed.jsonl angelegt (nachgetragene Teams vor
Einfuehrung der Aufzeichnung, ohne Config-Pruefsumme).
"""
import hashlib
import json
import os
from datetime import datetime, timezone

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
HISTORY_FILE = os.path.join(ROOT, 'artifacts', 'db', 'team_history.jsonl')
CONFIGS_DIR = os.path.join(ROOT, 'src', 'stbot', 'strategy', 'configs')
SEED_FILE = os.path.join(os.path.dirname(__file__), 'team_history_seed.jsonl')


def strategy_key(symbol: str, timeframe: str) -> str:
    """'ENA/USDT:USDT', '15m' -> 'ENAUSDTUSDT_15m' (wie die Config-Dateinamen)."""
    return f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"


def _config_hash(key: str) -> str | None:
    path = os.path.join(CONFIGS_DIR, f'config_{key}.json')
    if not os.path.exists(path):
        return None
    with open(path, 'rb') as f:
        return hashlib.sha1(f.read()).hexdigest()[:12]


def _ensure_seeded(path: str) -> None:
    """Echte Historie fehlt noch -> einmalig mit den nachgetragenen Teams anlegen."""
    if path == HISTORY_FILE and not os.path.exists(path) and os.path.exists(SEED_FILE):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(SEED_FILE, encoding='utf-8') as src, open(path, 'w', encoding='utf-8') as dst:
            dst.write(src.read())


def load_history(path: str = HISTORY_FILE) -> list:
    _ensure_seeded(path)
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def record_team(keys: list, source: str, path: str = HISTORY_FILE, now: datetime | None = None,
                force: bool = False) -> bool:
    """Haengt einen Eintrag an, wenn Team oder Config-Inhalt vom letzten Eintrag abweicht.
    force=True schreibt auch ein unveraendertes Team (Wochenauswahl hat bestaetigt)."""
    team = {k: _config_hash(k) for k in sorted(set(keys))}
    history = load_history(path)
    if history and history[-1]['team'] == team and not force:
        return False
    entry = {'valid_from': (now or datetime.now(timezone.utc)).isoformat(timespec='seconds'),
             'source': source, 'team': team}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry) + '\n')
    return True


def team_at(ts, path: str = HISTORY_FILE) -> dict | None:
    """Team (Key -> Config-Hash), das zum Zeitpunkt ts aktiv war; None vor der ersten Aufzeichnung."""
    ts = pd.Timestamp(ts)
    ts = ts.tz_localize('UTC') if ts.tzinfo is None else ts.tz_convert('UTC')
    active = None
    for entry in load_history(path):
        if pd.Timestamp(entry['valid_from']) <= ts:
            active = entry['team']
        else:
            break
    return active
