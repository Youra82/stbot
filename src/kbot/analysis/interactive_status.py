#!/usr/bin/env python3
"""
Interactive Charts für KBot - Stoch‑RSI
Verhält sich wie `stbot`'s interactive_status.py, aber verwendet ausschließlich KBot-Strategien/Backtester.
Zeigt Candlestick-Chart mit Entry/Exit-Signalen und Equity-Curve.
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
from kbot.analysis.backtester import run_backtest, load_data
from kbot.strategy.stochrsi_engine import StochRSIEngine

def choose_telegram_config(secrets: dict) -> dict:
    """Return the telegram config to use for KBot (no external fallbacks).

    Priority:
      1) top-level 'telegram' in secrets
      2) nested 'telegram' under first 'kbot' account
      3) return {} (do not fallback to other bots)
    """
    if not isinstance(secrets, dict):
        return {}

    # 1) top-level
    top = secrets.get('telegram') or {}
    if isinstance(top, dict) and top.get('bot_token') and top.get('chat_id'):
        return top

    # 2) nested under kbot account
    kbot_accounts = secrets.get('kbot') or []
    if isinstance(kbot_accounts, list) and len(kbot_accounts) > 0 and isinstance(kbot_accounts[0], dict):
        acct = kbot_accounts[0]
        if isinstance(acct.get('telegram'), dict):
            kbot_tele = acct.get('telegram')
            if kbot_tele.get('bot_token') and kbot_tele.get('chat_id'):
                return kbot_tele
        elif acct.get('bot_token') and acct.get('chat_id'):
            return {'bot_token': acct.get('bot_token'), 'chat_id': acct.get('chat_id')}

    # 3) explicit NO FALLBACK to other bots
    return {}

def setup_logging():
    logger = logging.getLogger('kbot.interactive_status')
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        logger.addHandler(ch)
    return logger


logger = setup_logging()


def get_config_files():
    configs_dir = os.path.join(PROJECT_ROOT, 'src', 'kbot', 'strategy', 'configs')
    if not os.path.exists(configs_dir):
        return []

    configs = []
    for filename in sorted(os.listdir(configs_dir)):
        if filename.startswith('config_') and filename.endswith('.json'):
            filepath = os.path.join(configs_dir, filename)
            configs.append((filename, filepath))
    return configs


def select_configs():
    configs = get_config_files()
    if not configs:
        logger.error('Keine Konfigurationsdateien gefunden!')
        sys.exit(1)

    print('\n' + '=' * 60)
    print('Verfügbare Konfigurationen:')
    print('=' * 60)
    for idx, (filename, _) in enumerate(configs, 1):
        clean_name = filename.replace('config_', '').replace('.json', '')
        print(f"{idx:2d}) {clean_name}")
    print('=' * 60)

    selection = input('\nAuswahl (z.B. 1 oder 1,3 oder "alle"): ').strip()
    if not selection:
        logger.error('Keine Auswahl getroffen')
        sys.exit(1)

    if selection.lower() == 'alle':
        return configs

    selected_indices = []
    for part in selection.replace(',', ' ').split():
        try:
            idx = int(part)
            if 1 <= idx <= len(configs):
                selected_indices.append(idx - 1)
        except ValueError:
            logger.warning(f'Ignoriere ungültige Eingabe: {part}')

    if not selected_indices:
        logger.error('Keine gültigen Konfigurationen gewählt!')
        sys.exit(1)

    return [configs[i] for i in selected_indices]


def load_config(filepath: str) -> dict:
    with open(filepath, 'r') as f:
        return json.load(f)


def run_backtest_for_chart(df: pd.DataFrame, config: dict, start_capital: float = 1000):
    """Führt den KBot-Backtester aus und liefert Trades, Equity-DF und Stats."""
    try:
        # run_backtest returns stats or (stats, equity_snapshots) when return_equity=True
        result = run_backtest(df.copy(), config, start_capital=start_capital, verbose=False, return_equity=True)
        if isinstance(result, tuple) and len(result) == 2:
            stats, equity_snapshots = result
        else:
            stats = result
            equity_snapshots = []

        equity_df = pd.DataFrame(equity_snapshots) if equity_snapshots else pd.DataFrame()

        # Normalize equity timestamps robustly (accept numeric epochs in s/ms/us/ns and ISO strings)
        def _infer_unit(v):
            try:
                n = abs(int(v))
            except Exception:
                return None
            if n > 10**17:
                return 'ns'
            if n > 10**14:
                return 'us'
            if n > 10**11:
                return 'ms'
            if n > 10**9:
                return 's'
            return None

        if not equity_df.empty and 'timestamp' in equity_df.columns:
            # if column is numeric -> convert with inferred unit
            if pd.api.types.is_numeric_dtype(equity_df['timestamp']):
                # infer by inspecting a max value
                maxv = int(equity_df['timestamp'].max())
                unit = _infer_unit(maxv) or 's'
                equity_df['timestamp'] = pd.to_datetime(equity_df['timestamp'].astype('int64'), unit=unit, utc=True)
            else:
                # let pandas parse strings/datetimes and coerce invalids
                equity_df['timestamp'] = pd.to_datetime(equity_df['timestamp'], utc=True, errors='coerce')
            # drop any rows where timestamp could not be parsed
            equity_df = equity_df[~equity_df['timestamp'].isna()].copy()
            equity_df.set_index('timestamp', inplace=True)

        return stats, equity_df
    except Exception as e:
        logger.warning(f'Fehler bei Backtest-Simulation: {e}')
        return {}, pd.DataFrame()


def create_interactive_chart(symbol, timeframe, df, trades, equity_df, stats, start_date, end_date, window=None, start_capital=1000):
    # Filter window / dates
    if window:
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=window)
        df = df[df.index >= cutoff_date].copy()

    if start_date:
        df = df[df.index >= pd.to_datetime(start_date, utc=True)]
    if end_date:
        df = df[df.index <= pd.to_datetime(end_date, utc=True)]

    # Ensure indicators exist
    engine = StochRSIEngine(settings=stats.get('strategy', {}))
    df = engine.process_dataframe(df.copy())

    # Robust timestamp helpers — infer unit (s/ms/us/ns) for numeric epoch values
    def _infer_time_unit(v):
        try:
            v = abs(int(v))
        except Exception:
            return None
        if v > 10**17:
            return 'ns'
        if v > 10**14:
            return 'us'
        if v > 10**11:
            return 'ms'
        if v > 10**9:
            return 's'
        return None

    def _to_dt(x):
        if x is None:
            return None
        # already datetime-like
        if isinstance(x, pd.Timestamp) or hasattr(x, 'tzinfo') or hasattr(x, 'isoformat') and isinstance(x, (str,)) and 'T' in x:
            try:
                return pd.to_datetime(x, utc=True)
            except Exception:
                pass
        # numeric-like values (try to detect unit)
        try:
            num = float(x)
            if not pd.isna(num):
                unit = _infer_time_unit(num)
                if unit:
                    return pd.to_datetime(int(num), unit=unit, utc=True)
        except Exception:
            pass
        # fallback — let pandas try
        return pd.to_datetime(x, utc=True, errors='coerce')

    # normalize equity_df index if present
    if not (equity_df is None or (isinstance(equity_df, pd.DataFrame) and equity_df.empty)):
        try:
            if pd.api.types.is_numeric_dtype(equity_df.index):
                maxv = int(equity_df.index.astype('int64').max())
                unit = _infer_time_unit(maxv) or 's'
                equity_df.index = pd.to_datetime(equity_df.index.astype('int64'), unit=unit, utc=True)
            else:
                equity_df.index = pd.to_datetime(equity_df.index, utc=True, errors='coerce')
        except Exception:
            equity_df.index = pd.to_datetime(equity_df.index, utc=True, errors='coerce')

    # Use the same top-panel layout as StBot: candles + equity on secondary y, smaller indicator panel below
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        vertical_spacing=0.06, row_heights=[0.72, 0.28], specs=[[{"secondary_y": True}], [{}]])

    # Candles (styled like StBot)
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df['open'], high=df['high'], low=df['low'], close=df['close'],
        name='OHLC', increasing_line_color="#16a34a", decreasing_line_color="#dc2626", showlegend=True
    ), row=1, col=1)

    # NOTE: channel_top/channel_bot are synthetic ATR envelopes kept for SL/TP calculations in the engine.
    # We intentionally DO NOT plot them here to avoid confusion with legacy LTBBot channels.

    # Equity curve on secondary y of top subplot
    if not equity_df.empty and 'equity' in equity_df.columns:
        fig.add_trace(go.Scatter(x=equity_df.index, y=equity_df['equity'], name='Equity', line=dict(color='#3b82f6', width=2)), row=1, col=1, secondary_y=True)

    # Plot StochRSI K/D in second subplot
    if 'stochrsi_k' in df.columns and 'stochrsi_d' in df.columns:
        fig.add_trace(go.Scatter(x=df.index, y=df['stochrsi_k'], name='StochRSI K', line=dict(color='#0ea5e9')), row=2, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df['stochrsi_d'], name='StochRSI D', line=dict(color='#f97316')), row=2, col=1)
        # add OB/OS bands
        ob = getattr(engine, 'ob', 0.8)
        os_ = getattr(engine, 'os', 0.2)
        fig.add_hline(y=ob, line_dash='dash', line_color='#9ca3af', row=2, col=1)
        fig.add_hline(y=os_, line_dash='dash', line_color='#9ca3af', row=2, col=1)

    # Plot trade markers
    entry_long_x, entry_long_y = [], []
    exit_long_x, exit_long_y = [], []
    entry_short_x, entry_short_y = [], []
    exit_short_x, exit_short_y = [], []

    for trade in trades:
        # parse times robustly (accept ints in s/ms/us/ns as well as ISO strings / datetimes)
        if 'entry_long' in trade:
            t = trade['entry_long']
            if t.get('time') and t.get('price'):
                ts = _to_dt(t['time'])
                if not pd.isna(ts):
                    entry_long_x.append(ts)
                    entry_long_y.append(t['price'])
        if 'exit_long' in trade:
            t = trade['exit_long']
            if t.get('time') and t.get('price'):
                ts = _to_dt(t['time'])
                if not pd.isna(ts):
                    exit_long_x.append(ts)
                    exit_long_y.append(t['price'])
        if 'entry_short' in trade:
            t = trade['entry_short']
            if t.get('time') and t.get('price'):
                ts = _to_dt(t['time'])
                if not pd.isna(ts):
                    entry_short_x.append(ts)
                    entry_short_y.append(t['price'])
        if 'exit_short' in trade:
            t = trade['exit_short']
            if t.get('time') and t.get('price'):
                ts = _to_dt(t['time'])
                if not pd.isna(ts):
                    exit_short_x.append(ts)
                    exit_short_y.append(t['price'])

    if entry_long_x:
        fig.add_trace(go.Scatter(x=entry_long_x, y=entry_long_y, mode='markers', marker=dict(color='#16a34a', symbol='triangle-up', size=12), name='Entry Long'), row=1, col=1)
    if exit_long_x:
        fig.add_trace(go.Scatter(x=exit_long_x, y=exit_long_y, mode='markers', marker=dict(color='#22d3ee', symbol='circle', size=10), name='Exit Long'), row=1, col=1)
    if entry_short_x:
        fig.add_trace(go.Scatter(x=entry_short_x, y=entry_short_y, mode='markers', marker=dict(color='#f59e0b', symbol='triangle-down', size=12), name='Entry Short'), row=1, col=1)
    if exit_short_x:
        fig.add_trace(go.Scatter(x=exit_short_x, y=exit_short_y, mode='markers', marker=dict(color='#ef4444', symbol='diamond', size=10), name='Exit Short'), row=1, col=1)

    # Title & layout
    pnl_pct = stats.get('total_pnl_pct', 0)
    trades_count = stats.get('trades_count', 0)
    win_rate = stats.get('win_rate', 0)
    max_dd = stats.get('max_drawdown_pct', 0)
    end_capital = stats.get('end_capital', start_capital)

    title = f"{symbol} {timeframe} — KBot (Stoch‑RSI) | PnL: {pnl_pct:+.2f}% | Trades: {trades_count} | WinRate: {win_rate:.1f}% | DD: {max_dd:.2f}%"

    fig.update_layout(title=title, height=720, hovermode='x unified', template='plotly_white')
    # make rangeslider visible and explicitly set its background so templates can't inject unexpected fills
    fig.update_xaxes(rangeslider_visible=True, rangeslider=dict(bgcolor='white'))
    fig.update_yaxes(title_text='Preis', row=1, col=1)
    fig.update_yaxes(title_text='Stoch‑RSI', row=2, col=1)

    # Defensive sanitization: remove unexpected filled shapes/traces that caused a large green rectangle in the saved HTML
    # 1) keep only horizontal StochRSI OB/OS lines (yref == 'y3') in layout.shapes
    shapes = list(getattr(fig.layout, 'shapes', []) or [])
    cleaned_shapes = []
    for s in shapes:
        stype = getattr(s, 'type', None)
        yref = getattr(s, 'yref', None)
        if stype == 'line' and yref == 'y3':
            cleaned_shapes.append(s)
    fig.layout.shapes = cleaned_shapes

    # 2) ensure no scatter trace has an accidental area fill
    fig.update_traces(fill='none', selector=dict(type='scatter'))

    # 3) force plot and paper background to white (defensive)
    fig.update_layout(plot_bgcolor='white', paper_bgcolor='white')

    return fig


def main():
    selected = select_configs()

    print('\n' + '=' * 60)
    print('Chart-Optionen:')
    print('=' * 60)
    # Match StBot UX: empty = default behaviour
    start_date = input('Startdatum (YYYY-MM-DD) [leer=beliebig]: ').strip() or None
    end_date = input('Enddatum (YYYY-MM-DD) [leer=Heute]: ').strip() or None
    window_input = input('Letzten N Tage anzeigen [leer=alle]: ').strip()
    window = int(window_input) if window_input.isdigit() else None
    send_telegram = input('Telegram versenden? (j/n) [Standard: n]: ').strip().lower() in ['j','y','yes']

    # secret.json optional: prefer Exchange when configured, else fall back to cached data
    secret_path = os.path.join(PROJECT_ROOT, 'secret.json')
    secrets = {}
    try:
        logger.info(f"Lese secret.json von: {secret_path}")
        with open(secret_path, 'r') as f:
            secrets = json.load(f)
        logger.info(f"Secret keys: {list(secrets.keys())}")
    except Exception:
        logger.info('secret.json nicht gefunden oder ungültig — benutze lokale Cache, falls vorhanden')

    account = (secrets.get('kbot') or [None])[0]
    if account:
        exchange = Exchange(account)
    else:
        exchange = None
        logger.warning('Keine KBot-API-Konfiguration gefunden — verwende lokale Cache-Daten (wenn vorhanden)')

    # Choose telegram config using helper (strict: KBot only, no workspace fallbacks)
    telegram_config = choose_telegram_config(secrets)
    if telegram_config:
        logger.info('Verwende Telegram-Credentials aus: kbot/secret.json')
    else:
        logger.warning('Keine gültigen Telegram-Credentials in kbot/secret.json — Telegram wird nicht gesendet.')

    for filename, filepath in selected:
        try:
            logger.info(f"Verarbeite {filename}...")
            config = load_config(filepath)
            symbol = config['market']['symbol']
            timeframe = config['market']['timeframe']

            # Auswahl der Ladezeit
            if not start_date:
                start_date_for_load = (datetime.now(timezone.utc) - timedelta(days=30)).strftime('%Y-%m-%d')
            else:
                start_date_for_load = start_date
            if not end_date:
                end_date_for_load = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            else:
                end_date_for_load = end_date

            logger.info(f'Lade OHLCV-Daten für {symbol} {timeframe}...')
            if exchange:
                df = exchange.fetch_historical_ohlcv(symbol, timeframe, start_date_for_load, end_date_for_load)
            else:
                # fallback auf lokal gecachte CSVs
                df = load_data(symbol, timeframe, start_date_for_load, end_date_for_load)

            if df is None or df.empty:
                logger.warning(f'Keine Daten für {symbol} {timeframe} im Zeitraum {start_date_for_load} - {end_date_for_load}')
                continue

            logger.info('Führe Backtest (für Chart-Daten) aus...')
            stats, equity_df = run_backtest_for_chart(df.copy(), config, start_capital=1000)
            trades = stats.get('trades', [])

            logger.info('Erstelle interaktives Chart...')
            fig = create_interactive_chart(symbol, timeframe, df, trades, equity_df, stats, start_date, end_date, window, 1000)

            # Speicherort
            out_dir = os.path.join(PROJECT_ROOT, 'artifacts', 'interactive_charts')
            os.makedirs(out_dir, exist_ok=True)
            safe_name = f"{symbol.replace('/', '_').replace(':','_')}_{timeframe}"
            out_file = os.path.join(out_dir, f"kbot_{safe_name}.html")
            fig.write_html(out_file)
            logger.info(f'Chart gespeichert: {out_file}')

            # Optional: Telegram versenden (wie StBot)
            if send_telegram:
                logger.info(f"telegram_config present: {bool(telegram_config)} | keys={list(telegram_config.keys()) if isinstance(telegram_config, dict) else telegram_config}")
                # Re-read kbot/secret.json just before sending to ensure latest creds are used
                try:
                    fresh = {}
                    with open(os.path.join(PROJECT_ROOT, 'secret.json'), 'r') as sf:
                        fresh = json.load(sf)
                    # top-level telegram preferred
                    fresh_top = fresh.get('telegram') or {}
                    if fresh_top.get('bot_token') and fresh_top.get('chat_id'):
                        bot_token = fresh_top.get('bot_token')
                        chat_id = fresh_top.get('chat_id')
                        logger.info('Verwende telegram creds aus kbot/secret.json (fresh read top-level)')
                    else:
                        # nested kbot account
                        kacc = (fresh.get('kbot') or [])
                        if kacc and isinstance(kacc, list) and isinstance(kacc[0], dict):
                            nested = kacc[0].get('telegram') or {}
                            if nested.get('bot_token') and nested.get('chat_id'):
                                bot_token = nested.get('bot_token')
                                chat_id = nested.get('chat_id')
                                logger.info('Verwende telegram creds aus kbot/secret.json (fresh read nested)')
                except Exception:
                    # ignore and fall back to the earlier-determined telegram_config
                    pass

                bot_token = telegram_config.get('bot_token') if 'bot_token' not in locals() else bot_token
                chat_id = telegram_config.get('chat_id') if 'chat_id' not in locals() else chat_id
                logger.info(f"bot_token present: {bool(bot_token)}, chat_id present: {bool(chat_id)}")
                if bot_token and chat_id:
                    try:
                        logger.info('Sende Chart via Telegram...')
                        from kbot.utils.telegram import send_document
                        ok = send_document(bot_token, chat_id, out_file, caption=f"Chart: {symbol} {timeframe}")
                        if ok:
                            logger.info('Telegram: Chart erfolgreich gesendet.')
                        else:
                            logger.warning('Telegram: Versand fehlgeschlagen - prüfe Logs.')
                    except Exception as e:
                        logger.warning(f'Konnte Chart nicht via Telegram versenden: {e}')
                else:
                    logger.warning('Telegram-Credentials fehlen oder unvollständig; überspringe Versand.')

        except Exception as e:
            logger.error(f'Fehler bei {filename}: {e}', exc_info=True)
            continue

    logger.info('\n✅ Interaktive Charts (KBot) erstellt!')


if __name__ == '__main__':
    main()
