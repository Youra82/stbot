# /root/utbot2/src/stbot/analysis/portfolio_simulator.py
import pandas as pd
import numpy as np
from tqdm import tqdm
import sys
import os
import ta
import math
import json

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

# Imports auf StBot angepasst
from stbot.strategy.trade_rules import (prepare_signal_frame, build_bias_context, entry_side,
                                        sl_distance, position_notional, safe_leverage,
                                        new_position, candle_path, simulate_exit, trade_net_pnl,
                                        bitget_min_amounts, order_allowed)
from stbot.analysis.backtester import load_data, _get_fine_slice, load_weekly_source

class Bias:
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


def _effective_start(strat_start, start_date):
    """
    Erster Handelszeitpunkt: start_date der Simulation (bzw. ein spaeterer
    strategie-eigener trade_start). Die Daten davor sind nur Vorlauf fuer SR-Zonen und
    Indikatoren -- seit load_data() >= 1000 Kerzen Vorlauf liefert, handelte der
    Simulator sonst schon im Vorlauf (Wochenauswahl "26 Wochen" bewertete bei 6h ab
    Juli 2025, gefunden 2026-10-14).
    """
    starts = [pd.Timestamp(x) for x in (strat_start, start_date) if x is not None]
    starts = [x.tz_localize('UTC') if x.tzinfo is None else x for x in starts]
    return max(starts) if starts else None


