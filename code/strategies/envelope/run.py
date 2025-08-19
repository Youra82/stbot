# code/strategies/envelope/run.py
import os
import sys
import json
import time
import logging
import requests

# --- PFAD-SETUP ---
# Fügt das Hauptverzeichnis des Projekts zum Python-Pfad hinzu
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(PROJECT_ROOT)

from utilities.bitget_futures import BitgetFutures
from utilities.strategy_logic import calculate_signals
from utilities.state_manager import StateManager

# --- KONFIGURATION LADEN ---
def load_config():
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        logging.critical(f"Kritischer Fehler: Konfigurationsdatei config.json konnte nicht geladen werden: {e}")
        # Telegram-Benachrichtigung hier, falls möglich
        sys.exit(1)

params = load_config()

# --- PFADEINSTELLUNGEN ---
BASE_DIR = os.path.expanduser("~/utbot2") # Basisverzeichnis auf dem Server
KEY_PATH = os.path.join(BASE_DIR, 'secret.json')
DB_PATH = os.path.join(os.path.dirname(__file__), f"tracker_{params['symbol'].replace('/', '-').replace(':', '-')}.db")
LOG_DIR = os.path.join(BASE_DIR, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, 'envelope.log')

# --- LOGGING & TELEGRAM ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s UTC: %(message)s', datefmt='%Y-%m-%d %H:%M:%S', handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
logger = logging.getLogger('envelope_bot')

telegram_bot_token = None
telegram_chat_id = None

def send_telegram_message(message):
    if not telegram_bot_token or not telegram_chat_id:
        return
    url = f"https://api.telegram.org/bot{telegram_bot_token}/sendMessage"
    payload = {'chat_id': telegram_chat_id, 'text': message, 'parse_mode': 'Markdown'}
    try:
        requests.post(url, data=payload, timeout=10).raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Fehler beim Senden der Telegram-Nachricht: {e}")

# --- AUTHENTIFIZIERUNG & SETUP ---
logger.info(f">>> Starte Ausführung für {params['symbol']}")
try:
    with open(KEY_PATH, "r") as f:
        secrets = json.load(f)
    api_setup = secrets['envelope']
    telegram_setup = secrets.get('telegram', {})
    telegram_bot_token = telegram_setup.get('bot_token')
    telegram_chat_id = telegram_setup.get('chat_id')
except Exception as e:
    logger.critical(f"Kritischer Fehler beim Laden der Keys: {e}")
    sys.exit(1)

state_manager = StateManager(DB_PATH)

def create_bitget_connection():
    for attempt in range(params['max_retries']):
        try:
            return BitgetFutures(api_setup)
        except Exception as e:
            logger.error(f"Verbindungsfehler (Versuch {attempt+1}/{params['max_retries']}): {e}")
            if attempt < params['max_retries'] - 1: time.sleep(params['retry_delay'])
    logger.critical("API-Verbindung fehlgeschlagen")
    send_telegram_message(f"❌ *Kritischer Fehler:* API-Verbindung zu Bitget fehlgeschlagen für {params['symbol']}.")
    sys.exit(1)

bitget = create_bitget_connection()

# --- HAUPTFUNKTIONEN ---

