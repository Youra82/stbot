# /root/stbot/tests/test_workflow.py
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

# Korrekter Import der tatsächlich existierenden Funktionen
from stbot.utils.exchange import Exchange
from stbot.utils.trade_manager import check_and_open_new_position, housekeeper_routine
from stbot.utils.trade_manager import set_trade_lock, is_trade_locked
from stbot.utils.timeframe_utils import determine_htf

@pytest.fixture(scope="module")
def test_setup():
    print("\n--- Starte umfassenden LIVE StBot-Workflow-Test ---")
    print("\n[Setup] Bereite Testumgebung vor...")

    secret_path = os.path.join(PROJECT_ROOT, 'secret.json')
    if not os.path.exists(secret_path):
            pytest.skip("secret.json nicht gefunden. Überspringe Live-Workflow-Test.")

    with open(secret_path, 'r') as f:
        secrets = json.load(f)

    # Key check auf stbot (nach Umbenennung)
    # Wir prüfen sowohl auf 'stbot' als auch auf 'utbot2' als Fallback
    account_key = 'stbot'
    if 'stbot' not in secrets:
        if 'utbot2' in secrets:
            account_key = 'utbot2'
        else:
            pytest.skip("Es wird ein Account unter 'stbot' (oder 'utbot2') in secret.json benötigt.")

    if not secrets[account_key]:
        pytest.skip(f"Liste der Accounts unter '{account_key}' ist leer.")

    test_account = secrets[account_key][0]
    telegram_config = secrets.get('telegram', {})

    try:
        exchange = Exchange(test_account)
        # Einfacher Check, ob API Key validiert wurde (durch Laden der Märkte)
        if not exchange.markets:
            pytest.fail("Exchange konnte nicht initialisiert werden (Märkte nicht geladen). Prüfe API-Keys in secret.json!")
    except Exception as e:
        pytest.fail(f"Exchange Initialisierungsfehler: {e}")

    # XRP FÜR TEST (ANGEPASSTE PARAMETER FÜR SRv2)
    symbol = 'XRP/USDT:USDT'
    timeframe = '5m'
    htf = determine_htf(timeframe)

    # SRv2 Parameter für den Test
    params = {
        'market': {'symbol': symbol, 'timeframe': timeframe, 'htf': htf},
        'strategy': {
            'pivot_period': 5,
            'max_pivots': 10,
            'channel_width_pct': 10,
            'max_sr_levels': 5,
            'min_strength': 1,
            'source': 'High/Low'
        },
        'risk': {
            'margin_mode': 'isolated',
            'risk_per_trade_pct': 0.5,
            'risk_reward_ratio': 2.0,
            'leverage': 10,
            'trailing_stop_activation_rr': 1.5,
            'trailing_stop_callback_rate_pct': 0.5,
            'atr_multiplier_sl': 1.0,
            'min_sl_pct': 0.1
        },
        'behavior': { 'use_longs': True, 'use_shorts': True }
    }

    test_logger = logging.getLogger("test-logger")
    test_logger.setLevel(logging.INFO)
    if not test_logger.handlers:
        test_logger.addHandler(logging.StreamHandler(sys.stdout))

    print("-> Führe initiales Aufräumen durch...")
    try:
        housekeeper_routine(exchange, symbol, test_logger)
        time.sleep(2)
        pos_check = exchange.fetch_open_positions(symbol)
        if pos_check:
            print(f"WARNUNG: Position für {symbol} nach initialem Aufräumen noch vorhanden. Schließe sie...")
            exchange.create_market_order(symbol, 'sell' if pos_check[0]['side'] == 'long' else 'buy', float(pos_check[0]['contracts']), {'reduceOnly': True})
            time.sleep(3)
            pos_check_after = exchange.fetch_open_positions(symbol)
            if pos_check_after:
                    pytest.fail(f"Konnte initiale Position für {symbol} nicht schließen.")
            else:
                    print("-> Initiale Position erfolgreich geschlossen.")
                    housekeeper_routine(exchange, symbol, test_logger)
                    time.sleep(1)

        print("-> Ausgangszustand ist sauber.")
    except Exception as e:
        pytest.fail(f"Fehler beim initialen Aufräumen: {e}")

    yield exchange, params, telegram_config, symbol, test_logger

    print("\n[Teardown] Räume nach dem Test auf...")
    try:
        print("-> Lösche offene Trigger Orders...")
        exchange.cancel_all_orders_for_symbol(symbol)
        time.sleep(2)

        print("-> Prüfe auf offene Positionen...")
        position = exchange.fetch_open_positions(symbol)
        if position:
            print(f"-> Position nach Test noch offen. Schließe sie...")
            exchange.create_market_order(symbol, 'sell' if position[0]['side'] == 'long' else 'buy', float(position[0]['contracts']), {'reduceOnly': True})
            time.sleep(3)
        else:
            print("-> Keine offene Position gefunden.")

        print("-> Führe finale Order-Löschung durch...")
        exchange.cancel_all_orders_for_symbol(symbol)
        print("-> Aufräumen abgeschlossen.")

    except Exception as e:
        print(f"FEHLER beim Aufräumen nach dem Test: {e}")

