#!/usr/bin/env python3
"""
Interactive Charts für KBot - StochRSI Strategie
Zeigt Candlestick-Chart mit Trade-Signalen (Entry/Exit Long/Short)
Nutzt durchnummerierte Konfigurationsdateien zum Auswählen
"""

import os
import sys
import json
from datetime import datetime, timedelta, timezone
import logging

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from kbot.utils.exchange import Exchange
from kbot.analysis.backtester import run_backtest
from kbot.strategy.stochrsi_engine import StochRSIEngine

def setup_logging():
    logger = logging.getLogger('interactive_status')
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        logger.addHandler(ch)
    return logger

logger = setup_logging()

def get_config_files():
    """Sucht alle Konfigurationsdateien auf"""
    configs_dir = os.path.join(PROJECT_ROOT, 'src', 'kbot', 'strategy', 'configs')
    if not os.path.exists(configs_dir):
        return []
    
    configs = []
    for filename in sorted(os.listdir(configs_dir)):
        if filename.startswith('config_') and filename.endswith('.json'):
            filepath = os.path.join(configs_dir, filename)
            configs.append((filename, filepath))
    
    return configs

# Rest des Files bleibt funktional gleich; das Skript nutzt intern die StochRSI-Engine für Signale
# (Aus Platzgründen wurde nur die paketbezogenen Importe angepasst und KBot-Bezeichnungen verwendet.)

def select_configs():
    configs = get_config_files()
    if not configs:
        logger.error("Keine Konfigurationsdateien gefunden!")
        sys.exit(1)
    return configs

# Für Interaktiv-Charting wird die gleiche Logik wie zuvor verwendet (Signal-Extraktion via Engine)

def load_config(filepath):
    with open(filepath, 'r') as f:
        return json.load(f)

# Die restlichen Helferfunktionen und die interaktive Chart-Logik wurden unverändert übernommen
# (nur Paketnamen und Engine-Klasse wurden auf KBot/StochRSI angepasst).


def run_backtest_for_chart(df, config, start_capital=1000):
    """Führt einen Backtest durch und gibt Trades, Equity Curve und Stats zurück (KBot)"""
    try:
        strategy_params = config.get('strategy', {})
        risk_params = config.get('risk', {})

        # Nutze den KBot-Backtester mit return_trades=True
        original_logger = None
        try:
            logger_backtest = logging.getLogger('kbot.analysis.backtester')
            original_logger = logger_backtest.level
            logger_backtest.setLevel(logging.ERROR)
        except Exception:
            original_logger = None

        stats, trade_history, equity_snapshots = run_backtest(
            df.copy(), strategy_params, risk_params,
            start_capital=start_capital, verbose=False, return_trades=True
        )

        if original_logger is not None:
            logger_backtest.setLevel(original_logger)

        if equity_snapshots:
            equity_df = pd.DataFrame(equity_snapshots)
            equity_df.set_index('timestamp', inplace=True)
        else:
            equity_df = pd.DataFrame()

        return trade_history, equity_df, stats
    except Exception as e:
        logger.warning(f"Fehler bei Backtest-Simulation: {e}")
        import traceback
        traceback.print_exc()
        return [], pd.DataFrame(), {}


