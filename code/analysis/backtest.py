# code/analysis/backtest.py
import os
import sys
import json
import pandas as pd
import argparse

# Pfad anpassen
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from utilities.bitget_futures import BitgetFutures
from utilities.strategy_logic import calculate_signals

def run_backtest(data, params, verbose=True):
    if verbose:
        print("\nFühre Backtest aus...")

    # NEUE PARAMETER FÜR REALISTISCHERES BACKTESTING
    leverage = params.get('leverage', 1.0)
    sl_multiplier = params.get('stop_loss_atr_multiplier', 1.5)
    
    in_position = False
    position_side = None
    entry_price = 0.0
    stop_loss_price = 0.0
    total_pnl = 0.0
    trades_count = 0
    wins_count = 0
    
    fee_pct = 0.05 / 100

    for i in range(1, len(data)):
        prev_candle = data.iloc[i-1]
        current_candle = data.iloc[i]

        # --- 1. PRÜFUNG: WURDE EIN STOP-LOSS AUSGELÖST? ---
        if in_position:
            pnl = 0.0
            exit_price = 0.0

            # Stop-Loss für Long-Position
            if position_side == 'long' and current_candle['low'] <= stop_loss_price:
                exit_price = stop_loss_price
                pnl = (((exit_price - entry_price) / entry_price) * leverage) - (2 * fee_pct * leverage)
                if verbose: print(f"{current_candle.name.strftime('%Y-%m-%d %H:%M')} | STOP-LOSS   | PnL: {pnl*100:.2f}%")
            
            # Stop-Loss für Short-Position
            elif position_side == 'short' and current_candle['high'] >= stop_loss_price:
                exit_price = stop_loss_price
                pnl = (((entry_price - exit_price) / entry_price) * leverage) - (2 * fee_pct * leverage)
                if verbose: print(f"{current_candle.name.strftime('%Y-%m-%d %H:%M')} | STOP-LOSS   | PnL: {pnl*100:.2f}%")

            if exit_price > 0: # Wenn Stop-Loss ausgelöst wurde
                total_pnl += pnl
                trades_count += 1
                if pnl > 0: wins_count += 1
                in_position = False
                stop_loss_price = 0.0
                continue # Gehe zur nächsten Kerze

        # --- 2. PRÜFUNG: GIBT ES EIN REGULÄRES AUSSTIEGSSIGNAL? (FALLS KEIN SL) ---
        if in_position:
            exit_price = current_candle['open'] # Ausstieg am Anfang der neuen Kerze
            pnl = 0.0

            # Ausstiegssignal für Long-Position
            if position_side == 'long' and prev_candle['sell_signal_ut']:
                pnl = (((exit_price - entry_price) / entry_price) * leverage) - (2 * fee_pct * leverage)
                if verbose: print(f"{current_candle.name.strftime('%Y-%m-%d %H:%M')} | CLOSE LONG  | PnL: {pnl*100:.2f}%")

            # Ausstiegssignal für Short-Position
            elif position_side == 'short' and prev_candle['buy_signal_ut']:
                pnl = (((entry_price - exit_price) / entry_price) * leverage) - (2 * fee_pct * leverage)
                if verbose: print(f"{current_candle.name.strftime('%Y-%m-%d %H:%M')} | CLOSE SHORT | PnL: {pnl*100:.2f}%")

            if pnl != 0.0: # Wenn ein Gegensignal kam
                total_pnl += pnl
                trades_count += 1
                if pnl > 0: wins_count += 1
                in_position = False

        # --- 3. PRÜFUNG: GIBT ES EIN EINSTIEGSSIGNAL? ---
        if not in_position:
            entry_price = current_candle['open']
            atr_for_sl = prev_candle['atr']

            # Einstiegssignal für Long-Position
            if prev_candle['buy_signal'] and params.get('use_longs', True):
                in_position = True
                position_side = 'long'
                stop_loss_price = entry_price - (atr_for_sl * sl_multiplier)
                if verbose: print(f"{current_candle.name.strftime('%Y-%m-%d %H:%M')} | OPEN LONG   | @ {entry_price:.2f} | SL: {stop_loss_price:.2f}")

            # Einstiegssignal für Short-Position
            elif prev_candle['sell_signal'] and params.get('use_shorts', True):
                in_position = True
                position_side = 'short'
                stop_loss_price = entry_price + (atr_for_sl * sl_multiplier)
                if verbose: print(f"{current_candle.name.strftime('%Y-%m-%d %H:%M')} | OPEN SHORT  | @ {entry_price:.2f} | SL: {stop_loss_price:.2f}")

    win_rate = (wins_count / trades_count * 100) if trades_count > 0 else 0
    
    if verbose:
        print("\n--- Backtest-Ergebnisse ---")
        print(f"Zeitraum: {data.index[0].strftime('%Y-%m-%d')} -> {data.index[-1].strftime('%Y-%m-%d')}")
        if 'symbol_display' in params:
             print(f"Symbol: {params['symbol_display']}")
        print(f"Timeframe: {params['timeframe']}")
        print(f"Hebel: {leverage}x | SL-Multiplikator: {sl_multiplier}")
        print(f"Parameter: ut_atr_period={params['ut_atr_period']}, ut_key_value={params['ut_key_value']}, adx_threshold={params.get('adx_threshold', 'N/A')}")
        print("-" * 27)
        print(f"Gesamt-PnL (gehebelt): {total_pnl * 100:.2f}%")
        print(f"Anzahl Trades: {trades_count}")
        print(f"Gewonnene Trades: {wins_count}")
        print(f"Trefferquote: {win_rate:.2f}%")
        print("---------------------------")

    return {
        "total_pnl_pct": total_pnl * 100,
        "trades_count": trades_count,
        "win_rate": win_rate,
        "params": params
    }

