"""
Paritaetspruefung: Backtest = Livebot.

Prueft fuer jede Config, dass alle drei Pfade dieselben Trades erzeugen:
  1. Optimizer-Backtester   (analysis/backtester.run_backtest)
  2. Portfolio-Simulator    (analysis/portfolio_simulator.run_portfolio_simulation, eine Strategie)
  3. Live-Code              (utils/trade_manager.full_trade_cycle) gegen eine Mock-Boerse,
                            die im 15-Minuten-Takt (Cron +30 s) nur die zu diesem Zeitpunkt
                            geschlossenen Kerzen herausgibt.

Verglichen werden:
  - Backtester vs. Simulator: jeder Trade (Entry-Zeit, Richtung, Entry-, Exit-Zeit, Exit-Preis, PnL)
  - Live vs. Simulator: jede Entry-Zeit + Richtung. Offene Positionen im Mock = Positionen laut
    Simulator (gleiche Exits), damit gezielt die EINSTIEGS-Entscheidung des Live-Codes geprueft wird;
    die Exits fuehrt live Bitget mit SL- und Trailing-Order aus, die der Simulator per
    trade_rules.simulate_exit nachbildet (Abgleich mit echten Fills: daten/-Auswertungen).

Nutzung (braucht Netzwerk fuer Kerzendaten, nutzt den data/cache):
  python tests/parity_check.py                          # alle Configs, 01.08. bis heute
  python tests/parity_check.py --configs ARBUSDTUSDT_4h --start 2026-07-01 --end 2026-10-11
Exit-Code 0 = alle Configs identisch, 1 = Abweichung gefunden.
"""
import argparse
import glob
import json
import logging
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
os.chdir(ROOT)

import ccxt
import pandas as pd

import stbot.utils.trade_manager as tm
from stbot.analysis.backtester import load_data, run_backtest, FINE_TF_MAP
from stbot.analysis.portfolio_simulator import run_portfolio_simulation
from stbot.strategy.trade_rules import bitget_min_amounts

TF_MIN = {'15m': 15, '30m': 30, '1h': 60, '2h': 120, '4h': 240, '6h': 360, '1d': 1440}
WARMUP_DAYS = 60
PRICE_TOL = 1e-9


class MockExchange:
    """Gibt nur Daten heraus, die zum simulierten Zeitpunkt `now` bekannt waren."""

    def __init__(self, symbol, frames, positions, capital, min_amount):
        self.symbol, self.frames, self.positions, self.capital = symbol, frames, positions, capital
        self.now = None
        self.orders = []
        self.exchange = ccxt.bitget()
        self.exchange.price_to_precision = lambda s, p: p
        self.markets = {symbol: {'limits': {'amount': {'min': min_amount or 0.0}}}}

    def fetch_recent_ohlcv(self, symbol, timeframe, limit=300):
        df = self.frames[timeframe]
        dur = pd.Timedelta(minutes=TF_MIN[timeframe])
        return df[df.index + dur <= self.now].tail(limit).copy()

    def fetch_open_positions(self, symbol):
        for a, b in self.positions:
            if a <= self.now < b:
                return [{'contracts': 1, 'side': 'long'}]
        return []

    def fetch_realized_equity_usdt(self): return self.capital
    def fetch_balance_usdt(self): return self.capital
    def set_margin_mode(self, *a, **k): pass
    def set_leverage(self, *a, **k): pass
    def place_trigger_market_order(self, *a, **k): pass
    def place_trailing_stop_order(self, *a, **k): pass
    def cancel_all_orders_for_symbol(self, *a, **k): pass

    def create_market_order(self, symbol, side, amount, params=None):
        self.orders.append((self.now, 'long' if side == 'buy' else 'short'))
        return {'id': len(self.orders)}


def _silence_live_side_effects(tmpdir):
    tm.DB_PATH = tmpdir
    tm.TRADE_LOCK_FILE = os.path.join(tmpdir, 'trade_lock.json')
    tm.time.sleep = lambda s: None
    tm.send_message = lambda *a, **k: None
    tm._send_stbot_chart = lambda *a, **k: None


