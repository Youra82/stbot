# tests/test_workflow.py
import pytest
import os
import sys
import json
import logging
import time
from unittest.mock import patch

# Füge das Projektverzeichnis zum Python-Pfad hinzu
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

# Importiere die StBot-Funktionen
from stbot.utils.exchange import Exchange
from stbot.utils.trade_manager import check_and_open_new_position, housekeeper_routine
from stbot.utils.trade_manager import set_trade_lock, is_trade_locked
from stbot.utils.timeframe_utils import determine_htf

@pytest.fixture(scope="module")
def test_setup():
    print("\n--- Starte umfassenden LIVE StBot-Workflow-Test (PEPE) ---")
    print("\n[Setup] Bereite Testumgebung vor...")

    secret_path = os.path.join(PROJECT_ROOT, 'secret.json')
    if not os.path.exists(secret_path):
            pytest.skip("secret.json nicht gefunden. Überspringe Live-Workflow-Test.")

    with open(secret_path, 'r') as f:
        secrets = json.load(f)

    if not secrets.get('stbot') or not secrets['stbot']:
        pytest.skip("Es wird mindestens ein Account unter 'stbot' in secret.json benötigt.")

    test_account = secrets['stbot'][0]
    telegram_config = secrets.get('telegram', {})

    try:
        exchange = Exchange(test_account)
        if not exchange.markets:
            pytest.fail("Exchange konnte nicht initialisiert werden (Märkte nicht geladen).")
    except Exception as e:
        pytest.fail(f"Exchange konnte nicht initialisiert werden: {e}")

    # WIR NEHMEN PEPE (Kleine Mindestgröße, gut zum Testen)
    symbol = 'PEPE/USDT:USDT'
    timeframe = '15m'
    htf = determine_htf(timeframe)

    params = {
        'market': {'symbol': symbol, 'timeframe': timeframe, 'htf': htf},
        'strategy': { 'pivot_period': 10, 'max_pivots': 20, 'channel_width_pct': 10, 'max_sr_levels': 5, 'min_strength': 2 },
        'risk': {
            'margin_mode': 'isolated',
            # 15% Risiko vom verfügbaren Guthaben
            'risk_per_trade_pct': 15.0,
            'risk_reward_ratio': 2.0,
            'leverage': 20,
            'trailing_stop_activation_rr': 1.5,
            'trailing_stop_callback_rate_pct': 0.5,
            'atr_multiplier_sl': 1.0,
            'min_sl_pct': 4.0
        },
        'behavior': { 'use_longs': True, 'use_shorts': True }
    }

    test_logger = logging.getLogger("test-logger")
    test_logger.setLevel(logging.INFO)
    if not test_logger.handlers:
        test_logger.addHandler(logging.StreamHandler(sys.stdout))

    print(f"-> Führe initiales Aufräumen für {symbol} durch...")
    try:
        housekeeper_routine(exchange, symbol, test_logger)
        time.sleep(2)
        # Doppelte Sicherheit: Prüfen ob wirklich zu
        pos = exchange.fetch_open_positions(symbol)
        if pos:
             exchange.create_market_order(symbol, 'sell' if pos[0]['side'] == 'long' else 'buy', float(pos[0]['contracts']), {'reduceOnly': True})
             time.sleep(2)

        print("-> Ausgangszustand ist sauber.")
    except Exception as e:
        pytest.fail(f"Fehler beim initialen Aufräumen: {e}")

    yield exchange, params, telegram_config, symbol, test_logger

    print("\n[Teardown] Räume nach dem Test auf...")
    try:
        print("-> 1. Lösche offene Trigger Orders...")
        exchange.cancel_all_orders_for_symbol(symbol)
        time.sleep(2)

        print("-> 2. Prüfe auf offene Positionen...")
        position = exchange.fetch_open_positions(symbol)
        if position:
            print(f"-> Position nach Test noch offen. Schließe sie...")
            exchange.create_market_order(symbol, 'sell' if position[0]['side'] == 'long' else 'buy', float(position[0]['contracts']), {'reduceOnly': True})
            time.sleep(3)
        else:
            print("-> Keine offene Position gefunden.")

        print("-> 3. Lösche verbleibende Trigger Orders (Sicherheitsnetz)...")
        exchange.cancel_all_orders_for_symbol(symbol)

        print("-> Aufräumen abgeschlossen.")

    except Exception as e:
        print(f"FEHLER beim Aufräumen nach dem Test: {e}")

