"""
Erzeugt die README-Illustrationen aus echten Daten (docs/images/*.png).

  python docs/make_illustrations.py trade                 # echter OOS-Trade (ARB/2h)
  python docs/make_illustrations.py baseline <csv...>     # Kontrollgruppe (tests/random_baseline_check.py-Ausgaben)
  python docs/make_illustrations.py equity <equity.csv>   # OOS-Portfolio-Equity (Spalte 'equity', Index Zeit)
"""
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
os.chdir(ROOT)

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd

OUT = os.path.join(ROOT, 'docs', 'images')
BG, FG, GRID = '#0f1424', '#e6e6e6', '#2a3146'
GREEN, RED, YELLOW, BLUE, GREY = '#22c55e', '#ef4444', '#eab308', '#38bdf8', '#94a3b8'


def _style(ax):
    ax.set_facecolor(BG)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.tick_params(colors=GREY, labelsize=9)
    ax.grid(color=GRID, linewidth=0.6, alpha=0.6)


def trade(config='ARBUSDTUSDT_2h'):
    from stbot.analysis.backtester import load_data, run_backtest, FINE_TF_MAP, _get_fine_slice
    from stbot.strategy.trade_rules import new_position, candle_path, simulate_exit, sl_distance, prepare_signal_frame

    cfg = json.load(open(f'src/stbot/strategy/configs/config_{config}.json'))
    sym, tf = cfg['market']['symbol'], cfg['market']['timeframe']
    split = pd.Timestamp(cfg['_meta']['is_oos_split_date'], tz='UTC')
    sp = dict(cfg['strategy']); sp.update(symbol=sym, timeframe=tf, htf=cfg['market'].get('htf'))
    data = load_data(sym, tf, str(split.date()), '2026-10-13', quiet=True)
    data = data[~data.index.duplicated()].sort_index()
    fine = load_data(sym, FINE_TF_MAP[tf], str(split.date()), '2026-10-13', quiet=True)
    res = run_backtest(data.copy(), sp, cfg['risk'], 1000, fine_data=fine, return_trades=True, trade_start=split)
    pf = prepare_signal_frame(data, sp)
    dur = pf.index[1] - pf.index[0]

    def replay(t):
        # Trailing-Stop-Verlauf nachrechnen (gleiche Funktion wie Backtest/Live)
        i0, i1 = pf.index.get_loc(t['entry_time']), pf.index.get_loc(t['exit_time'])
        cc = pf.loc[t['entry_time']]
        pos = new_position('buy' if t['side'] == 'long' else 'sell', cc['close'], sl_distance(cc, cfg['risk']), cfg['risk'])
        xs, ys = [], []
        for ts in pf.index[i0 + 1: i1 + 1]:
            row = pf.loc[ts]
            simulate_exit(pos, candle_path(row['open'], row['high'], row['low'], row['close'],
                                           _get_fine_slice(fine, ts, ts + dur)))
            if pos['trailing_active']:
                c = pos['callback_rate']
                xs.append(ts + dur); ys.append(pos['peak_price'] * (1 - c if pos['side'] == 'long' else 1 + c))
        return i0, i1, pos, xs, ys

    # Gewinner mit der laengsten Trailing-Phase (zeigt das Nachziehen am deutlichsten)
    winners = [t for t in res['trades'] if t['pnl_usd'] > 0]
    t = max(winners, key=lambda x: len(replay(x)[3]))
    i0, i1, pos, trail_x, trail_y = replay(t)
    win = pf.iloc[max(0, i0 - 25): min(len(pf), i1 + 6)]

    fig, ax = plt.subplots(figsize=(11, 5.8), facecolor=BG)
    _style(ax)
    w = dur * 0.6
    for ts, r in win.iterrows():
        col = GREEN if r['close'] >= r['open'] else RED
        ax.plot([ts + dur / 2, ts + dur / 2], [r['low'], r['high']], color=col, linewidth=0.8)
        ax.add_patch(plt.Rectangle((ts + dur / 2 - w / 2, min(r['open'], r['close'])), w,
                                   max(abs(r['close'] - r['open']), 1e-9), color=col))
    x0, x1 = win.index[0], win.index[-1] + dur
    entry, sl_p, act = pos['entry_price'], pos['stop_loss'], pos['activation_price']
    for y, c, lab in [(entry, YELLOW, f"Entry {entry:.4f} (Kerzenschluss, Breakout + Volumen)"),
                      (sl_p, RED, f"Stop-Loss {sl_p:.4f} (ATR x {cfg['risk']['atr_multiplier_sl']})"),
                      (act, BLUE, f"Trailing-Aktivierung {act:.4f} ({cfg['risk']['trailing_stop_activation_rr']}R)")]:
        ax.hlines(y, x0, x1, colors=c, linestyles='--', linewidth=1)
        ax.text(x1, y, '  ' + lab, color=c, va='center', fontsize=9)
    if trail_x:
        ax.step(trail_x, trail_y, where='pre', color=GREEN, linestyle=':', linewidth=1.6,
                label=f"Trailing-Stop (Callback {cfg['risk']['trailing_stop_callback_rate_pct']} %)")
    ax.scatter([t['entry_time'] + dur / 2], [entry], marker='^' if t['side'] == 'long' else 'v', s=120, color=YELLOW, zorder=5)
    ax.scatter([t['exit_time'] + dur / 2], [t['exit_price']], marker='X', s=120, color=GREEN, zorder=5)
    mv = (t['exit_price'] / entry - 1) * 100 * (1 if t['side'] == 'long' else -1)
    ax.set_title(f"Echter OOS-Trade {sym.split(':')[0]} {tf}: {t['side'].upper()} {t['entry_time']:%d.%m.%Y}  ->  "
                 f"Exit per Trailing-Stop, {mv:+.1f} %", color=FG, fontsize=12)
    ax.legend(facecolor=BG, edgecolor=GRID, labelcolor=FG, fontsize=9, loc='upper left')
    ax.set_xlim(x0, x1 + dur * 18)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'trade_example.png'), dpi=110, facecolor=BG)
    print('trade_example.png', t['entry_time'], t['side'], round(mv, 2))


