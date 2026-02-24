#!/usr/bin/env python3
"""
Interactive Charts fuer StBot - Support & Resistance Dynamic v2 (SRv2)
Zeigt Candlestick-Chart mit S/R-Zonen + Trade-Signale (Entry/Exit Long/Short)
Nutzt durchnummerierte Konfigurationsdateien zum Auswaehlen
"""

import os
import sys
import json
import logging
import warnings
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
warnings.filterwarnings('ignore')

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from stbot.strategy.sr_engine import SREngine
from stbot.analysis.backtester import load_data, run_backtest

logger = logging.getLogger('interactive_status')
if not logger.handlers:
    logger.setLevel(logging.INFO)
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
    logger.addHandler(ch)


# ---------------------------------------------------------------------------
# Config-Auswahl
# ---------------------------------------------------------------------------

def get_config_files():
    configs_dir = os.path.join(PROJECT_ROOT, 'src', 'stbot', 'strategy', 'configs')
    if not os.path.exists(configs_dir):
        return []
    return sorted(
        [(f, os.path.join(configs_dir, f))
         for f in os.listdir(configs_dir)
         if f.startswith('config_') and f.endswith('.json')]
    )


def select_configs():
    configs = get_config_files()
    if not configs:
        logger.error("Keine Konfigurationsdateien gefunden!")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("Verfuegbare Konfigurationen:")
    print("=" * 60)
    for idx, (filename, _) in enumerate(configs, 1):
        clean = filename.replace('config_', '').replace('.json', '')
        print(f"{idx:2d}) {clean}")
    print("=" * 60)
    print("\nWaehle Konfiguration(en) zum Anzeigen:")
    print("  Einzeln:  z.B. '1' oder '5'")
    print("  Mehrfach: z.B. '1,3,5' oder '1 3 5'")

    selection = input("\nAuswahl: ").strip()
    selected = []
    for part in selection.replace(',', ' ').split():
        try:
            idx = int(part)
            if 1 <= idx <= len(configs):
                selected.append(configs[idx - 1])
            else:
                logger.warning(f"Index {idx} ausserhalb des Bereichs")
        except ValueError:
            logger.warning(f"Ungueltige Eingabe: {part}")

    if not selected:
        logger.error("Keine gueltigen Konfigurationen gewaehlt!")
        sys.exit(1)

    return selected


# ---------------------------------------------------------------------------
# S/R Zonen fuer Visualisierung berechnen
# ---------------------------------------------------------------------------

def compute_last_sr_zones(df: pd.DataFrame, config: dict) -> list:
    """
    Berechnet die aktuellen S/R-Zonen (letzter Zustand) fuer die Chart-Darstellung.
    Gibt eine Liste von {'hi': float, 'lo': float, 'strength': int} zurueck.
    """
    try:
        settings = config.get('strategy', {})
        prd = settings.get('pivot_period', 10)
        ppsrc = settings.get('source', 'High/Low')
        maxnumpp = settings.get('max_pivots', 20)
        channel_w_pct = settings.get('channel_width_pct', 10)
        maxnumsr = settings.get('max_sr_levels', 5)
        min_strength = settings.get('min_strength', 2)

        df = df.copy()
        src1 = df['high'] if ppsrc == 'High/Low' else df[['open', 'close']].max(axis=1)
        src2 = df['low'] if ppsrc == 'High/Low' else df[['open', 'close']].min(axis=1)

        window = 2 * prd + 1
        max_roll = src1.rolling(window=window, center=True).max()
        min_roll = src2.rolling(window=window, center=True).min()
        ph_confirmed = (src1 == max_roll).shift(prd).fillna(False).astype(bool)
        pl_confirmed = (src2 == min_roll).shift(prd).fillna(False).astype(bool)
        ph_val = src1.shift(prd)
        pl_val = src2.shift(prd)

        if 'atr' in df.columns:
            cwidths = df['atr'] * (channel_w_pct / 10.0)
        else:
            h300 = df['high'].rolling(300, min_periods=50).max()
            l300 = df['low'].rolling(300, min_periods=50).min()
            cwidths = (h300 - l300) * channel_w_pct / 100

        closes = df['close'].values
        idx_ph = ph_confirmed.values
        idx_pl = pl_confirmed.values
        val_ph = ph_val.values
        val_pl = pl_val.values
        arr_cw = cwidths.fillna(0).values

        pivotvals = []
        final_zones = []

        for i in range(len(df)):
            new_val = None
            if idx_ph[i]:
                new_val = val_ph[i]
            elif idx_pl[i]:
                new_val = val_pl[i]
            if new_val is not None and not np.isnan(new_val):
                pivotvals.insert(0, new_val)
                if len(pivotvals) > maxnumpp:
                    pivotvals.pop()

            if not pivotvals:
                continue

            cw = arr_cw[i]
            if cw == 0 and i > 50:
                cw = closes[i] * 0.01

            temp = []
            for p_ref in pivotvals:
                lo, hi, strength = p_ref, p_ref, 0
                for p_comp in pivotvals:
                    wdth = hi - p_comp if p_comp <= lo else p_comp - lo
                    if wdth <= cw:
                        lo = min(lo, p_comp)
                        hi = max(hi, p_comp)
                        strength += 1
                temp.append({'hi': hi, 'lo': lo, 'strength': strength})

            temp.sort(key=lambda x: x['strength'], reverse=True)
            zones = []
            for z in temp:
                if z['strength'] < min_strength:
                    continue
                overlap = any(
                    (ex['hi'] >= z['lo'] and ex['hi'] <= z['hi']) or
                    (ex['lo'] >= z['lo'] and ex['lo'] <= z['hi']) or
                    (z['hi'] >= ex['lo'] and z['hi'] <= ex['hi'])
                    for ex in zones
                )
                if not overlap:
                    zones.append(z)
                    if len(zones) >= maxnumsr:
                        break

            final_zones = zones  # behalte letzten Zustand

        return final_zones

    except Exception as e:
        logger.warning(f"Fehler bei S/R Zonen-Berechnung: {e}")
        return []


