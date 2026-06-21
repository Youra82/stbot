#!/usr/bin/env python3
"""
show_chart.py — Simuliert einen SR-Breakout-Chart und sendet ihn per Telegram.

Laedt OHLCV-Daten, berechnet SR-Signale und sendet einen PNG-Chart mit
EMA20/50, SR-Breakout-Marker und Entry/SL/TP-Levels.
Kein echter Trade wird platziert.

Aufruf:
    .venv/bin/python show_chart.py
    .venv/bin/python show_chart.py --symbol BTC/USDT:USDT --timeframe 4h
    .venv/bin/python show_chart.py --symbol BTC/USDT:USDT --timeframe 4h --side buy
"""
import argparse
import json
import logging
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

import pandas as pd
import ta

from stbot.utils.exchange import Exchange
from stbot.utils.trade_manager import _generate_stbot_chart_png
from stbot.utils.telegram import send_photo, send_message
from stbot.strategy.sr_engine import SREngine
from stbot.strategy.trade_logic import get_titan_signal

logging.basicConfig(level=logging.WARNING, format='[%(levelname)s] %(message)s')

TMP_DIR = os.path.join(PROJECT_ROOT, 'artifacts', 'tmp')


def _load_secrets():
    with open(os.path.join(PROJECT_ROOT, 'secret.json')) as f:
        return json.load(f)


def _load_settings():
    with open(os.path.join(PROJECT_ROOT, 'settings.json')) as f:
        return json.load(f)


def _make_dummy_signal(processed_df: pd.DataFrame, side: str, params: dict) -> dict:
    """Baut ATR-basiertes Dummy-Signal falls kein echtes SR-Signal vorhanden."""
    import numpy as np
    closes = processed_df['close'].values
    atrs   = processed_df['atr'].values if 'atr' in processed_df.columns else None

    entry = float(closes[-1])
    atr   = float(atrs[-1]) if atrs is not None and not pd.isna(atrs[-1]) else entry * 0.015

    risk  = params.get('risk', {})
    sl_mult = risk.get('atr_multiplier_sl', 2.0)
    rr      = risk.get('risk_reward_ratio', 2.0)

    sl = entry - sl_mult * atr if side == 'buy' else entry + sl_mult * atr
    tp = entry + rr * abs(entry - sl) if side == 'buy' else entry - rr * abs(entry - sl)

    return {
        'entry_price': entry,
        'sl_price':    sl,
        'tp_price':    tp,
        'rr':          rr,
    }


def _build_params(symbol: str, timeframe: str, settings: dict) -> dict:
    strats = settings.get('active_strategies', [])
    strat  = next((s for s in strats
                   if s.get('symbol') == symbol and s.get('timeframe') == timeframe), {})
    risk   = strat.get('risk_settings', settings.get('risk_settings', {}))
    strat_p = strat.get('strategy', {})
    return {
        'market':   {'symbol': symbol, 'timeframe': timeframe, 'htf': strat.get('htf')},
        'strategy': strat_p,
        'risk':     risk,
    }