def open_new_position(side, data):
    try:
        balance = bitget.fetch_balance().get('USDT', {}).get('total', 0.0)
        trade_size_usdt = (balance * (params['trade_size_pct'] / 100)) * params['leverage']
        min_trade_cost = 5.0

        if trade_size_usdt < min_trade_cost:
            msg = f"Handelsgröße ({trade_size_usdt:.2f} USDT) zu gering. Minimum ist {min_trade_cost} USDT."
            logger.error(msg)
            send_telegram_message(f"⚠️ *Trade nicht eröffnet ({params['symbol']}):* {msg}")
            state_manager.set_state(status="ok_to_trade")
            return

        current_price = data.iloc[-1]['close']
        amount_to_trade = trade_size_usdt / current_price
        
        bitget.place_market_order(params['symbol'], side, amount_to_trade)
        
        stop_loss_id = None
        if params['enable_stop_loss']:
            sl_side = 'sell' if side == 'buy' else 'buy'
            current_atr = data.iloc[-1]['atr']
            stop_loss_distance = current_atr * params['stop_loss_atr_multiplier']
            stop_loss_price = current_price - stop_loss_distance if side == 'buy' else current_price + stop_loss_distance
            
            sl_order = bitget.place_trigger_market_order(params['symbol'], sl_side, amount_to_trade, stop_loss_price, reduce=True)
            if sl_order and sl_order.get('id'):
                stop_loss_id = sl_order.get('id')
                state_manager.set_state(status="in_trade", last_side=side, stop_loss_ids=[stop_loss_id])
                position_type = 'Long' if side == 'buy' else 'Short'
                sl_text = f"mit Stop-Loss bei {stop_loss_price:.4f}"
                logger.info(f"{position_type}-Position bei {current_price:.4f} eröffnet, {sl_text}")
                send_telegram_message(f"✅ *{position_type}-Position eröffnet ({params['symbol']}):* @ {current_price:.4f} USDT\n{sl_text}")
            else:
                raise Exception("Konnte Stop-Loss ID nicht aus der Order-Antwort extrahieren.")
        else:
            state_manager.set_state(status="in_trade", last_side=side, stop_loss_ids=[])
            logger.info("Position ohne Stop-Loss eröffnet.")
            send_telegram_message(f"✅ *Position eröffnet ({params['symbol']}) OHNE Stop-Loss*")

    except Exception as e:
        msg = f"Fehler beim Eröffnen der {side}-Position: {e}"
        logger.error(msg)
        send_telegram_message(f"❌ *Fehler bei Positionseröffnung ({params['symbol']}):* {msg}")
        state_manager.set_state(status="ok_to_trade")

def manage_trailing_stop(position_info, data):
    if not params.get('enable_trailing_stop_loss', False):
        return

    state = state_manager.get_state()
    if not state.get('stop_loss_ids'):
        logger.info("Keine Stop-Loss ID für Trailing Stop gefunden.")
        return

    try:
        current_sl_id = state['stop_loss_ids'][0]
        open_orders = bitget.fetch_open_trigger_orders(params['symbol'])
        current_sl_order = next((o for o in open_orders if o['id'] == current_sl_id), None)

        if not current_sl_order:
            logger.warning(f"Gespeicherte SL-Order {current_sl_id} nicht mehr auf der Börse gefunden. Wurde sie manuell geschlossen?")
            state_manager.set_state("in_trade", last_side=state['last_side'], stop_loss_ids=[])
            return

        current_sl_price = float(current_sl_order['stopPrice'])
        new_trailing_stop_price = data.iloc[-1]['x_atr_trailing_stop']
        
        should_trail = False
        if position_info['side'] == 'long' and new_trailing_stop_price > current_sl_price:
            should_trail = True
        elif position_info['side'] == 'short' and new_trailing_stop_price < current_sl_price:
            should_trail = True

        if should_trail:
            logger.info(f"Trailing Stop: Verschiebe SL von {current_sl_price:.4f} nach {new_trailing_stop_price:.4f}")
            bitget.cancel_trigger_order(current_sl_id, params['symbol'])
            
            amount = float(position_info['contracts'])
            sl_side = 'sell' if position_info['side'] == 'long' else 'buy'
            new_sl_order = bitget.place_trigger_market_order(params['symbol'], sl_side, amount, new_trailing_stop_price, reduce=True)
            
            if new_sl_order and new_sl_order.get('id'):
                state_manager.set_state("in_trade", last_side=state['last_side'], stop_loss_ids=[new_sl_order['id']])
                send_telegram_message(f"📈 *Trailing Stop Update ({params['symbol']}):* Neuer SL bei {new_trailing_stop_price:.4f} USDT")
            else:
                raise Exception("Konnte neue Trailing-Stop-Loss ID nicht extrahieren.")

    except Exception as e:
        logger.error(f"Fehler beim Management des Trailing Stops: {e}")
        send_telegram_message(f"⚠️ *Warnung ({params['symbol']}):* Fehler beim Trailing Stop Management: {e}")

