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
    cursor.execute('CREATE TABLE IF NOT EXISTS bot_state (symbol TEXT PRIMARY KEY, side TEXT, sl_price REAL)')
    try:
        cursor.execute('ALTER TABLE bot_state ADD COLUMN sl_price REAL')
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

def get_trade_state():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT side, sl_price FROM bot_state WHERE symbol = ?", (SYMBOL,))
    result = cursor.fetchone()
    conn.close()
    if result and result[0] != 'none':
        return {'side': result[0], 'sl_price': result[1]}
    return None

def update_trade_state(side: str, sl_price: float = 0.0):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO bot_state (symbol, side, sl_price) VALUES (?, ?, ?)", (SYMBOL, side, sl_price))
    conn.commit()
    conn.close()

def place_order_and_verify(bitget, symbol, side, amount, sl_price, leverage, margin_mode, bot_token, chat_id, is_test=False):
    try:
        logger.info(f"Sende {side.upper()}-Market-Order über {amount:.5f} {symbol.split('/')[0]}...")
        order_result = bitget.create_market_order(symbol, side, amount, leverage, margin_mode)
        
        if order_result and order_result.get('id'):
            logger.info(f"✅ Market-Order an Bitget übermittelt (ID: {order_result.get('id')}). Warte auf Ausführung...")
            time.sleep(5)
            
            new_pos = bitget.fetch_open_positions(symbol)
            if not new_pos:
                logger.error("🚨 FEHLER: Order übermittelt, aber nach 5s keine offene Position gefunden!")
                send_telegram_message(bot_token, chat_id, f"🚨 FEHLER bei *{symbol}*: Order übermittelt, aber Position nicht gefunden!")
                return False

            logger.info("✅ Positionseröffnung erfolgreich bestätigt.")
            new_pos = new_pos[0]
            close_side = 'sell' if side == 'buy' else 'buy'
            bitget.place_trigger_market_order(symbol, close_side, float(new_pos['contracts']), sl_price, reduce=True)
            db_side_map = {'buy': 'long', 'sell': 'short'}
            update_trade_state(db_side_map[side], sl_price)
            
            test_str = "TEST (" + side.upper() + ")" if is_test else side.upper()
            message = f"🔥 {test_str} *{symbol}* eröffnet!\n- Hebel: {leverage}x\n- Stop-Loss: ${sl_price:.8f}"
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

def close_position_and_cleanup(bitget, position, bot_token, chat_id):
    symbol = position['symbol']
    side_to_close = 'sell' if position['side'] == 'long' else 'buy'
    amount = float(position['contracts'])
    margin_mode = params['risk']['margin_mode']

    try:
        logger.info(f"Schließe {position['side']}-Position für {symbol}...")
        bitget.create_market_order(symbol, side_to_close, amount, 0, margin_mode, params={'reduceOnly': True})
        
        logger.info(f"Räume verbleibende offene Orders für {symbol} auf...")
        time.sleep(2)
        open_orders = bitget.fetch_open_orders(symbol)
        if open_orders:
            for order in open_orders:
                try:
                    bitget.cancel_order(order['id'], symbol)
                    logger.info(f"Order {order['id']} gelöscht.")
                except Exception as e:
                    logger.error(f"Konnte Order {order['id']} nicht löschen: {e}")
        else:
            logger.info("Keine offenen Orders zum Löschen gefunden.")

        send_telegram_message(bot_token, chat_id, f"✅ Position *{symbol}* geschlossen & alle SL/TP Orders aufgeräumt.")
        update_trade_state('none')
        return True

    except Exception as e:
        logger.error(f"Fehler beim Schließen der Position oder Aufräumen der Orders: {e}", exc_info=True)
        send_telegram_message(bot_token, chat_id, f"🚨 KRITISCHER FEHLER beim Schließen der Position für *{symbol}*! Manuelle Prüfung erforderlich.\n\n`{e}`")
        return False

