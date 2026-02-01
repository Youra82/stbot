#!/usr/bin/env python3
"""
Interactive Charts für StBot - SMC Strategie
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

from stbot.utils.exchange import Exchange
from stbot.analysis.backtester import run_backtest
from stbot.strategy.sr_engine import SREngine

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
    configs_dir = os.path.join(PROJECT_ROOT, 'src', 'stbot', 'strategy', 'configs')
    if not os.path.exists(configs_dir):
        return []
    
    configs = []
    for filename in sorted(os.listdir(configs_dir)):
        if filename.startswith('config_') and filename.endswith('.json'):
            filepath = os.path.join(configs_dir, filename)
            configs.append((filename, filepath))
    
    return configs

def select_configs():
    """Zeigt durchnummerierte Konfigurationsdateien und lässt User wählen"""
    configs = get_config_files()
    
    if not configs:
        logger.error("Keine Konfigurationsdateien gefunden!")
        sys.exit(1)
    
    print("\n" + "="*60)
    print("Verfügbare Konfigurationen:")
    print("="*60)
    for idx, (filename, _) in enumerate(configs, 1):
        # Extrahiere Symbol/Timeframe aus Dateiname
        clean_name = filename.replace('config_', '').replace('.json', '')
        print(f"{idx:2d}) {clean_name}")
    print("="*60)
    
    print("\nWähle Konfiguration(en) zum Anzeigen:")
    print("  Einzeln: z.B. '1' oder '5'")
    print("  Mehrfach: z.B. '1,3,5' oder '1 3 5'")
    
    selection = input("\nAuswahl: ").strip()
    
    # Parse Eingabe
    selected_indices = []
    for part in selection.replace(',', ' ').split():
        try:
            idx = int(part)
            if 1 <= idx <= len(configs):
                selected_indices.append(idx - 1)
            else:
                logger.warning(f"Index {idx} außerhalb des Bereichs")
        except ValueError:
            logger.warning(f"Ungültige Eingabe: {part}")
    
    if not selected_indices:
        logger.error("Keine gültigen Konfigurationen gewählt!")
        sys.exit(1)
    
    return [configs[i] for i in selected_indices]

def load_config(filepath):
    """Lädt eine Konfiguration"""
    with open(filepath, 'r') as f:
        return json.load(f)

def add_stbot_indicators(df):
    """Fügt Indikatoren für Chart-Anzeige hinzu (vereinfacht)"""
    # Kerzen-Daten sind bereits vorhanden, keine zusätzlichen Indikatoren nötig
    # Die eigentliche SMC-Analyse passiert in der Backtest-Funktion
    return df

def build_equity_curve(df, trades, start_capital):
    """
    Erstellt eine Equity Curve basierend auf den simulierten Trades
    """
    equity = start_capital
    equity_data = []
    
    # Sammle alle Trade-Events mit Zeitstempel
    trade_events = []
    for trade in trades:
        if 'exit_long' in trade:
            entry_price = trade.get('entry_long', {}).get('price', 0)
            exit_price = trade.get('exit_long', {}).get('price', 0)
            exit_time = trade.get('exit_long', {}).get('time')
            if entry_price and exit_price and exit_time:
                pnl_pct = (exit_price - entry_price) / entry_price
                trade_events.append({
                    'time': pd.to_datetime(exit_time),
                    'pnl_pct': pnl_pct,
                    'side': 'long'
                })
        
        if 'exit_short' in trade:
            entry_price = trade.get('entry_short', {}).get('price', 0)
            exit_price = trade.get('exit_short', {}).get('price', 0)
            exit_time = trade.get('exit_short', {}).get('time')
            if entry_price and exit_price and exit_time:
                pnl_pct = (entry_price - exit_price) / entry_price
                trade_events.append({
                    'time': pd.to_datetime(exit_time),
                    'pnl_pct': pnl_pct,
                    'side': 'short'
                })
    
    # Sortiere Trade-Events nach Zeit
    trade_events = sorted(trade_events, key=lambda x: x['time'])
    
    # Erstelle Equity Curve für jeden Timestamp im DataFrame
    trade_idx = 0
    for timestamp, row in df.iterrows():
        # Wende alle Trades bis zu diesem Timestamp an
        while trade_idx < len(trade_events) and trade_events[trade_idx]['time'] <= timestamp:
            trade = trade_events[trade_idx]
            equity += equity * trade['pnl_pct']
            trade_idx += 1
        
        equity_data.append({
            'timestamp': timestamp,
            'equity': equity
        })
    
    equity_df = pd.DataFrame(equity_data)
    equity_df.set_index('timestamp', inplace=True)
    return equity_df

def extract_trades_from_backtest(df, config, start_capital=1000):
    """
    Extrahiert Trade-Signale aus der SMC-Strategie für die Visualisierung
    Simuliert die gleiche Logik wie der Backtester mit SL/TP
    Liefert Entry/Exit Punkte für Long und Short Positionen
    """
    import ta
    trades = []
    try:
        # ATR berechnen für Stop-Loss
        atr_indicator = ta.volatility.AverageTrueRange(
            high=df['high'], low=df['low'], close=df['close'], window=14
        )
        df['atr'] = atr_indicator.average_true_range()
        
        # SREngine für Signale
        engine = SREngine(config.get('strategy', {}))
        df = engine.process_dataframe(df.copy())
        
        # Risk params
        risk_params = config.get('risk', {})
        risk_reward_ratio = risk_params.get('risk_reward_ratio', 2.0)
        atr_multiplier_sl = risk_params.get('atr_multiplier_sl', 2.0)
        min_sl_pct = risk_params.get('min_sl_pct', 0.3) / 100.0
        activation_rr = risk_params.get('trailing_stop_activation_rr', 2.0)
        callback_rate = risk_params.get('trailing_stop_callback_rate_pct', 1.0) / 100
        
        position = None
        
        for i in range(len(df)):
            row = df.iloc[i]
            timestamp = row.name
            close = row['close']
            high = row['high']
            low = row['low']
            current_atr = row.get('atr', 0)
            
            # sr_signal: 1 = Buy (Resistance Break), -1 = Sell (Support Break)
            sr_signal = row.get('sr_signal', 0)
            
            # Position Management
            if position:
                exit_price = None
                exit_reason = None
                
                if position['side'] == 'long':
                    # Trailing Stop aktivieren
                    if not position['trailing_active'] and high >= position['activation_price']:
                        position['trailing_active'] = True
                    
                    if position['trailing_active']:
                        position['peak_price'] = max(position['peak_price'], high)
                        trailing_sl = position['peak_price'] * (1 - callback_rate)
                        position['stop_loss'] = max(position['stop_loss'], trailing_sl)
                    
                    # Exit Check
                    if low <= position['stop_loss']:
                        exit_price = position['stop_loss']
                        exit_reason = 'SL'
                    elif not position['trailing_active'] and high >= position['take_profit']:
                        exit_price = position['take_profit']
                        exit_reason = 'TP'
                        
                elif position['side'] == 'short':
                    if not position['trailing_active'] and low <= position['activation_price']:
                        position['trailing_active'] = True
                    
                    if position['trailing_active']:
                        position['peak_price'] = min(position['peak_price'], low)
                        trailing_sl = position['peak_price'] * (1 + callback_rate)
                        position['stop_loss'] = min(position['stop_loss'], trailing_sl)
                    
                    if high >= position['stop_loss']:
                        exit_price = position['stop_loss']
                        exit_reason = 'SL'
                    elif not position['trailing_active'] and low <= position['take_profit']:
                        exit_price = position['take_profit']
                        exit_reason = 'TP'
                
                if exit_price:
                    trade = {
                        'entry_' + position['side']: {
                            'time': position['entry_time'].isoformat() if pd.notna(position['entry_time']) else None,
                            'price': float(position['entry_price'])
                        },
                        'exit_' + position['side']: {
                            'time': timestamp.isoformat() if pd.notna(timestamp) else None,
                            'price': float(exit_price)
                        }
                    }
                    trades.append(trade)
                    position = None
            
            # Entry Logic (nur wenn keine Position)
            if not position and sr_signal != 0 and current_atr > 0:
                entry_price = close
                sl_dist = max(current_atr * atr_multiplier_sl, entry_price * min_sl_pct)
                
                if sr_signal == 1:  # BUY Signal
                    sl = entry_price - sl_dist
                    tp = entry_price + sl_dist * risk_reward_ratio
                    act = entry_price + sl_dist * activation_rr
                    position = {
                        'side': 'long',
                        'entry_price': entry_price,
                        'entry_time': timestamp,
                        'stop_loss': sl,
                        'take_profit': tp,
                        'activation_price': act,
                        'trailing_active': False,
                        'peak_price': entry_price
                    }
                elif sr_signal == -1:  # SELL Signal
                    sl = entry_price + sl_dist
                    tp = entry_price - sl_dist * risk_reward_ratio
                    act = entry_price - sl_dist * activation_rr
                    position = {
                        'side': 'short',
                        'entry_price': entry_price,
                        'entry_time': timestamp,
                        'stop_loss': sl,
                        'take_profit': tp,
                        'activation_price': act,
                        'trailing_active': False,
                        'peak_price': entry_price
                    }
        
        return trades
    except Exception as e:
        logger.warning(f"Fehler bei Trade-Extraktion: {e}")
        import traceback
        traceback.print_exc()
        return []
        return []

def run_backtest_for_chart(df, config, start_capital=1000):
    """
    Führt einen Backtest durch und gibt Trades, Equity Curve und Stats zurück
    Nutzt den echten Backtester mit return_trades=True für konsistente Daten
    """
    try:
        strategy_params = config.get('strategy', {})
        risk_params = config.get('risk', {})
        
        # Backtester ausführen mit return_trades=True für konsistente Trade/Equity Daten
        logger_backtest = logging.getLogger('stbot.analysis.backtester')
        original_level = logger_backtest.level
        logger_backtest.setLevel(logging.ERROR)
        
        stats, trade_history, equity_snapshots = run_backtest(
            df.copy(), strategy_params, risk_params, 
            start_capital=start_capital, verbose=False, return_trades=True
        )
        
        logger_backtest.setLevel(original_level)
        
        # Equity Snapshots in DataFrame umwandeln
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
    
    # Erstelle Chart mit secondary_y für Equity Curve (wie UtBot2)
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    
    # === Candlestick Chart (primäre Y-Achse) ===
    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df['open'],
            high=df['high'],
            low=df['low'],
            close=df['close'],
            name='OHLC',
            increasing_line_color="#16a34a",
            decreasing_line_color="#dc2626",
            showlegend=True
        ),
        secondary_y=False
    )
    
    # === Equity Curve (sekundäre Y-Achse, rechts) ===
    if not equity_df.empty and 'equity' in equity_df.columns:
        fig.add_trace(
            go.Scatter(
                x=equity_df.index,
                y=equity_df['equity'],
                name='Kontostand',
                line=dict(color='#3b82f6', width=2),
                hovertemplate='<b>Kontostand</b><br>Zeit: %{x}<br>Equity: $%{y:.2f}<extra></extra>',
                showlegend=True
            ),
            secondary_y=True
        )
    
    # === Trade-Signale extrahieren und eintragen ===
    entry_long_x, entry_long_y = [], []
    exit_long_x, exit_long_y = [], []
    entry_short_x, entry_short_y = [], []
    exit_short_x, exit_short_y = [], []
    
    for trade in trades:
        # Entry Long (Dreieck nach oben, grün)
        if 'entry_long' in trade:
            entry_time = trade['entry_long'].get('time')
            entry_price = trade['entry_long'].get('price')
            if entry_time and entry_price:
                entry_long_x.append(pd.to_datetime(entry_time))
                entry_long_y.append(entry_price)
        
        # Exit Long (Kreis, Cyan)
        if 'exit_long' in trade:
            exit_time = trade['exit_long'].get('time')
            exit_price = trade['exit_long'].get('price')
            if exit_time and exit_price:
                exit_long_x.append(pd.to_datetime(exit_time))
                exit_long_y.append(exit_price)
        
        # Entry Short (Dreieck nach unten, Orange)
        if 'entry_short' in trade:
            entry_time = trade['entry_short'].get('time')
            entry_price = trade['entry_short'].get('price')
            if entry_time and entry_price:
                entry_short_x.append(pd.to_datetime(entry_time))
                entry_short_y.append(entry_price)
        
        # Exit Short (Diamant, Rot)
        if 'exit_short' in trade:
            exit_time = trade['exit_short'].get('time')
            exit_price = trade['exit_short'].get('price')
            if exit_time and exit_price:
                exit_short_x.append(pd.to_datetime(exit_time))
                exit_short_y.append(exit_price)
    
    # Entry Long: Dreieck nach oben, grün (#16a34a)
    if entry_long_x:
        fig.add_trace(go.Scatter(
            x=entry_long_x, y=entry_long_y, mode="markers",
            marker=dict(color="#16a34a", symbol="triangle-up", size=14, line=dict(width=1.2, color="#0f5132")),
            name="Entry Long",
            showlegend=True
        ), secondary_y=False)
    
    # Exit Long: Kreis, Cyan (#22d3ee)
    if exit_long_x:
        fig.add_trace(go.Scatter(
            x=exit_long_x, y=exit_long_y, mode="markers",
            marker=dict(color="#22d3ee", symbol="circle", size=12, line=dict(width=1.1, color="#0e7490")),
            name="Exit Long",
            showlegend=True
        ), secondary_y=False)
    
    # Entry Short: Dreieck nach unten, Orange (#f59e0b)
    if entry_short_x:
        fig.add_trace(go.Scatter(
            x=entry_short_x, y=entry_short_y, mode="markers",
            marker=dict(color="#f59e0b", symbol="triangle-down", size=14, line=dict(width=1.2, color="#92400e")),
            name="Entry Short",
            showlegend=True
        ), secondary_y=False)
    
    # Exit Short: Diamant, Rot (#ef4444)
    if exit_short_x:
        fig.add_trace(go.Scatter(
            x=exit_short_x, y=exit_short_y, mode="markers",
            marker=dict(color="#ef4444", symbol="diamond", size=12, line=dict(width=1.1, color="#7f1d1d")),
            name="Exit Short",
            showlegend=True
        ), secondary_y=False)
    
    # Berechne Stats für Titel
    end_capital = stats.get('end_capital', start_capital)
    pnl_pct = stats.get('total_pnl_pct', 0)
    trades_count = stats.get('trades_count', 0)
    win_rate = stats.get('win_rate', 0)
    max_dd = stats.get('max_drawdown_pct', 0) * 100
    
    # Layout mit erweiterten Stats im Titel
    title = (
        f"{symbol} {timeframe} - StBot | "
        f"Capital: ${start_capital:.0f}→${end_capital:.0f} | "
        f"PnL: {pnl_pct:+.2f}% | "
        f"DD: {max_dd:.2f}% | "
        f"Trades: {trades_count} @ {win_rate:.1f}%"
    )
    
    fig.update_layout(
        title=title,
        height=600,
        hovermode='x unified',
        template='plotly_white',
        dragmode='zoom',
        xaxis=dict(rangeslider=dict(visible=True), fixedrange=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        showlegend=True
    )
    
    fig.update_xaxes(fixedrange=False)
    fig.update_yaxes(title_text="Preis", secondary_y=False)
    fig.update_yaxes(title_text="Kontostand ($)", secondary_y=True)
    
    return fig

def main():
    # Wähle Konfigurationsdateien
    selected_configs = select_configs()
    
    # Parameter für Chart-Generierung
    print("\n" + "="*60)
    print("Chart-Optionen:")
    print("="*60)
    
    start_date = input("Startdatum (YYYY-MM-DD) [leer=beliebig]: ").strip() or None
    end_date = input("Enddatum (YYYY-MM-DD) [leer=heute]: ").strip() or None
    window_input = input("Letzten N Tage anzeigen [leer=alle]: ").strip()
    window = int(window_input) if window_input.isdigit() else None
    send_telegram = input("Telegram versenden? (j/n) [Standard: n]: ").strip().lower() in ['j', 'y', 'yes']
    
    try:
        with open(os.path.join(PROJECT_ROOT, 'secret.json'), 'r') as f:
            secrets = json.load(f)
    except Exception as e:
        logger.error(f"Fehler beim Laden von secret.json: {e}")
        sys.exit(1)
    
    account = secrets.get('stbot', [None])[0]
    if not account:
        logger.error("Keine StBot-Accountkonfiguration gefunden")
        sys.exit(1)
    
    exchange = Exchange(account)
    telegram_config = secrets.get('telegram', {})
    
    # Generiere Chart für jede gewählte Config
    for filename, filepath in selected_configs:
        try:
            logger.info(f"\nVerarbeite {filename}...")
            
            config = load_config(filepath)
            symbol = config['market']['symbol']
            timeframe = config['market']['timeframe']
            
            logger.info(f"Lade OHLCV-Daten für {symbol} {timeframe}...")
            
            # Nutze historische Daten basierend auf Start/End Datum
            # Falls keine Daten angefordert: letzte 30 Tage
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
            
            logger.info("Verarbeite Daten...")
            
            # Führe Backtest durch, um Trades, Equity Curve und Stats zu generieren
            logger.info("Führe Backtest durch...")
            trades, equity_df, stats = run_backtest_for_chart(df.copy(), config, start_capital=1000)
            
            # Erstelle Chart mit Trade-Signalen und Equity Curve
            logger.info("Erstelle Chart...")
            fig = create_interactive_chart(
                symbol,
                timeframe,
                df,
                trades,
                equity_df,
                stats,
                start_date,
                end_date,
                window,
                1000
            )
            
            # Speichere HTML
            safe_name = f"{symbol.replace('/', '_')}_{timeframe}"
            output_file = f"/tmp/stbot_{safe_name}.html"
            fig.write_html(output_file)
            logger.info(f"✅ Chart gespeichert: {output_file}")
            
            # Telegram versenden (optional)
            if send_telegram and telegram_config:
                try:
                    logger.info(f"Sende Chart via Telegram...")
                    from stbot.utils.telegram import send_document
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
