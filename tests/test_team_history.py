"""Lokale Tests fuer die Teamhistorie (keine Bitget-Aufrufe)."""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
from stbot.utils.team_history import load_history, record_team, strategy_key, team_at


def _t(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def test_strategy_key():
    assert strategy_key('ENA/USDT:USDT', '15m') == 'ENAUSDTUSDT_15m'


def test_records_only_changes_and_answers_point_in_time(tmp_path):
    path = str(tmp_path / 'team_history.jsonl')
    week1 = ['ENAUSDTUSDT_15m', 'ETHUSDTUSDT_4h']
    week2 = ['ENAUSDTUSDT_15m', 'AVAXUSDTUSDT_30m']
    assert record_team(week1, 'master_runner', path, now=_t('2026-10-14 22:30:00'))
    assert not record_team(list(reversed(week1)), 'master_runner', path, now=_t('2026-10-14 22:45:00'))
    assert record_team(week2, 'master_runner', path, now=_t('2026-10-19 15:15:00'))
    assert len(load_history(path)) == 2

    assert team_at('2026-10-14 22:00', path) is None
    assert set(team_at('2026-10-18 20:00', path)) == set(week1)
    assert set(team_at('2026-10-19 15:15', path)) == set(week2)
    assert set(team_at('2026-10-20 14:00+00:00', path)) == set(week2)


def test_config_change_creates_new_entry(tmp_path, monkeypatch):
    import stbot.utils.team_history as th
    cfg_dir = tmp_path / 'configs'
    cfg_dir.mkdir()
    (cfg_dir / 'config_ENAUSDTUSDT_15m.json').write_text('{"a": 1}')
    monkeypatch.setattr(th, 'CONFIGS_DIR', str(cfg_dir))
    path = str(tmp_path / 'h.jsonl')
    assert record_team(['ENAUSDTUSDT_15m'], 'master_runner', path)
    (cfg_dir / 'config_ENAUSDTUSDT_15m.json').write_text('{"a": 2}')
    assert record_team(['ENAUSDTUSDT_15m'], 'master_runner', path)


def test_weekly_selection_keeps_team_is_recorded(tmp_path):
    path = str(tmp_path / 'h.jsonl')
    team = ['ENAUSDTUSDT_15m']
    assert record_team(team, 'master_runner', path, now=_t('2026-10-19 15:15:00'))
    assert record_team(team, 'wochenauswahl: beibehalten', path, now=_t('2026-10-26 15:00:00'), force=True)
    assert not record_team(team, 'master_runner', path, now=_t('2026-10-26 15:15:00'))
    assert [e['source'] for e in load_history(path)] == ['master_runner', 'wochenauswahl: beibehalten']


def test_seed_initialises_missing_history(tmp_path, monkeypatch):
    import stbot.utils.team_history as th
    seed = tmp_path / 'seed.jsonl'
    seed.write_text('{"valid_from": "2026-10-14T22:20:00+00:00", "source": "nachgetragen", '
                    '"team": {"ENAUSDTUSDT_15m": null}}\n', encoding='utf-8')
    hist = str(tmp_path / 'db' / 'team_history.jsonl')
    monkeypatch.setattr(th, 'SEED_FILE', str(seed))
    monkeypatch.setattr(th, 'HISTORY_FILE', hist)
    assert [e['source'] for e in th.load_history(hist)] == ['nachgetragen']
    assert th.record_team(['ENAUSDTUSDT_15m'], 'master_runner', hist)  # echte Pruefsumme ergaenzt
    assert len(th.load_history(hist)) == 2