def main():
    logger.info(f">>> Starte Ausführung für {SYMBOL} (stbot v2.5 - Robuster SL-Check)")
    
    try:
        key_path = os.path.abspath(os.path.join(PROJECT_ROOT, 'secret.json'))
        with open(key_path, "r") as f: secrets = json.load(f)
        api_setup = secrets['envelope']
        telegram_config = secrets.get('telegram', {})
        bot_token = telegram_config.get('bot_token')
        chat_id = telegram_config.get('chat_id')
    except Exception as e:
        logger.critical(f"Fehler beim Laden der API-Schlüssel: {e}"); sys.exit(1)

    bitget = BitgetFutures(api_setup)
    setup_database()
    
    try:
        data = bitget.fetch_recent_ohlcv(SYMBOL, params['market']['timeframe'], 500)
        data = calculate_stochrsi_indicators(data, params['strategy'])
        
        prev_candle = data.iloc[-2]
        current_candle = data.iloc[-1]

        positions = bitget.fetch_open_positions(SYMBOL)
        open_position = positions[0] if positions else None
        trade_state = get_trade_state()

        if open_position:
            if not trade_state:
                logger.warning("⚠️ Fremde Position entdeckt! Übernehme die Verwaltung...")
                update_trade_state(open_position['side']) 
                trade_state = get_trade_state()
                send_telegram_message(bot_token, chat_id, f"⚠️ *{SYMBOL}*: Fremde {open_position['side']}-Position entdeckt und Verwaltung übernommen.")

            logger.info("Position auf Börse gefunden. Prüfe auf fehlenden Stop-Loss...")
            trigger_orders = bitget.fetch_open_trigger_orders(SYMBOL)
            
            # === FINALE KORREKTUR: Robuster SL-Check ===
            # Prüft nur noch, ob irgendeine SL-Order in die richtige Richtung existiert.
            
            sl_order_found = False
            correct_sl_side = 'sell' if open_position['side'] == 'long' else 'buy'
            for order in trigger_orders:
                if order.get('side') == correct_sl_side:
                    logger.info(f"Passende SL-Order {order['id']} gefunden. Alles in Ordnung.")
                    sl_order_found = True
                    break
            
            if not sl_order_found:
                logger.warning(f"⚠️ FEHLENDER STOP-LOSS ENTDECKT! Platziere ihn jetzt...")
                sl_to_place = trade_state.get('sl_price') if trade_state else None
                if not sl_to_place or sl_to_place <= 0:
                    logger.warning("Kein SL-Preis in der Datenbank. Berechne Notfall-SL...")
                    if open_position['side'] == 'long':
                        sl_to_place = prev_candle['swing_low'] * (1 - params['risk']['sl_buffer_pct'] / 100)
                    else: # short
                        sl_to_place = prev_candle['swing_high'] * (1 + params['risk']['sl_buffer_pct'] / 100)
                    update_trade_state(open_position['side'], sl_to_place)
                bitget.place_trigger_market_order(SYMBOL, correct_sl_side, float(open_position['contracts']), sl_to_place, reduce=True)
                send_telegram_message(bot_token, chat_id, f"⚠️ *{SYMBOL}*: Fehlender Stop-Loss wurde automatisch nachplatziert.")
        
        if not open_position and trade_state:
            message = f"✅ Position für *{SYMBOL}* ({trade_state['side']}) auf der Börse geschlossen bestätigt."; send_telegram_message(bot_token, chat_id, message)
            logger.info(message)
            open_orders = bitget.fetch_open_orders(SYMBOL)
            if open_orders:
                logger.warning("Position geschlossen, aber verwaiste Orders gefunden. Räume jetzt auf...")
                for order in open_orders:
                    bitget.cancel_order(order['id'], SYMBOL)
            update_trade_state('none')
            trade_state = None

        logger.info(f"Indikatoren: %K={current_candle['%k']:.1f}, %D={current_candle['%d']:.1f}, EMA={current_candle['ema_trend']:.8f}")

        if open_position:
            if trade_state: 
                logger.info(f"Position offen: {trade_state.get('side', 'unbekannt')}. Prüfe auf Take-Profit-Signal...")
                oversold = params['strategy']['oversold_level']; overbought = params['strategy']['overbought_level']
                
                close_signal = False
                if trade_state.get('side') == 'long' and current_candle['%k'] > overbought:
                    logger.info(f"🟢 LONG Take-Profit (%K > {overbought}). Schließe Position."); 
                    close_signal = True
                elif trade_state.get('side') == 'short' and current_candle['%k'] < oversold:
                    logger.info(f"🔴 SHORT Take-Profit (%K < {oversold}). Schließe Position.");
                    close_signal = True
                
                if close_signal:
                    close_position_and_cleanup(bitget, open_position, bot_token, chat_id)
            else:
                logger.warning("Position auf Börse gefunden, aber kein Zustand in der DB. Warte auf nächsten Durchlauf für Selbstheilung.")

        else: 
            logger.info("Keine Position offen. Suche nach neuem Einstieg.")
            # ... (Einstiegslogik bleibt unverändert und wird hier nicht wiederholt)

    except Exception as e:
        logger.error(f"Unerwarteter Fehler im Haupt-Loop: {e}", exc_info=True)
        error_message = f"🚨 KRITISCHER FEHLER im stbot für *{SYMBOL}*!\n\n`{traceback.format_exc()}`"
        send_telegram_message(bot_token, chat_id, error_message[:4000])

if __name__ == "__main__":
    main()
    logger.info("<<< Ausführung abgeschlossen\n")
