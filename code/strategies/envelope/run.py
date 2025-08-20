# code/strategies/envelope/run.py
import os
import sys
import json
import time
import logging
import requests

# --- PATH SETUP ---
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(PROJECT_ROOT)

from utilities.bitget_futures import BitgetFutures
from utilities.strategy_logic import calculate_signals
from utilities.state_manager import StateManager

# --- LOAD CONFIGURATION ---
def load_config():
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        logging.critical(f"Critical Error: Could not load configuration file config.json: {e}")
        sys.exit(1)

params = load_config()

# --- PATH SETTINGS ---
BASE_DIR = os.path.expanduser(os.path.join("~", "stbot")) # Assumption: bot is in ~/stbot
KEY_PATH = os.path.join(BASE_DIR, 'secret.json')
DB_PATH = os.path.join(os.path.dirname(__file__), f"tracker_{params['symbol'].replace('/', '-').replace(':', '-')}.db")
LOG_DIR = os.path.join(BASE_DIR, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, 'supertrend.log')

# --- LOGGING & TELEGRAM ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s UTC: %(message)s', datefmt='%Y-%m-%d %H:%M:%S', handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
logger = logging.getLogger('supertrend_bot')

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
        logger.error(f"Error sending Telegram message: {e}")

# --- AUTHENTICATION & SETUP ---
logger.info(f">>> Starting execution for {params['symbol']} (Supertrend Strategy)")
try:
    with open(KEY_PATH, "r") as f:
        secrets = json.load(f)
    api_setup = secrets['envelope']
    telegram_setup = secrets.get('telegram', {})
    telegram_bot_token = telegram_setup.get('bot_token')
    telegram_chat_id = telegram_setup.get('chat_id')
except Exception as e:
    logger.critical(f"Critical error loading keys: {e}")
    sys.exit(1)

state_manager = StateManager(DB_PATH)

def create_bitget_connection():
    for attempt in range(params['max_retries']):
        try:
            return BitgetFutures(api_setup)
        except Exception as e:
            logger.error(f"Connection error (Attempt {attempt+1}/{params['max_retries']}): {e}")
            if attempt < params['max_retries'] - 1: time.sleep(params['retry_delay'])
    logger.critical("API connection failed")
    send_telegram_message(f"❌ *Critical Error:* API connection to Bitget failed for {params['symbol']}.")
    sys.exit(1)

bitget = create_bitget_connection()

# --- SET LEVERAGE ON BITGET ---
try:
    leverage_to_set = int(params.get('leverage', 1))
    logger.info(f"Attempting to set leverage for {params['symbol']} to {leverage_to_set}x...")
    bitget.set_leverage(params['symbol'], leverage_to_set)
    logger.info(f"Leverage successfully set to {leverage_to_set}x.")
except Exception as e:
    logger.error(f"Error setting leverage: {e}")
    send_telegram_message(f"⚠️ *Warning ({params['symbol']}):* Could not set leverage to {leverage_to_set}x: {e}")
# --- END OF LEVERAGE BLOCK ---

# --- MAIN FUNCTIONS ---

def open_new_position(side, data):
    try:
        balance = bitget.fetch_balance().get('USDT', {}).get('total', 0.0)
        trade_size_usdt = (balance * (params['trade_size_pct'] / 100)) * params['leverage']
        min_trade_cost = 5.0

        if trade_size_usdt < min_trade_cost:
            msg = f"Trade size ({trade_size_usdt:.2f} USDT) is too small. Minimum is {min_trade_cost} USDT."
            logger.error(msg)
            send_telegram_message(f"⚠️ *Trade not opened ({params['symbol']}):* {msg}")
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
                sl_text = f"with initial stop-loss at {stop_loss_price:.4f}"
                logger.info(f"{position_type} position opened at {current_price:.4f}, {sl_text}")
                send_telegram_message(f"✅ *{position_type} position opened ({params['symbol']}):* @ {current_price:.4f} USDT\n{sl_text}")
            else:
                raise Exception("Could not extract Stop-Loss ID from order response.")
        else:
            state_manager.set_state(status="in_trade", last_side=side, stop_loss_ids=[])
            logger.info("Position opened without stop-loss.")
            send_telegram_message(f"✅ *Position opened ({params['symbol']}) WITHOUT Stop-Loss*")

    except Exception as e:
        msg = f"Error opening {side} position: {e}"
        logger.error(msg)
        send_telegram_message(f"❌ *Error on position open ({params['symbol']}):* {msg}")
        state_manager.set_state(status="ok_to_trade")