def load_data_for_backtest(symbol, timeframe, start_date_str, end_date_str):
    """Lädt und cacht historische Daten."""
    cache_dir = os.path.join(os.path.dirname(__file__), 'historical_data')
    os.makedirs(cache_dir, exist_ok=True)
    symbol_filename = symbol.replace('/', '-').replace(':', '-')
    cache_file = os.path.join(cache_dir, f"{symbol_filename}_{timeframe}.csv")

    data = None
    if os.path.exists(cache_file):
        print(f"Lade Daten aus lokaler Cache-Datei: {cache_file}")
        data = pd.read_csv(cache_file, index_col='timestamp', parse_dates=True)
        data.index = pd.to_datetime(data.index, utc=True)

    download_start_date = start_date_str
    if data is not None and not data.empty:
        last_cached_date = data.index[-1].strftime('%Y-%m-%d')
        print(f"Letztes Datum im Cache: {last_cached_date}")
        if pd.to_datetime(last_cached_date) < pd.to_datetime(end_date_str):
             download_start_date = (data.index[-1] + pd.Timedelta(minutes=int(timeframe.replace('m','')))).strftime('%Y-%m-%d %H:%M:%S')
        else:
            print("Cache ist aktuell. Keine neuen Daten zum Herunterladen.")
            download_start_date = None

    if download_start_date:
        print(f"Lade neue Daten von {download_start_date} bis {end_date_str} für {symbol}...")
        try:
            key_path = '/home/ubuntu/utbot2/secret.json'
            with open(key_path, "r") as f:
                api_setup = json.load(f)['envelope']
            bitget = BitgetFutures(api_setup)
            new_data = bitget.fetch_historical_ohlcv(symbol, timeframe, download_start_date, end_date_str)
            
            if new_data is not None and not new_data.empty:
                data = pd.concat([data, new_data]) if data is not None else new_data
                data = data[~data.index.duplicated(keep='first')]
                data.sort_index(inplace=True)
                data.to_csv(cache_file)
                print("Cache-Datei wurde aktualisiert.")
            else:
                print("Keine neuen Daten erhalten.")

        except Exception as e:
            print(f"\nEin Fehler beim Herunterladen der Daten ist aufgetreten: {e}")
            return None
    
    if data is not None and not data.empty:
        return data.loc[start_date_str:end_date_str]
    return None

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Strategie-Backtest für den Envelope Bot.")
    parser.add_argument('--start', required=True, help="Startdatum im Format YYYY-MM-DD")
    parser.add_argument('--end', required=True, help="Enddatum im Format YYYY-MM-DD")
    parser.add_argument('--timeframe', required=True, help="Timeframe (z.B. 15m, 1h, 4h, 1d)")
    parser.add_argument('--symbols', nargs='+', help="Ein oder mehrere Handelspaare (z.B. BTC ETH SOL), überschreibt die config.json")
    # NEUE ARGUMENTE
    parser.add_argument('--leverage', type=float, help="Optionaler Hebel (z.B. 10)")
    parser.add_argument('--sl_multiplier', type=float, help="Optionaler Stop-Loss ATR Multiplikator (z.B. 1.5)")
    args = parser.parse_args()

    print("Lade Konfiguration...")
    config_path = os.path.join(os.path.dirname(__file__), '..', 'strategies', 'envelope', 'config.json')
    with open(config_path, 'r') as f:
        base_params = json.load(f)
    
    symbols_to_test = args.symbols if args.symbols else [base_params['symbol']]

    for symbol_arg in symbols_to_test:
        
        params = base_params.copy()
        params['timeframe'] = args.timeframe

        if args.leverage:
            params['leverage'] = args.leverage
        if args.sl_multiplier:
            params['stop_loss_atr_multiplier'] = args.sl_multiplier

        raw_symbol = symbol_arg
        if '/' not in raw_symbol:
            formatted_symbol = f"{raw_symbol.upper()}/USDT:USDT"
            print(f"\n\n==================== START TEST FÜR: {formatted_symbol} ====================")
            params['symbol'] = formatted_symbol
        else:
            formatted_symbol = raw_symbol.upper()
            print(f"\n\n==================== START TEST FÜR: {formatted_symbol} ====================")
            params['symbol'] = formatted_symbol

        data_for_backtest = load_data_for_backtest(params['symbol'], args.timeframe, args.start, args.end)
        
        if data_for_backtest is not None and not data_for_backtest.empty:
            params_for_run = params.copy()
            params_for_run['symbol_display'] = params['symbol'] 

            data_with_signals = calculate_signals(data_for_backtest, params)
            
            run_backtest(data_with_signals, params_for_run)

        else:
            print(f"Keine Daten für das Symbol {params['symbol']} im angegebenen Zeitraum verfügbar.")
        
        print(f"==================== ENDE TEST FÜR: {formatted_symbol} =====================\n")
