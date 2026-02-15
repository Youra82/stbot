import pytest
from kbot.analysis.interactive_status import choose_telegram_config


def test_choose_telegram_top_level():
    secrets = {'telegram': {'bot_token': 'x:1', 'chat_id': '123'}}
    cfg = choose_telegram_config(secrets)
    assert cfg['bot_token'] == 'x:1' and cfg['chat_id'] == '123'


def test_choose_telegram_nested_kbot():
    secrets = {'kbot': [{'telegram': {'bot_token': 'y:2', 'chat_id': '456'}}]}
    cfg = choose_telegram_config(secrets)
    assert cfg['bot_token'] == 'y:2' and cfg['chat_id'] == '456'


def test_choose_telegram_missing():
    secrets = {'kbot': [{'apiKey': 'a'}], 'telegram': {}}
    cfg = choose_telegram_config(secrets)
    assert cfg == {}