# ---------------------------------------------------------------------------
# Trades aus Backtest extrahieren (fuer Chart-Markierungen)
# ---------------------------------------------------------------------------

def extract_trades(df: pd.DataFrame, config: dict, start_capital: float = 1000) -> list:
    """
    Fuehrt einen simulierten Backtest durch und gibt die Trades als Liste zurueck.
    Jeder Trade: {side, entry_time, entry_price, exit_time, exit_price, pnl_usd}
    """
    trades = []
    try:
        import ta as ta_lib

        df = df.copy()
        atr_ind = ta_lib.volatility.AverageTrueRange(
            high=df['high'], low=df['low'], close=df['close'], window=14)
        df['atr'] = atr_ind.average_true_range()
        df.dropna(subset=['atr'], inplace=True)

        engine = SREngine(settings=config.get('strategy', {}))
        df = engine.process_dataframe(df)

        risk_params = config.get('risk', {})
        rrr = risk_params.get('risk_reward_ratio', 2.0)
        rpt = risk_params.get('risk_per_trade_pct', 1.0) / 100
        act_rr = risk_params.get('trailing_stop_activation_rr', 2.0)
        cb = risk_params.get('trailing_stop_callback_rate_pct', 1.0) / 100
        lev = risk_params.get('leverage', 10)
        fee = 0.06 / 100
        atr_sl = risk_params.get('atr_multiplier_sl', 2.0)
        min_sl = risk_params.get('min_sl_pct', 0.3) / 100

        capital = start_capital
        pos = None

        for ts, row in df.iterrows():
            if capital <= 0:
                break

            if pos:
                exit_price = None
                if pos['side'] == 'long':
                    if not pos['trailing'] and row['high'] >= pos['act']:
                        pos['trailing'] = True
                    if pos['trailing']:
                        pos['peak'] = max(pos['peak'], row['high'])
                        pos['sl'] = max(pos['sl'], pos['peak'] * (1 - cb))
                    if row['low'] <= pos['sl']:
                        exit_price = pos['sl']
                    elif not pos['trailing'] and row['high'] >= pos['tp']:
                        exit_price = pos['tp']
                else:
                    if not pos['trailing'] and row['low'] <= pos['act']:
                        pos['trailing'] = True
                    if pos['trailing']:
                        pos['peak'] = min(pos['peak'], row['low'])
                        pos['sl'] = min(pos['sl'], pos['peak'] * (1 + cb))
                    if row['high'] >= pos['sl']:
                        exit_price = pos['sl']
                    elif not pos['trailing'] and row['low'] <= pos['tp']:
                        exit_price = pos['tp']

                if exit_price:
                    pnl_pct = (exit_price / pos['ep'] - 1) if pos['side'] == 'long' \
                        else (1 - exit_price / pos['ep'])
                    pnl_usd = pos['notional'] * pnl_pct - pos['notional'] * fee * 2
                    capital += pnl_usd
                    trades.append({
                        'side': pos['side'],
                        'entry_time': pos['entry_time'],
                        'entry_price': pos['ep'],
                        'exit_time': ts,
                        'exit_price': exit_price,
                        'pnl_usd': pnl_usd,
                    })
                    pos = None

            signal = row.get('sr_signal', 0)
            if not pos and signal != 0 and capital > 0:
                ep = row['close']
                atr_val = row.get('atr', 0)
                if atr_val <= 0:
                    continue
                sl_dist = max(atr_val * atr_sl, ep * min_sl)
                risk_usd = capital * rpt
                sl_pct = sl_dist / ep
                if sl_pct <= 0:
                    continue
                notional = min(risk_usd / sl_pct, capital * 10, 1_000_000)
                margin = notional / lev
                if margin > capital:
                    continue

                side = 'long' if signal == 1 else 'short'
                sl = (ep - sl_dist) if side == 'long' else (ep + sl_dist)
                tp = (ep + sl_dist * rrr) if side == 'long' else (ep - sl_dist * rrr)
                act = (ep + sl_dist * act_rr) if side == 'long' else (ep - sl_dist * act_rr)

                pos = {
                    'side': side, 'ep': ep, 'sl': sl, 'tp': tp,
                    'act': act, 'notional': notional,
                    'trailing': False, 'peak': ep,
                    'entry_time': ts,
                }

    except Exception as e:
        logger.warning(f"Fehler bei Trade-Extraktion: {e}")
        import traceback
        traceback.print_exc()

    return trades


