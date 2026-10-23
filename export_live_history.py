"""Exportiert die Live-Historie fuer den Live-vs-Backtest-Abgleich (nur lesend).

Sendet keine Orders, aendert nichts am Konto oder an Dateien des Bots.
Schreibt livemodus/live_export_<datum>.tar.gz (gitignored) mit:
  - Bitget v2: Positions-Historie, Order-Historie, Plan-Order-Historie
    (normal_plan = SL, track_plan = Trailing), Fill-Historie  -> Rohdaten als JSON
  - settings.json (enthaelt keine Schluessel)
  - Bot-Logs (logs/*.log), Scheduler-Status

Aufruf auf dem VPS:  .venv/bin/python3 export_live_history.py --since 2026-10-14
"""
import argparse
import glob
import json
import os
import tarfile
import time
from datetime import datetime, timezone

import ccxt

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUCT = 'USDT-FUTURES'


def paginate(call, base, list_key='entrustedList', max_pages=200):
    """Bitget-v2-Historie rueckwaerts ueber idLessThan/endId abholen."""
    rows, cursor = [], None
    for _ in range(max_pages):
        params = dict(base)
        if cursor:
            params['idLessThan'] = cursor
        resp = call(params)
        data = resp.get('data') or {}
        batch = data.get(list_key) or (data if isinstance(data, list) else []) or []
        if not batch:
            break
        rows.extend(batch)
        end_id = data.get('endId') if isinstance(data, dict) else None
        if not end_id or end_id == cursor or len(batch) < int(base.get('limit', 100)):
            break
        cursor = end_id
        time.sleep(0.2)
    return rows


def safe(name, fn, errors):
    try:
        out = fn()
        print(f"  {name}: {len(out)} Eintraege")
        return out
    except Exception as e:  # Export soll nie am einzelnen Endpunkt scheitern
        print(f"  {name}: FEHLER {type(e).__name__}: {e}")
        errors[name] = f"{type(e).__name__}: {e}"
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', required=True, help='YYYY-MM-DD (UTC)')
    args = ap.parse_args()
    since_ms = int(datetime.strptime(args.since, '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp() * 1000)
    now_ms = int(time.time() * 1000)

    if os.environ.get('STBOT_API_KEY'):
        acc = {'apiKey': os.environ['STBOT_API_KEY'], 'secret': os.environ.get('STBOT_API_SECRET'),
               'password': os.environ.get('STBOT_API_PASSWORD')}
    else:
        with open(os.path.join(SCRIPT_DIR, 'secret.json')) as f:
            acc = json.load(f)['stbot'][0]
    ex = ccxt.bitget({'apiKey': acc.get('apiKey'), 'secret': acc.get('secret'),
                      'password': acc.get('password'), 'options': {'defaultType': 'swap'},
                      'enableRateLimit': True})

    # Bitget erlaubt max. 90 Tage je Abfrage -> in 7-Tage-Fenstern abfragen
    windows, s = [], since_ms
    while s < now_ms:
        windows.append((s, min(s + 7 * 86400000, now_ms)))
        s += 7 * 86400000

    def collect(call, extra, list_key):
        out = []
        for a, b in windows:
            base = {'productType': PRODUCT, 'startTime': str(a), 'endTime': str(b), 'limit': '100', **extra}
            out.extend(paginate(call, base, list_key))
        return out

    errors = {}
    print("Lese Bitget-Historie (nur lesend) ...")
    export = {
        'exported_at_utc': datetime.now(timezone.utc).isoformat(),
        'since': args.since,
        'ccxt_version': ccxt.__version__,
        'positions_history': safe('positions_history', lambda: collect(
            ex.privateMixGetV2MixPositionHistoryPosition, {}, 'list'), errors),
        'orders_history': safe('orders_history', lambda: collect(
            ex.privateMixGetV2MixOrderOrdersHistory, {}, 'entrustedList'), errors),
        'plan_normal_history': safe('plan_normal_history', lambda: collect(
            ex.privateMixGetV2MixOrderOrdersPlanHistory, {'planType': 'normal_plan'}, 'entrustedList'), errors),
        'plan_track_history': safe('plan_track_history', lambda: collect(
            ex.privateMixGetV2MixOrderOrdersPlanHistory, {'planType': 'track_plan'}, 'entrustedList'), errors),
        'fills': safe('fills', lambda: collect(
            ex.privateMixGetV2MixOrderFillHistory, {}, 'fillList'), errors),
        'errors': errors,
    }

    stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')
    out_dir = os.path.join(SCRIPT_DIR, 'livemodus')
    os.makedirs(out_dir, exist_ok=True)
    raw_name = f'bitget_history_{stamp}.json'
    raw_path = os.path.join(out_dir, raw_name)
    with open(raw_path, 'w') as f:
        json.dump(export, f, indent=1)

    tar_path = os.path.join(out_dir, f'live_export_{stamp}.tar.gz')
    with tarfile.open(tar_path, 'w:gz') as tar:
        tar.add(raw_path, arcname=raw_name)
        tar.add(os.path.join(SCRIPT_DIR, 'settings.json'), arcname='settings.json')
        for p in glob.glob(os.path.join(SCRIPT_DIR, 'logs', '*.log*')):
            tar.add(p, arcname=os.path.join('logs', os.path.basename(p)))
        for extra in ['artifacts/results/last_optimizer_run.json', 'open_trades.json']:
            p = os.path.join(SCRIPT_DIR, extra)
            if os.path.exists(p):
                tar.add(p, arcname=extra)
    os.remove(raw_path)
    print(f"\nFertig: {tar_path}")
    if errors:
        print("Hinweis: einzelne Endpunkte fehlgeschlagen, siehe 'errors' im Export.")


if __name__ == '__main__':
    main()