def check_config(name, start, end, capital, log):
    cfg = json.load(open(os.path.join(ROOT, 'src', 'stbot', 'strategy', 'configs', f'config_{name}.json')))
    sym, tf, htf = cfg['market']['symbol'], cfg['market']['timeframe'], cfg['market'].get('htf')
    dur = pd.Timedelta(minutes=TF_MIN[tf])
    warm = (pd.Timestamp(start) - pd.Timedelta(days=WARMUP_DAYS)).strftime('%Y-%m-%d')
    data = load_data(sym, tf, warm, end, quiet=True)
    data = data[~data.index.duplicated()].sort_index()
    fine = load_data(sym, FINE_TF_MAP[tf], warm, end, quiet=True)
    frames = {tf: data}
    if cfg['strategy'].get('use_weekly_trend_filter'):
        frames['1d'] = load_data(sym, '1d', (pd.Timestamp(warm) - pd.Timedelta(days=450)).strftime('%Y-%m-%d'), end, quiet=True)
    min_amount = bitget_min_amounts().get(sym)

    # 1) Backtester
    sp = dict(cfg['strategy']); sp.update(symbol=sym, timeframe=tf, htf=htf)
    bt = run_backtest(data.copy(), sp, cfg['risk'], start_capital=capital, fine_data=fine, return_trades=True,
                      trade_start=pd.Timestamp(warm, tz='UTC'))
    bt_trades = [(pd.Timestamp(t['entry_time']), t['side'], t['entry_price'], pd.Timestamp(t['exit_time']),
                  t['exit_price'], t['pnl_usd']) for t in bt['trades']]

    # 2) Simulator
    sd = {name: {'symbol': sym, 'timeframe': tf, 'data': data.copy(), 'fine_data': fine,
                 'smc_params': cfg['strategy'], 'risk_params': cfg['risk'], 'htf': htf, 'min_amount': min_amount}}
    sim = run_portfolio_simulation(capital, sd, warm, end, verbose=False)
    sim_trades = [(pd.Timestamp(t['entry_time']), t['direction'], t['entry'], pd.Timestamp(t['ts']),
                   t['exit'], t['pnl']) for t in sim['trade_history']]

    bt_vs_sim = []
    for a, b in zip(bt_trades, sim_trades):
        if a[0] != b[0] or a[1] != b[1] or a[3] != b[3] or abs(a[2] - b[2]) > PRICE_TOL \
                or abs(a[4] - b[4]) > PRICE_TOL or abs(a[5] - b[5]) > max(1e-6, abs(b[5]) * 1e-6):
            bt_vs_sim.append((a, b))
    if len(bt_trades) != len(sim_trades):
        bt_vs_sim.append(('Anzahl', len(bt_trades), len(sim_trades)))

    # 3) Live-Code gegen Mock-Boerse
    positions = [(t[0] + dur + pd.Timedelta(seconds=60), t[3] + pd.Timedelta(seconds=60)) for t in sim_trades]
    for p in sim.get('open_positions', []):
        positions.append((pd.Timestamp(p['entry_time']) + dur + pd.Timedelta(seconds=60), pd.Timestamp('2100-01-01', tz='UTC')))
    ex = MockExchange(sym, frames, positions, capital, min_amount)
    params = {'market': {'symbol': sym, 'timeframe': tf, 'htf': htf}, 'strategy': cfg['strategy'], 'risk': cfg['risk']}
    tm.last_closed_candle_ts = lambda exch, timeframe: ex.now.floor(f"{int(dur.total_seconds())}s") - dur
    if os.path.exists(tm.TRADE_LOCK_FILE):
        os.remove(tm.TRADE_LOCK_FILE)
    t = pd.Timestamp(start, tz='UTC')
    t_end = pd.Timestamp(end, tz='UTC')
    while t <= t_end:
        ex.now = t + pd.Timedelta(seconds=30)
        tm.full_trade_cycle(ex, None, None, params, {}, log)
        t += pd.Timedelta(minutes=15)

    t0 = pd.Timestamp(start, tz='UTC')
    last_run = t_end + pd.Timedelta(seconds=30)
    # Nur Einstiege vergleichen, deren Signal-Kerze VOR dem letzten Live-Takt geschlossen
    # war -- sonst zaehlt eine Kerze, die der Live-Code im Testfenster nie sehen konnte.
    in_window = lambda ts: ts + dur >= t0 and ts + dur <= last_run
    live = {((o.floor('15min') - dur), side) for o, side in ex.orders}
    sim_entries = {(e[0], e[1]) for e in sim_trades if in_window(e[0])}
    sim_entries |= {(pd.Timestamp(p['entry_time']), p['direction']) for p in sim.get('open_positions', [])
                    if in_window(pd.Timestamp(p['entry_time']))}
    only_sim, only_live = sorted(sim_entries - live), sorted(live - sim_entries)

    ok = not bt_vs_sim and not only_sim and not only_live
    print(f"{'OK  ' if ok else 'FEHL'} {name:18s} Backtester {len(bt_trades):3d} / Simulator {len(sim_trades):3d} Trades "
          f"(PnL {bt['total_pnl_pct']:+.2f}% / {sim['total_pnl_pct']:+.2f}%) | Live-Entries {len(live):3d} "
          f"vs. Simulator {len(sim_entries):3d}", flush=True)
    for x in bt_vs_sim[:3]:
        print(f"       Backtester != Simulator: {x}")
    for x in only_sim[:3]:
        print(f"       nur Simulator: {x}")
    for x in only_live[:3]:
        print(f"       nur Live:      {x}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--configs', default='', help='Komma-Liste, z.B. ARBUSDTUSDT_4h (Standard: alle)')
    ap.add_argument('--start', default='2026-08-01')
    ap.add_argument('--end', default=pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d'))
    ap.add_argument('--capital', type=float, default=1000.0)
    args = ap.parse_args()

    names = [c for c in args.configs.split(',') if c] or sorted(
        os.path.basename(f)[7:-5] for f in glob.glob(os.path.join(ROOT, 'src/stbot/strategy/configs/config_*.json')))
    _silence_live_side_effects(tempfile.mkdtemp())
    log = logging.getLogger('parity')
    log.addHandler(logging.NullHandler())
    log.propagate = False

    failed = [n for n in names if not check_config(n, args.start, args.end, args.capital, log)]
    print(f"\n{len(names) - len(failed)}/{len(names)} Configs identisch.")
    if failed:
        print("Abweichungen: " + ", ".join(failed))
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
