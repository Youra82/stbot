"""Lokale Tests fuer die Log-Auswertung im Tagescheck (keine Bitget-Aufrufe)."""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import pandas as pd

import daily_live_vs_backtest_check as mod

LOCAL_TZ = datetime.now().astimezone().tzinfo
DECISION = pd.Timestamp('2026-10-18 20:00', tz='UTC')


def _write_log(tmp_path, key, entries):
    """entries: (Minuten nach Kerzenschluss, Level, Meldung) -- Zeitstempel in Ortszeit wie RotatingFileHandler."""
    lines = []
    for minutes, level, msg in entries:
        ts = (DECISION + pd.Timedelta(minutes=minutes)).tz_convert(LOCAL_TZ)
        lines.append(f"{ts.strftime('%Y-%m-%d %H:%M:%S')},123 - {level} - {msg}\n")
    (tmp_path / f'stbot_{key}.log').write_text(''.join(lines), encoding='utf-8')


def test_min_amount_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, 'LOG_DIR', str(tmp_path))
    _write_log(tmp_path, 'ETHUSDTUSDT_4h', [
        (-240, 'INFO', 'Prüfe StBot (SRv2) Signal für ETH/USDT:USDT (4h)...'),
        (0.5, 'INFO', 'Prüfe StBot (SRv2) Signal für ETH/USDT:USDT (4h)...'),
        (0.6, 'ERROR', 'Ordergröße 0.009700 (22.90 USDT) < Mindestmenge 0.01 – überspringe.'),
    ])
    assert mod.explain_from_log('ETHUSDTUSDT_4h', DECISION).startswith('Order unter Bitget-Mindestmenge')


def test_no_signal_and_late_signal(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, 'LOG_DIR', str(tmp_path))
    _write_log(tmp_path, 'RSRUSDTUSDT_2h', [(0.5, 'INFO', 'Kein Signal – überspringe.')])
    assert mod.explain_from_log('RSRUSDTUSDT_2h', DECISION).startswith('Live-Code sah kein Signal')
    _write_log(tmp_path, 'XRPUSDTUSDT_4h', [
        (25, 'INFO', 'Kerze 2026-10-18 16:00:00+00:00 schloss vor 0:25:00 (> 0:20:00) -- Signal verfaellt.')])
    assert mod.explain_from_log('XRPUSDTUSDT_4h', DECISION).startswith('Signal zu alt')


def test_bot_not_running_and_missing_log(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, 'LOG_DIR', str(tmp_path))
    _write_log(tmp_path, 'ENAUSDTUSDT_15m', [(-120, 'INFO', 'Kein Signal – überspringe.')])
    assert mod.explain_from_log('ENAUSDTUSDT_15m', DECISION).startswith('Bot lief in dem Zeitraum nicht')
    assert mod.explain_from_log('BCHUSDTUSDT_2h', DECISION) == 'kein Bot-Log vorhanden'


def test_active_periods_follow_team_history():
    history = [
        {'valid_from': '2026-10-14T22:20:00+00:00', 'team': {'ETHUSDTUSDT_4h': None, 'ENAUSDTUSDT_15m': None}},
        {'valid_from': '2026-10-19T15:00:00+00:00', 'team': {'ENAUSDTUSDT_15m': None}},
        {'valid_from': '2026-10-26T15:00:00+00:00', 'team': {'ETHUSDTUSDT_4h': None, 'ENAUSDTUSDT_15m': None}},
    ]
    ts = pd.Timestamp
    assert mod.active_periods(history, 'ETHUSDTUSDT_4h') == [
        (ts('2026-10-14T22:20:00+00:00'), ts('2026-10-19T15:00:00+00:00')),
        (ts('2026-10-26T15:00:00+00:00'), None)]
    assert mod.active_periods(history, 'ENAUSDTUSDT_15m') == [(ts('2026-10-14T22:20:00+00:00'), None)]


def test_position_without_stop_loss_is_reported():
    pos = [{'symbol': 'XRPUSDT', 'holdSide': 'short', 'markPrice': '1.2781'},
           {'symbol': 'ARBUSDT', 'holdSide': 'long', 'markPrice': '0.168'}]
    plans = [  # XRP: nur Trailing (unter dem Kurs, kein SL); ARB: SL an der Einstiegs-Order
        {'symbol': 'XRPUSDT', 'planType': 'track_plan', 'tradeSide': 'close', 'posSide': 'short', 'triggerPrice': '1.22'},
        {'symbol': 'ARBUSDT', 'planType': 'profit_loss', 'tradeSide': 'close', 'posSide': 'long',
         'triggerPrice': '0.108', 'stopLossTriggerPrice': '0.108'},
    ]
    flags = mod.positions_without_sl(pos, plans)
    assert len(flags) == 1 and 'XRP short OHNE Stop-Loss' in flags[0]
    plans.append({'symbol': 'XRPUSDT', 'planType': 'normal_plan', 'tradeSide': 'close', 'posSide': 'short',
                  'triggerPrice': '1.41'})
    assert mod.positions_without_sl(pos, plans) == []