def manage_trailing_stop(position_info, data):
    if not params.get('enable_trailing_stop_loss', False):
        return

    state = state_manager.get_state()
    if not state.get('stop_loss_ids'):
        logger.info("No Stop-Loss ID found for trailing stop. Will attempt to set a new one based on Supertrend line.")
    
    try:
        # The Supertrend line of the last closed candle is our new SL target
        new_trailing_stop_price = data.iloc[-2]['supertrend_line']
        current_sl_id = state['stop_loss_ids'][0] if state.get('stop_loss_ids') else None
        current_sl_price = 0.0

        if current_sl_id:
            open_orders = bitget.fetch_open_trigger_orders(params['symbol'])
            current_sl_order = next((o for o in open_orders if o['id'] == current_sl_id), None)
            if current_sl_order:
                current_sl_price = float(current_sl_order['stopPrice'])
            else:
                logger.warning(f"Saved SL order {current_sl_id} no longer found. Will be reset.")
                current_sl_id = None # Force setting a new order
        
        should_trail = False
        if position_info['side'] == 'long' and new_trailing_stop_price > current_sl_price:
            should_trail = True
        elif position_info['side'] == 'short' and new_trailing_stop_price < current_sl_price:
            should_trail = True
        
        # If no SL exists, but trailing is active, set the first one
        if not current_sl_id and params.get('enable_trailing_stop_loss'):
            should_trail = True
            logger.info("No active SL. Setting trailing stop to Supertrend line.")

        if should_trail:
            logger.info(f"Trailing Stop: Moving SL from {current_sl_price:.4f} to {new_trailing_stop_price:.4f}")
            
            # Cancel existing SL if it exists
            if current_sl_id:
                bitget.cancel_trigger_order(current_sl_id, params['symbol'])
            
            amount = float(position_info['contracts'])
            sl_side = 'sell' if position_info['side'] == 'long' else 'buy'
            new_sl_order = bitget.place_trigger_market_order(params['symbol'], sl_side, amount, new_trailing_stop_price, reduce=True)
            
            if new_sl_order and new_sl_order.get('id'):
                state_manager.set_state("in_trade", last_side=state['last_side'], stop_loss_ids=[new_sl_order['id']])
                send_telegram_message(f"📈 *Trailing Stop Update ({params['symbol']}):* New SL at {new_trailing_stop_price:.4f} USDT")
            else:
                raise Exception("Could not extract new Trailing Stop-Loss ID.")

    except Exception as e:
        logger.error(f"Error during trailing stop management: {e}")
        send_telegram_message(f"⚠️ *Warning ({params['symbol']}):* Error in Trailing Stop Management: {e}")

# --- MAIN LOGIC ---
def main():
    try:
        # 1. Fetch data and calculate signals
        data = bitget.fetch_recent_ohlcv(params['symbol'], params['timeframe'], 200)
        data = calculate_signals(data, params)
        last_candle = data.iloc[-2] # Previous, closed candle for decision making
        
        # 2. Check positions and status
        positions = bitget.fetch_open_positions(params['symbol'])
        is_position_open = len(positions) > 0
        state = state_manager.get_state()

        # 3. Synchronization: DB Status <> Exchange Status
        if state['status'] == "in_trade" and not is_position_open:
            logger.warning("Tracker was 'in_trade', but no position found. Resetting.")
            send_telegram_message(f"ℹ️ *Info ({params['symbol']}):* Position was closed externally. Resetting bot status.")
            state_manager.set_state(status="ok_to_trade")
            state = state_manager.get_state()
        
        # --- LOGIC FOR OPEN POSITIONS ---
        if is_position_open:
            pos_info = positions[0]
            logger.info(f"Holding open {pos_info['side']} position. PnL: {pos_info.get('unrealizedPnl', 0):.2f} USDT")

            # A. Check for close signal (flip)
            should_close_long = pos_info['side'] == 'long' and last_candle['sell_signal']
            should_close_short = pos_info['side'] == 'short' and last_candle['buy_signal']
            
            if should_close_long or should_close_short:
                closed_side_msg = "LONG" if should_close_long else "SHORT"
                logger.info(f"{closed_side_msg} position closed due to counter-signal.")
                send_telegram_message(f"🚪 *Position closed ({params['symbol']}):* {closed_side_msg} due to counter-signal.")
                
                # Cancel old SL orders
                if state.get('stop_loss_ids'):
                    for sl_id in state['stop_loss_ids']: bitget.cancel_trigger_order(sl_id, params['symbol'])
                
                bitget.flash_close_position(params['symbol'])
                
                # Flip: Immediately open a new position in the other direction
                if should_close_long and params['use_shorts']:
                    logger.info("Counter-signal (SELL) detected. Immediately opening Short position.")
                    open_new_position('sell', data)
                elif should_close_short and params['use_longs']:
                    logger.info("Counter-signal (BUY) detected. Immediately opening Long position.")
                    open_new_position('buy', data)
                else:
                    state_manager.set_state(status="ok_to_trade")
            
            # B. If no close signal, manage trailing stop
            else:
                manage_trailing_stop(pos_info, data)

        # --- LOGIC FOR NO OPEN POSITION ---
        else:
            if last_candle['buy_signal'] and params['use_longs']:
                logger.info("Buy signal detected. Opening new Long position.")
                open_new_position('buy', data)
            elif last_candle['sell_signal'] and params['use_shorts']:
                logger.info("Sell signal detected. Opening new Short position.")
                open_new_position('sell', data)
            else:
                logger.info("No new trading signal found.")

    except Exception as e:
        logger.error(f"An unexpected error occurred in the main loop: {e}")
        send_telegram_message(f"❌ *Unexpected Error ({params['symbol']}):* {e}")

if __name__ == "__main__":
    main()
    logger.info(f"<<< Execution finished\n")