# --- HAUPT-LOGIK ---
def main():
    try:
        # 1. Daten abrufen und Signale berechnen
        data = bitget.fetch_recent_ohlcv(params['symbol'], params['timeframe'], 200)
        data = calculate_signals(data, params)
        last_candle = data.iloc[-2] # Vorletzte, geschlossene Kerze für die Entscheidung
        
        # 2. Positionen und Status prüfen
        positions = bitget.fetch_open_positions(params['symbol'])
        is_position_open = len(positions) > 0
        state = state_manager.get_state()

        # 3. Synchronisation: DB-Status <> Börsen-Status
        if state['status'] == "in_trade" and not is_position_open:
            logger.warning("Tracker war 'in_trade', aber keine Position gefunden. Setze zurück.")
            send_telegram_message(f"ℹ️ *Info ({params['symbol']}):* Position wurde extern geschlossen. Setze Bot-Status zurück.")
            state_manager.set_state(status="ok_to_trade")
            state = state_manager.get_state()
        
        # --- LOGIK FÜR OFFENE POSITIONEN ---
        if is_position_open:
            pos_info = positions[0]
            logger.info(f"Offene {pos_info['side']}-Position wird gehalten. PnL: {pos_info.get('unrealizedPnl', 0):.2f} USDT")

            # A. Auf Schließ-Signal (Flip) prüfen
            should_close_long = pos_info['side'] == 'long' and last_candle['sell_signal_ut']
            should_close_short = pos_info['side'] == 'short' and last_candle['buy_signal_ut']
            
            if should_close_long or should_close_short:
                closed_side_msg = "LONG" if should_close_long else "SHORT"
                logger.info(f"{closed_side_msg} Position wegen Gegensignal geschlossen.")
                send_telegram_message(f"🚪 *Position geschlossen ({params['symbol']}):* {closed_side_msg} aufgrund eines Gegensignals.")
                
                # Alte SL-Orders stornieren
                if state.get('stop_loss_ids'):
                    for sl_id in state['stop_loss_ids']: bitget.cancel_trigger_order(sl_id, params['symbol'])
                
                bitget.flash_close_position(params['symbol'])
                
                # Flip: Sofort neue Position in die andere Richtung eröffnen
                if should_close_long and params['use_shorts']:
                    logger.info("Gegensignal (SELL) erkannt. Eröffne sofort Short-Position.")
                    open_new_position('sell', data)
                elif should_close_short and params['use_longs']:
                    logger.info("Gegensignal (BUY) erkannt. Eröffne sofort Long-Position.")
                    open_new_position('buy', data)
                else:
                    state_manager.set_state(status="ok_to_trade")
            
            # B. Wenn kein Schließ-Signal, Trailing Stop managen
            else:
                manage_trailing_stop(pos_info, data)

        # --- LOGIK FÜR KEINE OFFENE POSITION ---
        else:
            if last_candle['buy_signal'] and params['use_longs']:
                logger.info("Kaufsignal erkannt. Eröffne neue Long-Position.")
                open_new_position('buy', data)
            elif last_candle['sell_signal'] and params['use_shorts']:
                logger.info("Verkaufssignal erkannt. Eröffne neue Short-Position.")
                open_new_position('sell', data)
            else:
                logger.info("Kein neues Handelssignal gefunden.")

    except Exception as e:
        logger.error(f"Ein unerwarteter Fehler ist in der Hauptschleife aufgetreten: {e}")
        send_telegram_message(f"❌ *Unerwarteter Fehler ({params['symbol']}):* {e}")

if __name__ == "__main__":
    main()
    logger.info(f"<<< Ausführung abgeschlossen\n")