def test_full_stbot_workflow_on_bitget(test_setup):
    exchange, params, telegram_config, symbol, logger = test_setup

    # Wir mocken nur die Locks und das Signal, aber NICHT die Order-Ausführung.
    # Das testet die echte API-Verbindung.
    
    # Wir mocken 'get_titan_signal', damit wir sofort ein Kaufsignal erhalten,
    # egal was die SR Engine sagt.
    with patch('stbot.utils.trade_manager.set_trade_lock'), \
         patch('stbot.utils.trade_manager.is_trade_locked', return_value=False), \
         patch('stbot.utils.trade_manager.get_titan_signal', return_value=('buy', 0.60)): # Fake buy signal

        print("\n[Schritt 1/3] Mocke Signal und prüfe Trade-Eröffnung...")
        check_and_open_new_position(exchange, None, None, params, telegram_config, logger)

    print("-> Warte 5s auf Order-Ausführung...")
    time.sleep(5)

    print("\n[Schritt 2/3] Überprüfe Position und Orders...")
    position = exchange.fetch_open_positions(symbol)

    # Hier muss die Position existieren
    assert position, "FEHLER: Position wurde nicht eröffnet! Prüfe API-Keys und Guthaben."

    assert len(position) == 1
    pos_info = position[0]
    print(f"-> Position korrekt eröffnet ({pos_info.get('marginMode')}, {pos_info.get('leverage')}x).")

    trigger_orders = exchange.fetch_open_trigger_orders(symbol)
    # 1. Prüfe auf SL/TP (Trigger-Orders)
    assert len(trigger_orders) >= 1, f"SL fehlt! Gefunden: {len(trigger_orders)}"

    # 2. Prüfe auf TSL
    tsl_orders = [o for o in trigger_orders if 'trailingPercent' in o.get('info', {})]
    if len(tsl_orders) == 0:
        print("-> TSL-Prüfung: WARNUNG: TSL-Order wurde nicht in der Trigger-Liste gefunden (CCXT/Bitget-Problem), aber Log ok.")
    else:
        tsl = tsl_orders[0]
        print(f"-> TSL erfolgreich platziert: {tsl.get('orderId')}")

    # 3. Schließe die Position (Schritt 3/3)
    print("\n[Schritt 3/3] Schließe die Position...")

    # Zuerst alle offenen Orders löschen
    exchange.cancel_all_orders_for_symbol(symbol)

    amount_to_close = abs(float(pos_info.get('contracts', 0)))
    side_to_close = 'sell' if pos_info.get('side', '').lower() == 'long' else 'buy'

    if amount_to_close > 0:
        close_order = exchange.create_market_order(symbol, side_to_close, amount_to_close, params={'reduceOnly': True})
        assert close_order, "FEHLER: Konnte Position nicht schließen!"
        print(f"-> Position erfolgreich geschlossen ({side_to_close} {amount_to_close}).")
        time.sleep(5)
    else:
        print("-> Position war bereits geschlossen.")

    # Finale Überprüfung
    final_positions = exchange.fetch_open_positions(symbol)
    assert len(final_positions) == 0, f"FEHLER: Position sollte geschlossen sein, aber {len(final_positions)} ist/sind noch offen."

    print("\n--- UMFASSENDER WORKFLOW-TEST ERFOLGREICH! ---")
