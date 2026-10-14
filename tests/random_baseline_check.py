"""
Kontrollgruppe: Wie oft besteht eine Strategie OHNE Kante (Zufalls-Einstiege) die
Bestaetigungs-Kriterien? Gleiche Daten, IS/OOS-Aufteilung, SL-Abstand, Trailing-Exit,
Sizing und Gebuehren wie die jeweilige Config -- nur Einstiegszeitpunkt und Richtung
sind zufaellig, Einstiegshaeufigkeit ~ wie die echte Config im OOS.
Kriterien: IS-PnL > 0, OOS >= 30 Trades, OOS-PnL >= 10 %, OOS-MaxDD < 30 %.
Exit-Pfad: grobe 4-Punkte-Kerze (schneller; fuer die Schaetzung ausreichend).
"""
import sys, os, json, glob
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src')); os.chdir(ROOT)
import numpy as np, pandas as pd, ta
from stbot.analysis.backtester import load_data
from stbot.strategy.trade_rules import new_position, candle_path, simulate_exit, trade_net_pnl, position_notional

END = '2026-10-13'; SEEDS = 10
LB = {'15m': 180, '30m': 365, '1h': 365, '2h': 1825, '4h': 1825, '6h': 1825, '1d': 1825}
# Nutzung: python tests/random_baseline_check.py <Config1,Config2,...|alle> <ausgabe.csv>
names = sys.argv[1].split(',') if sys.argv[1] != 'alle' else sorted(
    os.path.basename(f)[7:-5] for f in glob.glob(os.path.join(ROOT, 'src/stbot/strategy/configs/config_*.json')))
out = sys.argv[2]


def stats(trades, cap0):
    eq = cap0; peak = cap0; dd = 0.0
    for pnl in trades:
        eq += pnl; peak = max(peak, eq); dd = max(dd, (peak - eq) / peak if peak > 0 else 1)
    return len(trades), (eq / cap0 - 1) * 100, dd * 100


rows = []
for n in names:
    cfg = json.load(open(f'src/stbot/strategy/configs/config_{n}.json'))
    m = cfg['_meta']; rp = cfg['risk']
    sym, tf = cfg['market']['symbol'], cfg['market']['timeframe']
    start = (pd.Timestamp(END) - pd.Timedelta(days=LB[tf])).strftime('%Y-%m-%d')
    d = load_data(sym, tf, start, END, quiet=True)
    if d is None or d.empty:
        continue
    d = d[~d.index.duplicated()].sort_index().copy()
    d['atr'] = ta.volatility.AverageTrueRange(d['high'], d['low'], d['close'], 14).average_true_range()
    ev0 = pd.Timestamp(start, tz='UTC')
    idx = d.index[d.index >= ev0]
    if len(idx) < 100:
        continue
    split = idx[int(len(idx) * 0.70)]
    n_oos = (d.index >= split).sum()
    target = max(int(m.get('oos_trades') or 0), 10)
    q = min(0.5, target / n_oos * 2.5)   # Einstiegswahrscheinlichkeit je flacher Kerze
    O, H, L, C, A = (d[c].values for c in ('open', 'high', 'low', 'close', 'atr'))
    ts = d.index
    def sim(seed, q):
        rng = np.random.default_rng(seed)
        cap_is = 1000.0; cap_oos = 1000.0
        is_tr, oos_trs = [], []
        pos = None; seg = None
        for i in range(len(d)):
            if pos is not None:
                xp = simulate_exit(pos, candle_path(O[i], H[i], L[i], C[i]))
                if xp:
                    pnl = trade_net_pnl(pos, xp, pos['notional'])
                    (is_tr if seg == 'is' else oos_trs).append(pnl)
                    if seg == 'is': cap_is += pnl
                    else: cap_oos += pnl
                    pos = None
            if pos is None and ts[i] >= ev0 and not np.isnan(A[i]) and rng.random() < q:
                seg = 'is' if ts[i] < split else 'oos'
                if seg == 'is' and i + 1 < len(d) and ts[i + 1] >= split:
                    continue
                cap = cap_is if seg == 'is' else cap_oos
                sl = max(A[i] * rp.get('atr_multiplier_sl', 2.0), C[i] * rp.get('min_sl_pct', 0.3) / 100)
                pos = new_position('buy' if rng.random() < 0.5 else 'sell', C[i], sl, rp)
                pos['notional'] = position_notional(cap, rp, sl / C[i])
        return is_tr, oos_trs

    # q so kalibrieren, dass die Zufallsstrategie im OOS etwa so oft handelt wie die echte
    for _ in range(3):
        _, o = sim(999, q)
        if len(o) == 0: q = min(0.5, q * 3); continue
        q = min(0.5, q * target / len(o))
    passes = 0; oos_tr = []
    for seed in range(SEEDS):
        is_tr, oos_trs = sim(seed, q)
        n_is, p_is, _ = stats(is_tr, 1000.0)
        n_o, p_o, dd_o = stats(oos_trs, 1000.0)
        ok = p_is > 0 and n_o >= 30 and p_o >= 10 and dd_o < 30
        passes += ok; oos_tr.append(n_o)
    rows.append(dict(cfg=n, real_confirmed=bool(m.get('confirmed')), real_oos_trades=m.get('oos_trades'),
                     random_oos_trades_avg=float(np.mean(oos_tr)), random_pass_rate=passes / SEEDS))
    print(f"{n:20s} echt={'J' if m.get('confirmed') else '-'} Zufall-Trades~{np.mean(oos_tr):5.1f} bestanden {passes}/{SEEDS}", flush=True)
pd.DataFrame(rows).to_csv(out, index=False)