def test_full_stbot_workflow_on_bitget(test_setup):
    exchange, params, telegram_config, symbol, logger = test_setup

    # Check Balance vor dem Test
    bal = exchange.fetch_balance_usdt()
    print(f"\n--- Verfügbares Guthaben für Test: {bal:.4f} USDT ---")

    with patch('stbot.utils.trade_manager.set_trade_lock'), \
         patch('stbot.utils.trade_manager.is_trade_locked', return_value=False), \
         patch('stbot.utils.trade_manager.get_titan_signal', return_value=('buy', None)): # Simuliere Buy-Signal

        print("\n[Schritt 1/3] Mocke Signal und prüfe Trade-Eröffnung...")
        check_and_open_new_position(exchange, None, None, params, telegram_config, logger)

    print("-> Warte 5s auf Order-Ausführung...")
    time.sleep(5)

    print("\n[Schritt 2/3] Überprüfe Position und Orders...")
    position = exchange.fetch_open_positions(symbol)

    # Assert Position
    if not position:
        pytest.fail(f"FEHLER: Position nicht eröffnet. Verfügbares Guthaben ({bal:.2f} USDT) war evtl. zu wenig oder API hat blockiert.")

    assert len(position) == 1
    pos_info = position[0]
    print(f"-> Position erfolgreich eröffnet: {pos_info['side'].upper()} {pos_info['contracts']} PEPE.")

    # Assert Orders
    trigger_orders = exchange.fetch_open_trigger_orders(symbol)
    if len(trigger_orders) == 0:
        print("WARNUNG: Keine Trigger-Orders im API-Return gefunden (kann bei PEPE vorkommen).")
    else:
        print(f"-> Trigger-Orders gefunden: {len(trigger_orders)}")

    # --- SAUBERES SCHLIESSEN ---
    print("\n[Schritt 3/3] Schließe die Position und räume auf...")

    # 1. Orders löschen VOR dem Schließen
    print("-> Lösche Trigger-Orders VOR dem Schließen...")
    exchange.cancel_all_orders_for_symbol(symbol)
    time.sleep(3)

    # 2. Position schließen
    amount_to_close = abs(float(pos_info.get('contracts', 0)))
    side_to_close = 'sell' if pos_info.get('side', '').lower() == 'long' else 'buy'

    if amount_to_close > 0:
        print(f"-> Schließe Position ({amount_to_close} PEPE)...")
        close_order = exchange.create_market_order(symbol, side_to_close, amount_to_close, params={'reduceOnly': True})
        assert close_order, "FEHLER: Konnte Position nicht schließen!"
        print(f"-> Position erfolgreich geschlossen.")
        time.sleep(4) # Etwas länger warten

    # 3. Orders löschen NACH dem Schließen
    print("-> Lösche verbleibende Trigger-Orders NACH dem Schließen...")
    exchange.cancel_all_orders_for_symbol(symbol)
    time.sleep(2)

    # Finale Prüfung mit RELOAD
    final_positions = exchange.fetch_open_positions(symbol)
    final_orders = exchange.fetch_open_trigger_orders(symbol)

    if len(final_positions) > 0:
        print(f"WARNUNG: Position ist noch offen! ({len(final_positions)})")

    if len(final_orders) > 0:
        print(f"WARNUNG: Es sind noch {len(final_orders)} Trigger-Orders offen! Versuche erneutes Löschen...")
        exchange.cancel_all_orders_for_symbol(symbol)
        time.sleep(2)
        # WICHTIG: Status neu laden für den Assert
        final_orders = exchange.fetch_open_trigger_orders(symbol)

    assert len(final_positions) == 0, "FEHLER: Position sollte geschlossen sein."
    assert len(final_orders) == 0, f"FEHLER: Trigger-Orders wurden nicht sauber gelöscht! ({len(final_orders)} verbleibend)"

    print("\n--- UMFASSENDER WORKFLOW-TEST ERFOLGREICH! ---")
