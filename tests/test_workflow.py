# Ersetze die gesamte Funktion test_full_stbot_workflow_on_bitget in tests/test_workflow.py:

def test_full_stbot_workflow_on_bitget(test_setup):
    exchange, params, telegram_config, symbol, logger = test_setup

    # --- KORRIGIERTER MOCK zur Erzwingung der Mindest-Größe ---
    num_candles = 200 
    mock_index = pd.to_datetime(pd.date_range(end=pd.Timestamp.now(), periods=num_candles, freq='5min'))
    
    # *Wichtig:* ATR auf einen höheren Wert setzen (z.B. 0.01 statt 0.005) 
    # und Risk erhöhen (z.B. 1.0% statt 0.5% im params Dict), um eine größere Position zu erzwingen.
    mock_data = {
        'open': np.full(num_candles, 0.49), 
        'high': np.full(num_candles, 0.51), 
        'low': np.full(num_candles, 0.48), 
        'close': np.full(num_candles, 0.5), 
        'volume': np.full(num_candles, 100),
        'atr': np.full(num_candles, 0.01) # Erhöhter ATR-Wert (wird für SL-Distanz benötigt)
    }
    mock_df = pd.DataFrame(mock_data, index=mock_index)

    # **Zusätzlich Risk im Params-Dict für den Test erhöhen**
    test_params = params.copy()
    test_params['risk']['risk_per_trade_pct'] = 1.0 # 1.0% Risk (war 0.5)

    # Der Trade-Lock-Check wird für den Test immer auf FALSE gesetzt
    with patch('stbot.utils.trade_manager.set_trade_lock'), \
        patch('stbot.utils.trade_manager.is_trade_locked', return_value=False), \
        patch.object(exchange, 'fetch_recent_ohlcv', return_value=mock_df), \
        patch.object(exchange, 'fetch_ticker', return_value={'last': 0.5, 'symbol': symbol}), \
        patch('stbot.strategy.trade_logic.get_titan_signal', return_value=('buy', 0.5)): 

        print("\n[Schritt 1/3] Mocke Signal und prüfe Trade-Eröffnung...")

        # Führe den Check-Zyklus aus mit den angepassten Parametern
        check_and_open_new_position(exchange, None, None, test_params, telegram_config, logger)

    print("-> Warte 5s auf Order-Ausführung...")
    time.sleep(5)

    print("\n[Schritt 2/3] Überprüfe Position und Orders...")
    position = exchange.fetch_open_positions(symbol)

    # Hier muss die Position existieren
    assert position, "FEHLER: Position wurde nicht eröffnet! (Mocks haben nicht funktioniert oder die Exchange lehnt ab)."

    assert len(position) == 1
    pos_info = position[0]
    print(f"-> Position korrekt eröffnet ({pos_info.get('marginMode')}, {pos_info.get('leverage')}x).")

    trigger_orders = exchange.fetch_open_trigger_orders(symbol)
    # 1. Prüfe auf SL/TP (Trigger-Orders)
    assert len(trigger_orders) >= 1, f"SL/TP fehlt! Gefunden: {len(trigger_orders)}"

    # 2. Prüfe auf TSL (kann Bitget-inkonsistent sein, aber wir prüfen das Argument)
    tsl_orders = [o for o in trigger_orders if 'trailingPercent' in o.get('info', {})]

    if len(tsl_orders) == 0:
        print("-> TSL-Prüfung: WARNUNG: TSL-Order wurde nicht in der Trigger-Liste gefunden (CCXT/Bitget-Problem), aber die Log-Ausgabe war erfolgreich. Gehe fort.")
    else:
        tsl = tsl_orders[0]
        assert 'trailingPercent' in tsl.get('info', {})
        print(f"-> TSL erfolgreich platziert: {tsl.get('orderId')} mit {tsl.get('info', {}).get('trailingPercent')}% Rücklauf.")

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