def baseline(csvs):
    d = pd.concat([pd.read_csv(c) for c in csvs])
    d['tf'] = d['cfg'].str.split('_').str[-1]
    order = ['15m', '30m', '1h', '2h', '4h', '6h']
    g = d[d['tf'].isin(order)].groupby('tf').agg(rand=('random_pass_rate', 'mean'),
                                                 real=('real_confirmed', 'mean'), n=('cfg', 'size')).reindex(order)
    fig, ax = plt.subplots(figsize=(9, 4.6), facecolor=BG)
    _style(ax)
    x = range(len(order))
    ax.bar([i - 0.2 for i in x], g['rand'] * 100, width=0.4, color=GREY, label='Zufalls-Einstiege (Kontrollgruppe)')
    ax.bar([i + 0.2 for i in x], g['real'] * 100, width=0.4, color=GREEN, label='Echte Strategie (bestaetigt)')
    for i, (r, e, n) in enumerate(zip(g['rand'], g['real'], g['n'])):
        ax.text(i - 0.2, r * 100 + 0.6, f"{r*100:.0f}%", ha='center', color=GREY, fontsize=9)
        ax.text(i + 0.2, e * 100 + 0.6, f"{e*100:.0f}%", ha='center', color=GREEN, fontsize=9)
    ax.set_xticks(list(x)); ax.set_xticklabels([f"{tf}\n({int(n)} Paare)" for tf, n in zip(order, g['n'])], color=GREY)
    ax.set_ylabel('Anteil, der die Bestaetigung besteht (%)', color=GREY)
    exp, real = d['random_pass_rate'].sum(), d['real_confirmed'].sum()
    ax.set_title(f"Kontrollgruppe: erwartet ohne Kante ~{exp:.0f} Treffer, gefunden {int(real)} (von {len(d)} Configs)",
                 color=FG, fontsize=12)
    ax.legend(facecolor=BG, edgecolor=GRID, labelcolor=FG, fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'random_baseline.png'), dpi=110, facecolor=BG)
    print('random_baseline.png')


def equity(csv):
    e = pd.read_csv(csv, index_col=0, parse_dates=True)['equity']
    e = e[e.index >= pd.Timestamp('2026-06-25', tz='UTC')]
    peak = e.cummax(); dd = (peak - e) / peak * 100
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(11, 5.8), facecolor=BG, sharex=True,
                                  gridspec_kw={'height_ratios': [3, 1]})
    _style(ax); _style(ax2)
    ax.plot(e.index, e.values, color=GREEN, linewidth=1.4)
    ax.axhline(e.iloc[0], color=GREY, linestyle='--', linewidth=0.8)
    ax.set_title(f"OOS-Portfolio aller bestaetigten Strategien ab 25.06.2026 (nur Out-of-Sample-Trades): "
                 f"{e.iloc[0]:.0f} -> {e.iloc[-1]:.0f} USDT ({(e.iloc[-1]/e.iloc[0]-1)*100:+.0f} %)", color=FG, fontsize=11)
    ax.set_ylabel('Equity (USDT)', color=GREY)
    ax2.fill_between(dd.index, 0, -dd.values, color=RED, alpha=0.6)
    ax2.set_ylabel(f'Drawdown % (max {dd.max():.0f})', color=GREY)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'oos_portfolio_equity.png'), dpi=110, facecolor=BG)
    print('oos_portfolio_equity.png')


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    what = sys.argv[1] if len(sys.argv) > 1 else 'trade'
    if what == 'trade':
        trade(sys.argv[2] if len(sys.argv) > 2 else 'ARBUSDTUSDT_2h')
    elif what == 'baseline':
        baseline(sys.argv[2:])
    elif what == 'equity':
        equity(sys.argv[2])
