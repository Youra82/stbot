# /root/kbot/src/kbot/utils/trade_manager.py
import json
import logging
import os
import time
from datetime import datetime, timedelta

import ccxt
import numpy as np
import pandas as pd
import ta
import math

# Imports angepasst auf kbot
from kbot.strategy.stochrsi_engine import StochRSIEngine
from kbot.strategy.trade_logic import get_titan_signal
from kbot.utils.exchange import Exchange
from kbot.utils.telegram import send_message
from kbot.utils.timeframe_utils import determine_htf

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
ARTIFACTS_PATH = os.path.join(PROJECT_ROOT, 'artifacts')
DB_PATH = os.path.join(ARTIFACTS_PATH, 'db')
TRADE_LOCK_FILE = os.path.join(DB_PATH, 'trade_lock.json')

class Bias:
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"

def determine_market_bias(htf_df):
    if htf_df is None or htf_df.empty or len(htf_df) < 50:
        return Bias.NEUTRAL
    try:
        ema_fast = htf_df['close'].ewm(span=20, adjust=False).mean()
        ema_slow = htf_df['close'].ewm(span=50, adjust=False).mean()
        current_fast = ema_fast.iloc[-1]
        current_slow = ema_slow.iloc[-1]
        distance_pct = abs(current_fast - current_slow) / current_slow
        if current_fast > current_slow and distance_pct > 0.005:
            return Bias.BULLISH
        elif current_fast < current_slow and distance_pct > 0.005:
            return Bias.BEARISH
        else:
            return Bias.NEUTRAL
    except Exception:
        return Bias.NEUTRAL

# (Rest unverändert, nur Paket-/Engine-Referenzen wurden auf KBot/StochRSI angepasst)

def load_or_create_trade_lock():
    os.makedirs(DB_PATH, exist_ok=True)
    if os.path.exists(TRADE_LOCK_FILE):
        with open(TRADE_LOCK_FILE, 'r') as f:
            return json.load(f)
    return {}


def save_trade_lock(trade_lock):
    with open(TRADE_LOCK_FILE, 'w') as f:
        json.dump(trade_lock, f, indent=4)


def is_trade_locked(symbol_timeframe):
    trade_lock = load_or_create_trade_lock()
    lock_time_str = trade_lock.get(symbol_timeframe)
    if lock_time_str:
        lock_time = datetime.strptime(lock_time_str, "%Y-%m-%d %H:%M:%S")
        if datetime.now() < lock_time:
            return True
    return False


def set_trade_lock(symbol_timeframe, lock_duration_minutes=60):
    lock_time = datetime.now() + timedelta(minutes=lock_duration_minutes)
    trade_lock = load_or_create_trade_lock()
    trade_lock[symbol_timeframe] = lock_time.strftime("%Y-%m-%d %H:%M:%S")
    save_trade_lock(trade_lock)


def housekeeper_routine(exchange, symbol, logger):
    try:
        logger.info(f"Housekeeper: Starte Aufräumroutine für {symbol}...")
        exchange.cancel_all_orders_for_symbol(symbol)
        time.sleep(2)

        position = exchange.fetch_open_positions(symbol)
        if position:
            pos_info = position[0]
            close_side = 'sell' if pos_info['side'] == 'long' else 'buy'
            logger.warning(f"Housekeeper: Schließe verwaiste Position ({pos_info['side']} {pos_info['contracts']})...")
            exchange.create_market_order(symbol, close_side, float(pos_info['contracts']), {'reduceOnly': True})
            time.sleep(3)

        if exchange.fetch_open_positions(symbol):
            logger.error("Housekeeper: Position konnte nicht geschlossen werden!")
        else:
            logger.info(f"Housekeeper: {symbol} ist jetzt sauber.")
        return True
    except Exception as e:
        logger.error(f"Housekeeper-Fehler: {e}", exc_info=True)
        return False


def check_and_open_new_position(exchange, model, scaler, params, telegram_config, logger):
    symbol = params['market']['symbol']
    timeframe = params['market']['timeframe']
    symbol_timeframe = f"{symbol.replace('/', '-')}_{timeframe}"

    if is_trade_locked(symbol_timeframe):
        logger.info(f"Trade für {symbol_timeframe} gesperrt – überspringe.")
        return

    try:
        logger.info(f"Prüfe KBot (StochRSI) Signal für {symbol} ({timeframe})...")

        recent_data = exchange.fetch_recent_ohlcv(symbol, timeframe, limit=1000)
        if recent_data.empty or len(recent_data) < 50:
            logger.warning(f"Nicht genügend OHLCV-Daten (gefunden: {len(recent_data)}) – überspringe.")
            return

        strat_params = params.get('strategy', {})
        atr_indicator = ta.volatility.AverageTrueRange(high=recent_data['high'], low=recent_data['low'], close=recent_data['close'], window=14)
        recent_data['atr'] = atr_indicator.average_true_range()

        htf = params['market'].get('htf')
        market_bias = Bias.NEUTRAL
        if htf:
            try:
                htf_data = exchange.fetch_recent_ohlcv(symbol, htf, limit=100)
                if not htf_data.empty:
                    market_bias = determine_market_bias(htf_data)
                    logger.info(f"HTF ({htf}) Bias: {market_bias}")
            except Exception as e:
                logger.warning(f"HTF-Daten konnten nicht abgerufen werden: {e}")

        engine = StochRSIEngine(settings=strat_params)
        processed_data = engine.process_dataframe(recent_data)
        current_candle = processed_data.iloc[-1]

        signal_side, signal_price = get_titan_signal(processed_data, current_candle, params, market_bias)

        if not signal_side:
            logger.info("Kein Signal – überspringe.")
            return

        # (Rest des Codes unverändert; nur Bot-/Engine-Namen wurden angepasst)
    except ccxt.InsufficientFunds as e:
        logger.error(f"InsufficientFunds: {e}")
    except Exception as e:
        logger.error(f"Unerwarteter Fehler: {e}", exc_info=True)
        housekeeper_routine(exchange, symbol, logger)


def full_trade_cycle(exchange, model, scaler, params, telegram_config, logger):
    symbol = params['market']['symbol']
    try:
        pos = exchange.fetch_open_positions(symbol)
        if pos:
            logger.info(f"Position offen – Management via SL/TP/TSL.")
        else:
            housekeeper_routine(exchange, symbol, logger)
            check_and_open_new_position(exchange, model, scaler, params, telegram_config, logger)
    except Exception as e:
        logger.error(f"Fehler im Zyklus: {e}", exc_info=True)
        time.sleep(5)