# ---------------------------------------------------------------------------
# Equity Curve aus Trades aufbauen
# ---------------------------------------------------------------------------

def build_equity_curve(df: pd.DataFrame, trades: list, start_capital: float) -> pd.DataFrame:
    equity = start_capital
    trade_events = sorted(
        [{'time': pd.to_datetime(t['exit_time']), 'pnl_usd': t['pnl_usd']}
         for t in trades],
        key=lambda x: x['time']
    )

    equity_data = []
    t_idx = 0
    for ts, _ in df.iterrows():
        while t_idx < len(trade_events) and trade_events[t_idx]['time'] <= ts:
            equity += trade_events[t_idx]['pnl_usd']
            t_idx += 1
        equity_data.append({'timestamp': ts, 'equity': equity})

    eq_df = pd.DataFrame(equity_data).set_index('timestamp')
    return eq_df


# ---------------------------------------------------------------------------
# Interaktiver Chart (Plotly)
# ---------------------------------------------------------------------------

def create_interactive_chart(symbol, timeframe, df, sr_zones, trades, equity_df,
                              stats, start_date, end_date, window=None,
                              start_capital=1000):
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        logger.error("plotly nicht installiert. Bitte: pip install plotly")
        return None

    # Zeitraum-Filter
    if window:
        cutoff = datetime.now(timezone.utc) - timedelta(days=window)
        df = df[df.index >= cutoff].copy()
    if start_date:
        df = df[df.index >= pd.to_datetime(start_date, utc=True)]
    if end_date:
        df = df[df.index <= pd.to_datetime(end_date, utc=True)]

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # ===== CANDLESTICKS =====
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df['open'], high=df['high'],
        low=df['low'], close=df['close'],
        name='OHLC',
        increasing_line_color='#16a34a',
        decreasing_line_color='#dc2626',
        showlegend=True,
    ), secondary_y=False)

    # ===== S/R ZONEN als horizontale Baender =====
    if df.index.min() and df.index.max():
        x0 = df.index.min()
        x1 = df.index.max()
        for i, z in enumerate(sr_zones):
            mid = (z['hi'] + z['lo']) / 2
            strength = z.get('strength', 1)
            # Faerbe nach Position relativ zum letzten Close
            last_close = df['close'].iloc[-1] if len(df) > 0 else mid
            color_fill = 'rgba(34,197,94,0.15)' if mid >= last_close else 'rgba(239,68,68,0.15)'
            color_line = 'rgba(34,197,94,0.6)' if mid >= last_close else 'rgba(239,68,68,0.6)'

            # Zone als Rectangle (Shapes)
            fig.add_shape(
                type='rect',
                x0=x0, x1=x1,
                y0=z['lo'], y1=z['hi'],
                fillcolor=color_fill,
                line=dict(color=color_line, width=1, dash='dot'),
                layer='below',
            )
            # Midline + Label
            fig.add_shape(
                type='line',
                x0=x0, x1=x1,
                y0=mid, y1=mid,
                line=dict(color=color_line, width=1),
                layer='below',
            )
            label_side = 'Resistance' if mid >= last_close else 'Support'
            fig.add_annotation(
                x=x1, y=mid,
                text=f" {label_side} ({strength}x)",
                showarrow=False,
                xanchor='left',
                font=dict(size=10, color=color_line),
                yref='y',
            )

    # ===== TRADE-SIGNALE =====
    entry_long_x, entry_long_y = [], []
    exit_long_x,  exit_long_y  = [], []
    entry_short_x, entry_short_y = [], []
    exit_short_x,  exit_short_y  = [], []

    for t in trades:
        et = pd.to_datetime(t['entry_time'])
        xt = pd.to_datetime(t['exit_time'])
        if t['side'] == 'long':
            entry_long_x.append(et);  entry_long_y.append(t['entry_price'])
            exit_long_x.append(xt);   exit_long_y.append(t['exit_price'])
        else:
            entry_short_x.append(et); entry_short_y.append(t['entry_price'])
            exit_short_x.append(xt);  exit_short_y.append(t['exit_price'])

    if entry_long_x:
        fig.add_trace(go.Scatter(
            x=entry_long_x, y=entry_long_y, mode='markers',
            marker=dict(color='#16a34a', symbol='triangle-up', size=14,
                        line=dict(width=1.2, color='#0f5132')),
            name='Entry Long', showlegend=True,
        ), secondary_y=False)

    if exit_long_x:
        fig.add_trace(go.Scatter(
            x=exit_long_x, y=exit_long_y, mode='markers',
            marker=dict(color='#22d3ee', symbol='circle', size=12,
                        line=dict(width=1.1, color='#0e7490')),
            name='Exit Long', showlegend=True,
        ), secondary_y=False)

    if entry_short_x:
        fig.add_trace(go.Scatter(
            x=entry_short_x, y=entry_short_y, mode='markers',
            marker=dict(color='#f59e0b', symbol='triangle-down', size=14,
                        line=dict(width=1.2, color='#92400e')),
            name='Entry Short', showlegend=True,
        ), secondary_y=False)

    if exit_short_x:
        fig.add_trace(go.Scatter(
            x=exit_short_x, y=exit_short_y, mode='markers',
            marker=dict(color='#ef4444', symbol='diamond', size=12,
                        line=dict(width=1.1, color='#7f1d1d')),
            name='Exit Short', showlegend=True,
        ), secondary_y=False)

    # ===== EQUITY CURVE (rechte Y-Achse) =====
    if not equity_df.empty and 'equity' in equity_df.columns:
        fig.add_trace(go.Scatter(
            x=equity_df.index, y=equity_df['equity'],
            name='Kontostand',
            line=dict(color='#2563eb', width=2),
            opacity=0.75,
            showlegend=True,
        ), secondary_y=True)

    # ===== TITEL =====
    pnl_pct = stats.get('total_pnl_pct', 0)
    end_cap = equity_df['equity'].iloc[-1] if not equity_df.empty else start_capital
    title_text = (
        f"{symbol} {timeframe} - StBot SRv2 | "
        f"Start: ${start_capital:.0f} | "
        f"End: ${end_cap:.0f} | "
        f"PnL: {'+' if pnl_pct >= 0 else ''}{pnl_pct:.2f}% | "
        f"Max DD: {stats.get('max_drawdown_pct', 0) * 100:.2f}% | "
        f"Trades: {stats.get('trades_count', len(trades))} | "
        f"Win Rate: {stats.get('win_rate', 0):.1f}%"
    )

    fig.update_layout(
        title=dict(text=title_text, font=dict(size=13), x=0.5, xanchor='center'),
        height=720,
        hovermode='x unified',
        template='plotly_white',
        dragmode='zoom',
        xaxis=dict(rangeslider=dict(visible=True), fixedrange=False),
        yaxis=dict(fixedrange=False),
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='center', x=0.5),
        showlegend=True,
    )
    fig.update_yaxes(title_text='Preis (USDT)', secondary_y=False)
    fig.update_yaxes(title_text='Kontostand (USDT)', secondary_y=True)

    return fig


