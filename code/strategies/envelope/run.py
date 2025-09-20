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

# --- SETUP (bleibt gleich) ---
# ... (Ihr bestehender Setup-Code für Logging, Config, DB etc. bleibt hier) ...

# --- NEUE, ROBUSTE KERNLOGIK ---

def sync_state_with_exchange(bitget, symbol, open_position, trade_state, prev_candle, bot_token, chat_id):
    """
    Die neue Kernfunktion. Gleicht den Zustand ab und führt notwendige Korrekturen durch.
    Gibt den bereinigten Zustand zurück.
    """
    if open_position:
        # --- FALL 1: EINE POSITION IST AUF DER BÖRSE OFFEN ---
        
        if not trade_state:
            # Position existiert, aber nicht in unserer DB -> "Adoption"
            logger.warning("⚠️ Fremde Position entdeckt! Übernehme die Verwaltung...")
            update_trade_state(open_position['side']) 
            trade_state = get_trade_state()
            send_telegram_message(bot_token, chat_id, f"⚠️ *{symbol}*: Fremde {open_position['side']}-Position entdeckt und Verwaltung übernommen.")

        logger.info("Position auf Börse gefunden. Überprüfe Stop-Loss-Integrität...")
        
        # Finde alle relevanten SL-Orders
        all_orders = bitget.fetch_open_orders(symbol)
        sl_side = 'sell' if open_position['side'] == 'long' else 'buy'
        stop_orders = [o for o in all_orders if o.get('stopPrice') is not None and o['side'] == sl_side]
        
        sl_count = len(stop_orders)
        
        if sl_count > 1:
            # Zu viele SLs -> Alle löschen
            logger.warning(f"⚠️ {sl_count} Stop-Loss-Orders gefunden! Räume auf...")
            for order in stop_orders:
                bitget.cancel_order(order['id'], symbol)
            sl_count = 0 # Setze auf 0, damit ein neuer, sauberer SL platziert wird
        
        if sl_count == 0:
            logger.warning("⚠️ Kein gültiger Stop-Loss gefunden! Platziere Notfall-SL...")
            
            # Berechne Notfall-SL basierend auf aktuellen Daten
            if open_position['side'] == 'long':
                sl_price = prev_candle['swing_low'] * (1 - params['risk']['sl_buffer_pct'] / 100)
            else: # short
                sl_price = prev_candle['swing_high'] * (1 + params['risk']['sl_buffer_pct'] / 100)
            
            bitget.place_stop_order(symbol, sl_side, float(open_position['contracts']), sl_price)
            update_trade_state(open_position['side'], sl_price) # Speichere den neuen SL
            send_telegram_message(bot_token, chat_id, f"⚠️ *{symbol}*: Position war ungeschützt! Notfall-Stop-Loss wurde platziert.")
        
        else: # sl_count == 1
            logger.info("✅ Ein korrekter Stop-Loss ist bereits vorhanden.")
            
    else:
        # --- FALL 2: KEINE POSITION IST AUF DER BÖRSE OFFEN ---
        
        # Prüfe, ob es verwaiste Orders gibt
        open_orders = bitget.fetch_open_orders(symbol)
        if open_orders:
            logger.warning("Keine Position, aber verwaiste Orders gefunden! Räume auf...")
            for order in open_orders:
                bitget.cancel_order(order['id'], symbol)
        
        # Setze den DB-Status zurück, falls nötig
        if trade_state:
            logger.info("Position wurde extern geschlossen. Setze internen Status zurück.")
            update_trade_state('none')
            trade_state = None
            
    return get_trade_state() # Gib den finalen, sauberen Zustand zurück

# --- IHR BESTEHENDER CODE (leicht angepasst) ---

def main():
    logger.info(f">>> Starte Ausführung für {SYMBOL} (stbot v3.0 - Proaktive Synchronisation)")
    
    # ... (Ihr Code zum Laden der Secrets) ...
    
    bitget = BitgetFutures(api_setup)
    setup_database()
    
    try:
        # 1. REALITÄT PRÜFEN
        positions = bitget.fetch_open_positions(SYMBOL)
        open_position = positions[0] if positions else None
        trade_state = get_trade_state()
        
        # 2. DATEN LADEN
        data = bitget.fetch_recent_ohlcv(SYMBOL, params['market']['timeframe'], 500)
        data = calculate_stochrsi_indicators(data, params['strategy'])
        prev_candle = data.iloc[-2]
        current_candle = data.iloc[-1]
        
        # 3. SYNCHRONISIEREN & AUFRÄUMEN
        trade_state = sync_state_with_exchange(bitget, SYMBOL, open_position, trade_state, prev_candle, bot_token, chat_id)
        open_position = bitget.fetch_open_positions(SYMBOL)[0] if bitget.fetch_open_positions(SYMBOL) else None # Lese Position neu nach dem Aufräumen

        # 4. HANDELN (basierend auf dem sauberen Zustand)
        if open_position:
            # Logik zum Schließen (Take-Profit)
            # ...
        else:
            # Logik zum Eröffnen
            # ...

    except Exception as e:
        # ... (Ihr Error-Handling) ...

if __name__ == "__main__":
    main()
    logger.info("<<< Ausführung abgeschlossen\n")