def run_portfolio_simulation(start_capital, strategies_data, start_date, end_date, verbose=True):
    """
    Führt eine chronologische Portfolio-Simulation mit mehreren StBot-Strategien durch.
    """
    if verbose:
        print("\n--- Starte Portfolio-Simulation (StBot SRv2)... ---")
        print("1/3: Bereite Strategie-Daten vor...")

    processed_strategies = {}
    all_timestamps = set()

    _iter = tqdm(strategies_data.items(), desc="Verarbeite Strategien") if verbose else strategies_data.items()
    for key, strat in _iter:
        try:
            raw = strat['data']
            if raw.empty or len(raw) < 100: continue

            params = strat.get('smc_params', {}) # Im Simulator heissen sie evtl. noch smc_params
            if not params: params = strat.get('strategy', {})

            if 'sr_signal' in raw.columns and 'vol_avg' in raw.columns:
                # Bereits in einem frueheren Aufruf mit denselben strategies_data
                # vorverarbeitet -- direkt wiederverwenden statt bei jedem Aufruf neu
                # zu berechnen. run_portfolio_optimizer.py ruft diese Funktion dutzende
                # bis 100+ Mal MIT DENSELBEN strategies_data auf -- ohne Cache fuehrte das
                # zu massivem Allokations-Churn (live beobachtet: OOM-Kill bei 5.1GB RSS
                # auf einem 6.3GB-Mini-PC, 2026-08-24).
                df = raw
            else:
                # GEMEINSAME Signal-Vorbereitung mit Optimizer-Backtester und Live
                # (strategy/trade_rules.py): ATR, Volumen-Schnitt, Energie/Avalanche/
                # Streak, SR-Signal.
                df = prepare_signal_frame(raw, params)
                df.dropna(subset=['atr', 'sr_signal'], inplace=True)
                if df.empty: continue
                strat['data'] = df  # fuer kuenftige Aufrufe mit denselben strategies_data cachen

            if strat.get('bias_ctx') is None:
                weekly_source = None
                if params.get('use_weekly_trend_filter', False):
                    weekly_source = load_weekly_source(strat.get('symbol', key), df.index[0], df.index[-1])
                strat['bias_ctx'] = build_bias_context(df, strat.get('timeframe', ''), params, strat.get('htf'),
                                                       weekly_source=weekly_source)

            processed_strategies[key] = {
                'data': df,
                'params': params,
                'risk_params': strat.get('risk_params', {}),
                'symbol': strat.get('symbol', key),
                'timeframe': strat.get('timeframe', ''),
                'fine_data': strat.get('fine_data'),
                'bias_ctx': strat.get('bias_ctx'),
                'min_amount': strat.get('min_amount') or bitget_min_amounts().get(strat.get('symbol', key)),
                # Optional: erst ab diesem Zeitpunkt einsteigen (Daten davor nur Vorlauf),
                # z.B. fuer OOS-Portfolio-Backtests -- wie run_backtest(trade_start=...).
                'trade_start': _effective_start(strat.get('trade_start'), start_date),
                'coarse_duration': df.index[1] - df.index[0] if len(df.index) >= 2 else None,
            }

            all_timestamps.update(df.index)

        except Exception as e:
            print(f"Fehler bei Vorbereitung von {key}: {e}")

    if not processed_strategies:
        return None

    sorted_timestamps = sorted(list(all_timestamps))
    if verbose:
        print(f"-> {len(sorted_timestamps)} Zeitschritte zu simulieren.")
        print("2/3: Führe Simulation durch...")

    equity = start_capital
    peak_equity = start_capital
    max_drawdown_pct = 0.0
    max_drawdown_date = None
    min_equity_ever = start_capital
    liquidation_date = None

    open_positions = {} # Key: strategy_key
    trade_history = []
    equity_curve = []

    absolute_max_notional_value = 1000000

    _ts_iter = tqdm(sorted_timestamps, desc="Simuliere") if verbose else sorted_timestamps
    for ts in _ts_iter:
        if liquidation_date: break

        current_total_equity = equity
        unrealized_pnl = 0
        positions_to_close = []

        # A) Offene Positionen managen
        for key, pos in open_positions.items():
            strat = processed_strategies.get(key)
            if not strat or ts not in strat['data'].index:
                if pos.get('last_known_price'):
                    pnl_mult = 1 if pos['side'] == 'long' else -1
                    unrealized_pnl += pos['notional_value'] * (pos['last_known_price'] / pos['entry_price'] - 1) * pnl_mult
                continue

            current_candle = strat['data'].loc[ts]
            pos['last_known_price'] = current_candle['close']

            # Ausstieg ueber die GEMEINSAME Logik (trade_rules.simulate_exit): SL-Trigger +
            # Trailing-Stop wie die beiden Live-Orders, keine feste TP-Order.
            fine_data = strat.get('fine_data')
            coarse_duration = strat.get('coarse_duration')
            fine_slice = None
            if fine_data is not None and coarse_duration is not None:
                fine_slice = _get_fine_slice(fine_data, ts, ts + coarse_duration)
            path = candle_path(current_candle['open'], current_candle['high'], current_candle['low'],
                               current_candle['close'], fine_slice)
            exit_price = simulate_exit(pos, path)

            if exit_price:
                net_pnl = trade_net_pnl(pos, exit_price, pos['notional_value'])
                equity += net_pnl
                trade_history.append({
                    'strategy_key': key,
                    'ts':         ts.isoformat() if hasattr(ts, 'isoformat') else str(ts),
                    'entry_time': pos.get('entry_time', ts).isoformat() if hasattr(pos.get('entry_time', ts), 'isoformat') else str(pos.get('entry_time', ts)),
                    'symbol':     pos.get('symbol_key', key),
                    'timeframe':  pos.get('timeframe', ''),
                    'direction':  pos['side'],
                    'entry':      pos['entry_price'],
                    'exit':       exit_price,
                    'pnl':        net_pnl,
                    'leverage':   pos.get('leverage', 0),
                    'margin_used': round(pos.get('margin_used', 0), 4),
                })
                positions_to_close.append(key)
            else:
                pnl_mult = 1 if pos['side'] == 'long' else -1
                unrealized_pnl += pos['notional_value'] * (current_candle['close'] / pos['entry_price'] - 1) * pnl_mult

        for key in positions_to_close:
            del open_positions[key]

        # B) Neue Positionen öffnen -- gleiche Regeln wie Live (trade_manager.py):
        #    trade_rules.entry_side, max. eine Position je Symbol, Bitget-Mindestmenge,
        #    liquidationssicherer Hebel.
        if equity > 0:
            for key, strat in processed_strategies.items():
                if key in open_positions: continue
                if ts not in strat['data'].index: continue
                if strat.get('trade_start') is not None and ts < strat['trade_start']: continue
                if any(p.get('symbol_key') == strat.get('symbol') for p in open_positions.values()):
                    continue

                current_candle = strat['data'].loc[ts]
                side = entry_side(current_candle, ts, strat['params'], strat.get('bias_ctx'))

                if side:
                    risk_params = strat['risk_params']
                    entry_price = current_candle['close']
                    sl_dist = sl_distance(current_candle, risk_params)
                    if sl_dist <= 0: continue
                    sl_pct = sl_dist / entry_price

                    final_notional = min(position_notional(equity, risk_params, sl_pct), absolute_max_notional_value)
                    if not order_allowed(final_notional, entry_price, strat.get('min_amount')): continue

                    leverage = safe_leverage(risk_params.get('leverage', 10), sl_pct)
                    margin_used = math.ceil((final_notional / leverage) * 100) / 100
                    current_used_margin = sum(p['margin_used'] for p in open_positions.values())

                    if current_used_margin + margin_used > equity: continue

                    pos = new_position(side, entry_price, sl_dist, risk_params)
                    pos.update({
                        'notional_value': final_notional, 'margin_used': margin_used,
                        'last_known_price': entry_price,
                        'entry_time': ts,
                        'symbol_key': strat.get('symbol', key),
                        'timeframe':  strat.get('timeframe', ''),
                        'leverage':   leverage,
                    })
                    open_positions[key] = pos

        # C) Tracking
        current_total_equity = equity + unrealized_pnl
        equity_curve.append({'timestamp': ts, 'equity': current_total_equity})

        peak_equity = max(peak_equity, current_total_equity)
        drawdown = (peak_equity - current_total_equity) / peak_equity if peak_equity > 0 else 0
        if drawdown > max_drawdown_pct:
            max_drawdown_pct = drawdown
            max_drawdown_date = ts

        min_equity_ever = min(min_equity_ever, current_total_equity)
        if current_total_equity <= 0 and not liquidation_date:
            liquidation_date = ts

    # --- 3. Abschluss ---
    if verbose:
        print("3/3: Bereite Ergebnisse vor...")
    final_equity = equity_curve[-1]['equity'] if equity_curve else start_capital
    total_pnl_pct = (final_equity / start_capital - 1) * 100 if start_capital > 0 else 0
    wins = sum(1 for t in trade_history if t['pnl'] > 0)
    win_rate = (wins / len(trade_history) * 100) if trade_history else 0

    equity_df = pd.DataFrame(equity_curve)
    if not equity_df.empty:
        equity_df['peak'] = equity_df['equity'].cummax()
        equity_df['drawdown_pct'] = ((equity_df['peak'] - equity_df['equity']) / equity_df['peak'].replace(0, np.nan)).fillna(0)
        equity_df['timestamp'] = pd.to_datetime(equity_df['timestamp'])
        equity_df.set_index('timestamp', inplace=True, drop=False)

    return {
        "start_capital": start_capital,
        "end_capital": final_equity,
        "total_pnl_pct": total_pnl_pct,
        "trade_count": len(trade_history),
        "win_rate": win_rate,
        "max_drawdown_pct": max_drawdown_pct * 100,
        "max_drawdown_date": max_drawdown_date,
        "min_equity": min_equity_ever,
        "liquidation_date": liquidation_date,
        "trade_history": trade_history,
        "open_positions": [{'strategy_key': k, 'entry_time': p.get('entry_time'), 'direction': p['side'],
                            'entry': p['entry_price']} for k, p in open_positions.items()],
        "equity_curve": equity_df
    }