# ---------------------------------------------------------------------------
# Haupt-Funktion
# ---------------------------------------------------------------------------

def main():
    selected_configs = select_configs()

    print("\n" + "=" * 60)
    print("Chart-Optionen:")
    print("=" * 60)
    start_date = input("Startdatum (YYYY-MM-DD) [leer=beliebig]:  ").strip() or None
    end_date   = input("Enddatum   (YYYY-MM-DD) [leer=heute]:     ").strip() or None
    cap_input  = input("Startkapital (USDT)     [Standard: 1000]: ").strip()
    start_capital = int(cap_input) if cap_input.isdigit() else 1000
    win_input  = input("Letzten N Tage anzeigen [leer=alle]:      ").strip()
    window     = int(win_input) if win_input.isdigit() else None
    tg_input   = input("Telegram versenden? (j/n) [Standard: n]:  ").strip().lower()
    send_telegram = tg_input in ['j', 'y', 'yes']

    try:
        with open(os.path.join(PROJECT_ROOT, 'secret.json'), 'r') as f:
            secrets = json.load(f)
    except Exception as e:
        logger.error(f"Fehler beim Laden von secret.json: {e}")
        sys.exit(1)

    telegram_config = secrets.get('telegram', {})

    end_date_load = end_date or datetime.now(timezone.utc).strftime('%Y-%m-%d')
    start_date_load = start_date or (
        datetime.now(timezone.utc) - timedelta(days=365)
    ).strftime('%Y-%m-%d')

    for filename, filepath in selected_configs:
        try:
            logger.info(f"\nVerarbeite {filename}...")

            with open(filepath, 'r') as f:
                config = json.load(f)

            symbol    = config['market']['symbol']
            timeframe = config['market']['timeframe']

            logger.info(f"Lade OHLCV-Daten fuer {symbol} {timeframe}...")
            df = load_data(symbol, timeframe, start_date_load, end_date_load)
            if df is None or df.empty:
                logger.warning(f"Keine Daten fuer {symbol} {timeframe}")
                continue

            # ATR berechnen (benoetigt SR Engine + Trade-Extraktion)
            try:
                import ta as ta_lib
                atr_ind = ta_lib.volatility.AverageTrueRange(
                    high=df['high'], low=df['low'], close=df['close'], window=14)
                df['atr'] = atr_ind.average_true_range()
                df.dropna(subset=['atr'], inplace=True)
            except Exception as e:
                logger.warning(f"ATR-Berechnung fehlgeschlagen: {e}")

            # S/R Zonen
            logger.info("Berechne S/R-Zonen...")
            sr_zones = compute_last_sr_zones(df, config)
            logger.info(f"  {len(sr_zones)} Zonen gefunden")

            # Trades extrahieren
            logger.info("Extrahiere Trades fuer Chart-Markierungen...")
            trades = extract_trades(df, config, start_capital)
            logger.info(f"  {len(trades)} Trades gefunden")

            # Equity Curve
            strategy_params = {**config.get('strategy', {}),
                               'symbol': symbol, 'timeframe': timeframe,
                               'htf': config['market'].get('htf')}
            stats = run_backtest(df.copy(), strategy_params, config.get('risk', {}),
                                 start_capital, verbose=False)
            equity_df = build_equity_curve(df, trades, start_capital)

            # Chart erstellen
            logger.info("Erstelle interaktiven Chart...")
            fig = create_interactive_chart(
                symbol, timeframe, df,
                sr_zones, trades, equity_df,
                stats, start_date, end_date,
                window, start_capital,
            )

            if fig is None:
                continue

            safe_name = f"{symbol.replace('/', '_').replace(':', '_')}_{timeframe}"
            output_file = f"/tmp/stbot_{safe_name}.html"
            fig.write_html(output_file)
            logger.info(f"\u2705 Chart gespeichert: {output_file}")

            if send_telegram and telegram_config.get('bot_token'):
                try:
                    from stbot.utils.telegram import send_document
                    send_document(
                        telegram_config['bot_token'],
                        telegram_config['chat_id'],
                        output_file,
                        caption=f"StBot SRv2 Chart: {symbol} {timeframe}",
                    )
                    logger.info("\u2705 Chart via Telegram versendet")
                except Exception as e:
                    logger.warning(f"Telegram-Versand fehlgeschlagen: {e}")

        except Exception as e:
            logger.error(f"Fehler bei {filename}: {e}", exc_info=True)
            continue

    logger.info("\n\u2705 Alle Charts generiert!")


if __name__ == '__main__':
    main()