def generate_and_send(exchange: Exchange, symbol: str, timeframe: str,
                      force_side: str, settings: dict, tg: dict) -> bool:
    params = _build_params(symbol, timeframe, settings)
    strat_params = params.get('strategy', {})

    print(f"  Lade OHLCV {symbol} ({timeframe})...")
    df = exchange.fetch_recent_ohlcv(symbol, timeframe, limit=500)
    if df is None or df.empty or len(df) < 50:
        print(f"  WARNUNG: Nicht genug Daten.")
        return False
    df = df.iloc[:-1]

    # ATR berechnen
    atr_ind = ta.volatility.AverageTrueRange(
        high=df['high'], low=df['low'], close=df['close'], window=14
    )
    df['atr'] = atr_ind.average_true_range()

    # SR Engine
    engine = SREngine(settings=strat_params)
    processed_df = engine.process_dataframe(df)
    current_candle = processed_df.iloc[-1]

    # Echtes Signal suchen
    signal_side, signal_price = get_titan_signal(processed_df, current_candle, params, 'NEUTRAL')

    risk = params.get('risk', {})
    rr   = float(risk.get('risk_reward_ratio', 2.0))

    if signal_side and not force_side:
        entry = float(signal_price or current_candle['close'])
        atr_v = float(current_candle.get('atr', entry * 0.01))
        sl_mult = float(risk.get('atr_multiplier_sl', 2.0))
        sl_dist = max(atr_v * sl_mult, entry * float(risk.get('min_sl_pct', 0.3)) / 100)
        if signal_side == 'buy':
            sl = entry - sl_dist
            tp = entry + sl_dist * rr
        else:
            sl = entry + sl_dist
            tp = entry - sl_dist * rr
        print(f"  Echtes Signal: {signal_side.upper()} @ {entry:.6g}")
    else:
        side_used = force_side or 'buy'
        dummy = _make_dummy_signal(processed_df, side_used, params)
        signal_side = side_used
        entry = dummy['entry_price']
        sl    = dummy['sl_price']
        tp    = dummy['tp_price']
        rr    = dummy['rr']
        print(f"  Kein SR-Signal — simuliere {signal_side.upper()} mit ATR-Levels")

    print(f"  Entry: {entry:.6g} | SL: {sl:.6g} | TP: {tp:.6g}")

    os.makedirs(TMP_DIR, exist_ok=True)
    path = _generate_stbot_chart_png(
        processed_df, signal_side, entry, sl, tp,
        symbol, timeframe, rr, 'NEUTRAL',
    )

    if not path or not os.path.exists(path):
        print("  FEHLER: PNG konnte nicht erstellt werden.")
        return False

    side_label = 'LONG' if signal_side == 'buy' else 'SHORT'
    caption = (
        f"[SIMULATION] STBOT | {symbol} ({timeframe})\n"
        f"{side_label} @ {entry:.6g}  |  SL: {sl:.6g}  |  TP: {tp:.6g}\n"
        f"R:R 1:{rr:.1f}  |  Signal: SR-Breakout"
    )
    send_photo(tg['bot_token'], tg['chat_id'], path, caption)
    os.remove(path)
    print("  Chart gesendet.")
    return True


def main():
    parser = argparse.ArgumentParser(description='stbot SR-Chart simulieren und per Telegram senden')
    parser.add_argument('--symbol',    type=str, help='Symbol (z.B. BTC/USDT:USDT)')
    parser.add_argument('--timeframe', type=str, help='Timeframe (z.B. 4h)')
    parser.add_argument('--side',      type=str, default='',
                        choices=['buy', 'sell', ''],
                        help='Richtung erzwingen (default: echtes SR-Signal)')
    args = parser.parse_args()

    secrets  = _load_secrets()
    settings = _load_settings()

    tg = secrets.get('telegram', {})
    if not tg.get('bot_token') or not tg.get('chat_id'):
        print("FEHLER: Kein Telegram-Token/Chat-ID in secret.json.")
        sys.exit(1)

    accounts = secrets.get('stbot', [])
    if not accounts:
        print("FEHLER: Kein 'stbot'-Account in secret.json.")
        sys.exit(1)

    print("Initialisiere Exchange...")
    exchange = Exchange(accounts[0])
    if not exchange.markets:
        print("FEHLER: Exchange konnte nicht initialisiert werden.")
        sys.exit(1)

    active = settings.get('active_strategies', [])

    if args.symbol or args.timeframe:
        targets = [
            s for s in active
            if (not args.symbol    or s['symbol']    == args.symbol)
            and (not args.timeframe or s['timeframe'] == args.timeframe)
        ]
        if not targets:
            sym = args.symbol or (active[0]['symbol'] if active else 'BTC/USDT:USDT')
            tf  = args.timeframe or '4h'
            targets = [{'symbol': sym, 'timeframe': tf, 'active': True}]
    else:
        targets = [s for s in active if s.get('active', True)]

    if not targets:
        print("Keine passenden Strategien gefunden.")
        sys.exit(1)

    print(f"\n{len(targets)} Strategie(n) — generiere Charts...\n")
    send_message(tg['bot_token'], tg['chat_id'],
                 f"STBOT Chart-Simulation ({len(targets)} Strategie(n))")

    ok = 0
    for s in targets:
        symbol    = s.get('symbol', s.get('market', {}).get('symbol', ''))
        timeframe = s.get('timeframe', s.get('market', {}).get('timeframe', ''))
        if not symbol or not timeframe:
            continue
        print(f"[{symbol} / {timeframe}]")
        try:
            if generate_and_send(exchange, symbol, timeframe, args.side, settings, tg):
                ok += 1
        except Exception as e:
            print(f"  FEHLER: {e}")

    print(f"\nFertig: {ok}/{len(targets)} Charts gesendet.")


if __name__ == '__main__':
    main()
