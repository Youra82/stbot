# code/strategies/envelope/run.py

import os
import sys
import json
import logging
import pandas as pd
import traceback
import sqlite3
import time

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..', '..', '..')
sys.path.append(os.path.join(PROJECT_ROOT, 'code'))

from utilities.bitget_futures import BitgetFutures
from utilities.strategy_logic import calculate_stochrsi_indicators
from utilities.telegram_handler import send_telegram_message

LOG_DIR = os.path.join(PROJECT_ROOT, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, 'stbot.log')
logging.basicConfig(level=logging.INFO, format='%(asctime)s UTC: %(message)s', datefmt='%Y-%m-%d %H:%M:%S', handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
logger = logging.getLogger('stbot')

def load_config():
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    with open(config_path, 'r') as f: return json.load(f)

params = load_config()
SYMBOL = params['market']['symbol']
DB_FILE = os.path.join(os.path.dirname(__file__), f"bot_state_{SYMBOL.replace('/', '-')}.db")

def setup_database():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS bot_state (symbol TEXT PRIMARY KEY, side TEXT)')
    conn.commit()
    conn.close()

def get_open_side():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT side FROM bot_state WHERE symbol = ?", (SYMBOL,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result and result[0] != 'none' else None

def update_open_side(side: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO bot_state (symbol, side) VALUES (?, ?)", (SYMBOL, side))
    conn.commit()
    conn.close()

def place_order_and_verify(bitget, symbol, side, amount, sl_price, leverage, margin_mode, bot_token, chat_id, is_test=False):
    """Eine robustere Funktion zum Platzieren und Verifizieren von Orders."""
    try:
        logger.info(f"Sende {side.upper()}-Market-Order über {amount:.5f} {symbol.split('/')[0]}...")
        order_result = bitget.create_market_order(symbol, side, amount, leverage, margin_mode)
        
        if order_result and order_result.get('status') == 'closed':
            logger.info("✅ Market-Order von Bitget als ERFOLGREICH bestätigt.")
            time.sleep(3)
            
            new_pos = bitget.fetch_open_positions(symbol)
            if not new_pos:
                logger.error("🚨 FEHLER: Order ausgeführt, aber keine offene Position gefunden!")
                send_telegram_message(bot_token, chat_id, f"🚨 FEHLER bei *{symbol}*: Order ausgeführt, aber Position nicht gefunden!")
                return False

            new_pos = new_pos[0]
            close_side = 'sell' if side == 'buy' else 'buy'
            bitget.place_trigger_market_order(symbol, close_side, float(new_pos['contracts']), sl_price, leverage, margin_mode, reduce=True)
            db_side_map = {'buy': 'long', 'sell': 'short'}
            update_open_side(db_side_map[side])
            
            test_str = "TEST (" + side.upper() + ")" if is_test else side.upper()
            message = f"🔥 {test_str} *{symbol}* eröffnet!\n- Hebel: {leverage}x\n- Stop-Loss: ${sl_price:.4f}"
            send_telegram_message(bot_token, chat_id, message)
            logger.info(message)
            return True
        else:
            logger.error(f"🚨 FEHLER: Market-Order wurde von der Börse NICHT ausgeführt! Antwort: {order_result}")
            send_telegram_message(bot_token, chat_id, f"🚨 FEHLER bei *{symbol}*: Order wurde abgelehnt. Antwort: `{order_result}`")
            return False
            
    except Exception as e:
        logger.error(f"🚨 KRITISCHER FEHLER bei der Order-Platzierung: {e}", exc_info=True)
        error_message = f"🚨 KRITISCHER FEHLER im stbot für *{symbol}* bei Order-Platzierung!\n\n`{traceback.format_exc()}`"
        send_telegram_message(bot_token, chat_id, error_message[:4000])
        return False

def main():
    logger.info(f">>> Starte Ausführung für {SYMBOL} (stbot v1.5 - Order-Parameter Fix)")
    
    try:
        key_path = os.path.abspath(os.path.join(PROJECT_ROOT, 'secret.json'))
        with open(key_path, "r") as f: secrets = json.load(f)
        api_setup = secrets['envelope']
        telegram_config = secrets.get('telegram', {})
        bot_token = telegram_config.get('bot_token')
        chat_id = telegram_config.get('chat_id')
    except Exception as e:
        logger.critical(f"Fehler beim Laden der API-Schlüssel: {e}"); sys.exit(1)

    API_SYMBOL = SYMBOL.split(':')[0].replace('/', '')

    dev_params = params.get('development', {})
    force_trade_side = dev_params.get('force_trade_side', 'none')

    bitget = BitgetFutures(api_setup)
    setup_database()
    
    try:
        timeframe = params['market']['timeframe']
        
        logger.info(f"Lade Marktdaten für {API_SYMBOL}...")
        data = bitget.fetch_recent_ohlcv(SYMBOL, timeframe, 500)
        data = calculate_stochrsi_indicators(data, params['strategy'])
        
        prev_candle = data.iloc[-2]
        current_candle = data.iloc[-1]
        logger.info(f"Indikatoren: %K={prev_candle['%k']:.1f}, %D={prev_candle['%d']:.1f}, EMA={prev_candle['ema_trend']:.4f}")

        positions = bitget.fetch_open_positions(API_SYMBOL)
        open_position = positions[0] if positions else None
        db_side = get_open_side()

        if not open_position and db_side:
            message = f"✅ Position für *{API_SYMBOL}* ({db_side}) geschlossen."; send_telegram_message(bot_token, chat_id, message)
            logger.info(message)
            update_open_side('none')
            db_side = None

        if not open_position:
            logger.info("Keine Position offen. Suche nach neuem Einstieg.")
            trend_filter_cfg = params['strategy'].get('trend_filter', {}); sideways_filter_cfg = params['strategy'].get('sideways_filter', {})
            trend_allows_long, trend_allows_short, market_is_not_sideways = True, True, True

            if trend_filter_cfg.get('enabled', False) and not pd.isna(prev_candle['ema_trend']):
                if prev_candle['close'] < prev_candle['ema_trend']:
                    logger.info("Trend-Filter (Aktiv): Markt unter EMA. Longs deaktiviert."); trend_allows_long = False
                else:
                    logger.info("Trend-Filter (Aktiv): Markt über EMA. Shorts deaktiviert."); trend_allows_short = False
            
            if sideways_filter_cfg.get('enabled', False):
                sideways_max_crosses = sideways_filter_cfg.get('max_crosses', 8)
                if prev_candle['sideways_cross_count'] > sideways_max_crosses:
                    logger.warning(f"Seitwärts-Filter (Aktiv): Markt unruhig. Kein Handel."); market_is_not_sideways = False

            base_leverage = params['risk']['base_leverage']; target_atr_pct = params['risk']['target_atr_pct']; max_leverage = params['risk']['max_leverage']
            current_atr_pct = prev_candle['atr_pct']
            leverage = base_leverage
            if pd.notna(current_atr_pct) and current_atr_pct > 0:
                leverage = base_leverage * (target_atr_pct / current_atr_pct)
            leverage = int(round(max(1.0, min(leverage, max_leverage))))
            margin_mode = params['risk']['margin_mode']
            logger.info(f"Berechneter Hebel: {leverage}x. Margin-Modus: {margin_mode}")

            # --- HINZUGEFÜGT: Hebel und Margin-Modus vor dem Handel an Bitget senden ---
            try:
                bitget.set_margin_mode(API_SYMBOL, margin_mode)
                bitget.set_leverage(API_SYMBOL, leverage, margin_mode)
            except Exception as e:
                logger.error(f"🚨 FEHLER beim Setzen von Hebel/Margin-Modus: {e}")
                # Breche hier ab, um keinen Trade mit falschen Einstellungen zu platzieren
                raise e 
            # --- ENDE DES HINZUGEFÜGTEN ABSCHNITTS ---
            
            oversold = params['strategy']['oversold_level']; overbought = params['strategy']['overbought_level']
            use_longs = params['behavior'].get('use_longs', True); use_shorts = params['behavior'].get('use_shorts', True)
            
            free_balance = bitget.fetch_balance()['USDT']['free']
            capital_to_use = free_balance * (params['risk']['balance_fraction_pct'] / 100.0)
            notional_value = capital_to_use * leverage
            amount = notional_value / current_candle['close']

            if force_trade_side.upper() in ["LONG", "SHORT"]:
                side_to_force = force_trade_side.upper()
                logger.info(f"MANUELLER TEST (aus config.json): Erzwinge {side_to_force}-Signal.")
                side = 'buy' if side_to_force == 'LONG' else 'sell'
                sl_price = prev_candle['swing_low'] * (1 - params['risk']['sl_buffer_pct'] / 100) if side == 'buy' else prev_candle['swing_high'] * (1 + params['risk']['sl_buffer_pct'] / 100)
                place_order_and_verify(bitget, API_SYMBOL, side, amount, sl_price, leverage, margin_mode, bot_token, chat_id, is_test=True)

            elif force_trade_side.lower() == 'none':
                if (use_longs and trend_allows_long and market_is_not_sideways and prev_candle['%k'] < prev_candle['%d'] and 
                    current_candle['%k'] > current_candle['%d'] and prev_candle['%k'] < oversold):
                    logger.info("🟢 LONG-Signal bestätigt. Alle Filter passiert.")
                    sl_price = prev_candle['swing_low'] * (1 - params['risk']['sl_buffer_pct'] / 100)
                    place_order_and_verify(bitget, API_SYMBOL, 'buy', amount, sl_price, leverage, margin_mode, bot_token, chat_id)

                elif (use_shorts and trend_allows_short and market_is_not_sideways and prev_candle['%k'] > prev_candle['%d'] and 
                      current_candle['%k'] < current_candle['%d'] and prev_candle['%k'] > overbought):
                    logger.info("🔴 SHORT-Signal bestätigt. Alle Filter passiert.")
                    sl_price = prev_candle['swing_high'] * (1 + params['risk']['sl_buffer_pct'] / 100)
                    place_order_and_verify(bitget, API_SYMBOL, 'sell', amount, sl_price, leverage, margin_mode, bot_token, chat_id)
                else:
                    logger.info("Kein gültiges Signal oder von Filtern blockiert.")

        elif open_position:
            db_side = get_open_side()
            # Der Hebel muss hier erneut berechnet werden, falls die Order-Funktion ihn benötigt
            leverage = int(open_position.get('leverage', params['risk']['base_leverage']))
            margin_mode = params['risk']['margin_mode']

            logger.info(f"Position offen: {db_side}. Prüfe auf Take-Profit-Signal...")
            oversold = params['strategy']['oversold_level']; overbought = params['strategy']['overbought_level']
            db_side_map = {'long': 'buy', 'short': 'sell'}
            
            if db_side_map.get(db_side) == 'buy' and current_candle['%k'] > overbought:
                logger.info(f"🟢 LONG Take-Profit (%K > {overbought}). Schließe Position."); 
                bitget.create_market_order(API_SYMBOL, 'sell', float(open_position['contracts']), leverage, margin_mode, params={'reduceOnly': True})
            elif db_side_map.get(db_side) == 'sell' and current_candle['%k'] < oversold:
                logger.info(f"🔴 SHORT Take-Profit (%K < {oversold}). Schließe Position."); 
                bitget.create_market_order(API_SYMBOL, 'buy', float(open_position['contracts']), leverage, margin_mode, params={'reduceOnly': True})

    except Exception as e:
        logger.error(f"Unerwarteter Fehler im Haupt-Loop: {e}", exc_info=True)
        error_message = f"🚨 KRITISCHER FEHLER im stbot für *{SYMBOL}*!\n\n`{traceback.format_exc()}`"
        send_telegram_message(bot_token, chat_id, error_message[:4000])

if __name__ == "__main__":
    main()
    logger.info("<<< Ausführung abgeschlossen\n")
