# src/kbot/analysis/backtester.py
# =============================================================================
# KBot Backtester: Stoch RSI Strategy
# =============================================================================

import os
import sys
import json
import pandas as pd
import numpy as np
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from kbot.utils.exchange import Exchange
from kbot.strategy.stochrsi_engine import StochRSIEngine


# =============================================================================
# DATEN LADEN
# =============================================================================
def load_data(symbol: str, timeframe: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    Lädt OHLCV-Daten von der Exchange oder aus dem Cache.

    Robust gegenüber leeren/ungültigen Datumsstrings (setzt `end_date` auf heute falls nötig).
    """
    # Fallback defaults for empty inputs
    from datetime import date as _date
    if not start_date or str(start_date).strip() == '':
        start_date = '1970-01-01'
    if not end_date or str(end_date).strip() == '':
        end_date = _date.today().strftime('%Y-%m-%d')

    cache_dir = os.path.join(PROJECT_ROOT, 'data', 'cache')
    os.makedirs(cache_dir, exist_ok=True)

    symbol_filename = symbol.replace('/', '-').replace(':', '-')
    cache_file = os.path.join(cache_dir, f"{symbol_filename}_{timeframe}.csv")

    # Versuche aus Cache zu laden
    if os.path.exists(cache_file):
        try:
            data = pd.read_csv(cache_file, index_col='timestamp', parse_dates=True)
            start_dt = pd.to_datetime(start_date, utc=True)
            end_dt = pd.to_datetime(end_date, utc=True)

            if data.index.min() <= start_dt and data.index.max() >= end_dt:
                return data.loc[start_date:end_date]
        except Exception:
            pass

    # Von Exchange laden
    print(f"Lade Daten für {symbol} ({timeframe}) von der Exchange...")
    try:
        secret_path = os.path.join(PROJECT_ROOT, 'secret.json')
        with open(secret_path, "r") as f:
            secrets = json.load(f)

        api_setup = secrets.get('kbot', [{}])[0]
        exchange = Exchange(api_setup)
        exchange.validate_timeframe(timeframe)

        full_data = exchange.fetch_historical_ohlcv(symbol, timeframe, start_date, end_date)

        if not full_data.empty:
            full_data.to_csv(cache_file)
            return full_data

    except Exception as e:
        print(f"Fehler beim Daten-Download: {e}")

    return pd.DataFrame()


# =============================================================================
# BACKTEST
# =============================================================================
def run_backtest(data: pd.DataFrame, params: dict, start_capital: float = 1000,
                 verbose: bool = False, return_equity: bool = False):
    """
    Führt einen Backtest der Stoch‑RSI Strategie durch.
    
    Args:
        data: OHLCV DataFrame
        params: Strategie-Parameter
        start_capital: Startkapital
        verbose: Detaillierte Ausgabe
        return_equity: Ob Equity-Curve zurückgegeben werden soll
    
    Returns:
        Dictionary mit Backtest-Ergebnissen
    """
    # Parameter extrahieren
    strategy = params.get('strategy', {})
    risk = params.get('risk', {})
    behavior = params.get('behavior', {})
    
    atr_period = strategy.get('atr_period', 200)
    channel_width = strategy.get('channel_width', 3.0)
    risk_reward = strategy.get('risk_reward_ratio', 2.0)
    use_volume_confirmation = strategy.get('use_volume_confirmation', True)
    
    risk_pct = risk.get('risk_per_trade_pct', 1.0) / 100
    leverage = risk.get('leverage', 10)
    
    use_longs = behavior.get('use_longs', True)
    use_shorts = behavior.get('use_shorts', True)
    
    fee_pct = 0.05 / 100  # 0.05% pro Trade
    
    # Engine initialisieren (Stoch‑RSI)
    engine = StochRSIEngine(settings=strategy)
    
    # Daten verarbeiten
    df = engine.process_dataframe(data)
    
    # Backtest-Variablen
    capital = start_capital
    position = None
    trades = []
    trades_list = []
    equity_snapshots = []
    peak_capital = start_capital
    max_drawdown_pct = 0.0
    wins_count = 0
    
    # Start nach ATR-Periode
    start_idx = atr_period + 20
    
    for i in range(start_idx, len(df)):
        current = df.iloc[i]
        prev = df.iloc[i-1]
        timestamp = current.name
        
        # Equity Snapshot
        equity_snapshots.append({'timestamp': timestamp, 'equity': capital})
        
        # Position-Management
        if position:
            current_price = current['close']
            current_high = current['high']
            current_low = current['low']
            
            exit_price = None
            
            if position['side'] == 'long':
                # Check SL
                if current_low <= position['sl']:
                    exit_price = position['sl']
                # Check TP
                elif current_high >= position['tp']:
                    exit_price = position['tp']
                
                if exit_price:
                    pnl_pct = (exit_price - position['entry']) / position['entry']
                    notional = position['size_usd']
                    pnl_usd = notional * pnl_pct * leverage
                    fees = notional * fee_pct * 2
                    net_pnl = pnl_usd - fees
                    
                    capital += net_pnl
                    if net_pnl > 0:
                        wins_count += 1
                    
                    trades.append({
                        'side': 'long',
                        'entry': position['entry'],
                        'exit': exit_price,
                        'pnl_pct': pnl_pct * 100,
                        'pnl_usd': net_pnl,
                        'result': 'TP' if exit_price >= position['tp'] else 'SL'
                    })
                    
                    trades_list.append({
                        'entry_long': {'time': str(position['entry_time']), 'price': position['entry']},
                        'exit_long': {'time': str(timestamp), 'price': exit_price}
                    })
                    position = None
            
            else:  # short
                # Check SL
                if current_high >= position['sl']:
                    exit_price = position['sl']
                # Check TP
                elif current_low <= position['tp']:
                    exit_price = position['tp']
                
                if exit_price:
                    pnl_pct = (position['entry'] - exit_price) / position['entry']
                    notional = position['size_usd']
                    pnl_usd = notional * pnl_pct * leverage
                    fees = notional * fee_pct * 2
                    net_pnl = pnl_usd - fees
                    
                    capital += net_pnl
                    if net_pnl > 0:
                        wins_count += 1
                    
                    trades.append({
                        'side': 'short',
                        'entry': position['entry'],
                        'exit': exit_price,
                        'pnl_pct': pnl_pct * 100,
                        'pnl_usd': net_pnl,
                        'result': 'TP' if exit_price <= position['tp'] else 'SL'
                    })
                    
                    trades_list.append({
                        'entry_short': {'time': str(position['entry_time']), 'price': position['entry']},
                        'exit_short': {'time': str(timestamp), 'price': exit_price}
                    })
                    position = None
            
            # Drawdown Update
            peak_capital = max(peak_capital, capital)
            if peak_capital > 0:
                dd = (peak_capital - capital) / peak_capital
                max_drawdown_pct = max(max_drawdown_pct, dd)
            
            if capital <= 0:
                break
        
        # Entry-Logik (nur wenn keine Position)
        if not position:
            sig = prev.get('stochrsi_signal', 0)
            delta = prev.get('volume_delta', 0)  # legacy field: optional (Stoch‑RSI ignoriert Volume‑Delta by default)
            
            # Long Signal (StochRSI)
            if sig == 1 and use_longs:
                if not use_volume_confirmation or delta >= 0:
                    entry_price = current['close']
                    # SL/TP via ATR (engine provides synthetic channel_* fields)
                    sl = current['channel_bot']
                    sl_distance = entry_price - sl
                    tp = entry_price + (sl_distance * risk_reward)

                    # Position Sizing
                    sl_pct = sl_distance / entry_price
                    if sl_pct > 0:
                        size_usd = (capital * risk_pct) / sl_pct
                        size_usd = min(size_usd, capital * 0.9)

                        if size_usd >= 5:  # Min notional
                            position = {
                                'side': 'long',
                                'entry': entry_price,
                                'sl': sl,
                                'tp': tp,
                                'size_usd': size_usd,
                                'entry_time': timestamp
                            }
                            if verbose:
                                print(f"  [LONG] Entry: {entry_price:.2f}, SL: {sl:.2f}, TP: {tp:.2f}")

            # Short Signal (StochRSI)
            elif sig == -1 and use_shorts:
                if not use_volume_confirmation or delta <= 0:
                    entry_price = current['close']
                    sl = current['channel_top']
                    sl_distance = sl - entry_price
                    tp = entry_price - (sl_distance * risk_reward)

                    # Position Sizing
                    sl_pct = sl_distance / entry_price
                    if sl_pct > 0:
                        size_usd = (capital * risk_pct) / sl_pct
                        size_usd = min(size_usd, capital * 0.9)

                        if size_usd >= 5:
                            position = {
                                'side': 'short',
                                'entry': entry_price,
                                'sl': sl,
                                'tp': tp,
                                'size_usd': size_usd,
                                'entry_time': timestamp
                            }
                            if verbose:
                                print(f"  [SHORT] Entry: {entry_price:.2f}, SL: {sl:.2f}, TP: {tp:.2f}")
    
    # Metriken berechnen
    num_trades = len(trades)
    win_rate = (wins_count / num_trades * 100) if num_trades > 0 else 0
    total_return = (capital - start_capital) / start_capital * 100
    
    # Profit Factor
    gross_profit = sum(t['pnl_usd'] for t in trades if t['pnl_usd'] > 0)
    gross_loss = abs(sum(t['pnl_usd'] for t in trades if t['pnl_usd'] < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
    
    stats = {
        'total_pnl_pct': total_return,
        'trades_count': num_trades,
        'win_rate': win_rate,
        'max_drawdown_pct': max_drawdown_pct * 100,
        'end_capital': capital,
        'profit_factor': profit_factor,
        'wins': wins_count,
        'losses': num_trades - wins_count,
        'trades': trades_list
    }
    
    if return_equity:
        return stats, equity_snapshots
    return stats


# =============================================================================
# MAIN
# =============================================================================
if __name__ == "__main__":
    print("KBot Backtester - Stoch RSI Strategy")
    print("=" * 50)
    print("Verwendung: Importiere run_backtest() und übergebe OHLCV-Daten")