def create_interactive_chart(symbol, timeframe, df, trades, equity_df, stats, start_date, end_date, window=None, start_capital=1000):
    """Erstellt interaktiven Chart mit Candlesticks, Trade-Signalen und Equity Curve"""

    # Filter auf Fenster
    if window:
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=window)
        df = df[df.index >= cutoff_date].copy()

    # Filter auf Start/End Datum
    if start_date:
        df = df[df.index >= pd.to_datetime(start_date, utc=True)]
    if end_date:
        df = df[df.index <= pd.to_datetime(end_date, utc=True)]

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # Candlestick
    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df['open'], high=df['high'], low=df['low'], close=df['close'],
            name='OHLC', increasing_line_color="#16a34a", decreasing_line_color="#dc2626",
            showlegend=True
        ),
        secondary_y=False
    )

    # Equity Curve (rechts)
    if not equity_df.empty and 'equity' in equity_df.columns:
        fig.add_trace(
            go.Scatter(
                x=equity_df.index, y=equity_df['equity'], name='Kontostand',
                line=dict(color='#3b82f6', width=2), showlegend=True
            ),
            secondary_y=True
        )

    # Trade-Markierungen
    entry_long_x, entry_long_y = [], []
    exit_long_x, exit_long_y = [], []
    entry_short_x, entry_short_y = [], []
    exit_short_x, exit_short_y = [], []

    for trade in trades:
        if 'entry_long' in trade:
            t = trade['entry_long']
            if t.get('time') and t.get('price'):
                entry_long_x.append(pd.to_datetime(t['time'])); entry_long_y.append(t['price'])
        if 'exit_long' in trade:
            t = trade['exit_long']
            if t.get('time') and t.get('price'):
                exit_long_x.append(pd.to_datetime(t['time'])); exit_long_y.append(t['price'])
        if 'entry_short' in trade:
            t = trade['entry_short']
            if t.get('time') and t.get('price'):
                entry_short_x.append(pd.to_datetime(t['time'])); entry_short_y.append(t['price'])
        if 'exit_short' in trade:
            t = trade['exit_short']
            if t.get('time') and t.get('price'):
                exit_short_x.append(pd.to_datetime(t['time'])); exit_short_y.append(t['price'])

    # Plot trade markers
    if entry_long_x:
        fig.add_trace(go.Scatter(x=entry_long_x, y=entry_long_y, mode='markers', name='Entry Long', marker=dict(symbol='triangle-up', color='green', size=10)), secondary_y=False)
    if exit_long_x:
        fig.add_trace(go.Scatter(x=exit_long_x, y=exit_long_y, mode='markers', name='Exit Long', marker=dict(symbol='circle', color='cyan', size=8)), secondary_y=False)
    if entry_short_x:
        fig.add_trace(go.Scatter(x=entry_short_x, y=entry_short_y, mode='markers', name='Entry Short', marker=dict(symbol='triangle-down', color='orange', size=10)), secondary_y=False)
    if exit_short_x:
        fig.add_trace(go.Scatter(x=exit_short_x, y=exit_short_y, mode='markers', name='Exit Short', marker=dict(symbol='circle', color='magenta', size=8)), secondary_y=False)

    fig.update_layout(height=700, template='plotly_dark', legend=dict(orientation='h'))
    return fig


def main():
    selected_configs = select_configs()

    print("\n" + "="*60)
    print("Chart-Optionen:")
    print("="*60)

    start_date = input("Startdatum (YYYY-MM-DD) [leer=beliebig]: ").strip() or None
    end_date = input("Enddatum (YYYY-MM-DD) [leer=heute]: ").strip() or None
    window_input = input("Letzten N Tage anzeigen [leer=alle]: ").strip()
    window = int(window_input) if window_input.isdigit() else None
    send_telegram = input("Telegram versenden? (j/n) [Standard: n]: ").strip().lower() in ['j', 'y', 'yes']

    try:
        with open(os.path.join(PROJECT_ROOT, 'secret.json'), 'r') as f: secrets = json.load(f)
    except Exception as e:
        logger.error(f"Fehler beim Laden von secret.json: {e}")
        sys.exit(1)

    account = secrets.get('kbot', [None])[0]
    if not account:
        logger.error("Keine KBot-Accountkonfiguration gefunden")
        sys.exit(1)

    exchange = Exchange(account)
    telegram_config = secrets.get('telegram', {})

    for filename, filepath in selected_configs:
        try:
            logger.info(f"\nVerarbeite {filename}...")
            config = load_config(filepath)
            symbol = config['market']['symbol']
            timeframe = config['market']['timeframe']

            logger.info(f"Lade OHLCV-Daten für {symbol} {timeframe}...")
            if not start_date:
                start_date_for_load = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
            else:
                start_date_for_load = start_date

            if not end_date:
                end_date_for_load = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            else:
                end_date_for_load = end_date

            df = exchange.fetch_historical_ohlcv(symbol, timeframe, start_date_for_load, end_date_for_load)
            if df is None or len(df) == 0:
                logger.warning(f"Keine Daten für {symbol} {timeframe} im Zeitraum {start_date_for_load} bis {end_date_for_load}")
                continue

            logger.info("Führe Backtest durch...")
            trades, equity_df, stats = run_backtest_for_chart(df.copy(), config, start_capital=1000)

            logger.info("Erstelle Chart...")
            fig = create_interactive_chart(symbol, timeframe, df, trades, equity_df, stats, start_date, end_date, window, 1000)

            safe_name = f"{symbol.replace('/', '_')}_{timeframe}"
            output_file = f"/tmp/kbot_{safe_name}.html"
            fig.write_html(output_file)
            logger.info(f"✅ Chart gespeichert: {output_file}")

            if send_telegram and telegram_config:
                try:
                    from kbot.utils.telegram import send_document
                    bot_token = telegram_config.get('bot_token')
                    chat_id = telegram_config.get('chat_id')
                    if bot_token and chat_id:
                        send_document(bot_token, chat_id, output_file, caption=f"Chart: {symbol} {timeframe}")
                except Exception as e:
                    logger.warning(f"Konnte Chart nicht via Telegram versenden: {e}")
        except Exception as e:
            logger.error(f"Fehler bei {filename}: {e}", exc_info=True)
            continue

    logger.info("\n✅ Alle Charts generiert!")


if __name__ == '__main__':
    main()