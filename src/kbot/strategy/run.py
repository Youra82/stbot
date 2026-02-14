# /root/kbot/src/kbot/strategy/run.py
import os
import sys
import json
import logging
from logging.handlers import RotatingFileHandler
import time
import argparse
import ccxt

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from kbot.utils.exchange import Exchange
from kbot.utils.telegram import send_message
from kbot.utils.trade_manager import full_trade_cycle
from kbot.utils.timeframe_utils import determine_htf


def setup_logging(symbol, timeframe):
    safe_filename = f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"
    log_dir = os.path.join(PROJECT_ROOT, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f'kbot_{safe_filename}.log')

    logger = logging.getLogger(f'kbot_{safe_filename}')
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        fh = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
        fh_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        fh.setFormatter(fh_formatter)
        logger.addHandler(fh)

        ch = logging.StreamHandler()
        ch_formatter = logging.Formatter(f'%(asctime)s [%(levelname)s] {symbol}|{timeframe}: %(message)s', datefmt='%H:%M:%S')
        ch.setFormatter(ch_formatter)
        logger.addHandler(ch)

        logger.propagate = False

    return logger


def load_config(symbol, timeframe, use_macd_filter):
    configs_dir = os.path.join(PROJECT_ROOT, 'src', 'kbot', 'strategy', 'configs')
    safe_filename_base = f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"

    suffix = "_macd" if use_macd_filter else ""
    config_filename = f"config_{safe_filename_base}{suffix}.json"
    config_path = os.path.join(configs_dir, config_filename)

    if not os.path.exists(config_path):
        config_filename_fallback = f"config_{safe_filename_base}.json"
        config_path_fallback = os.path.join(configs_dir, config_filename_fallback)
        if os.path.exists(config_path_fallback):
            config_path = config_path_fallback
            config_filename = config_filename_fallback
        else:
            raise FileNotFoundError(f"Konfigurationsdatei '{config_filename}' oder Fallbacks nicht gefunden.")

    with open(config_path, 'r') as f:
        config = json.load(f)

    config['market']['htf'] = determine_htf(config['market']['timeframe'])
    return config


def run_for_account(account, telegram_config, params, model, scaler, logger):
    try:
        account_name = account.get('name', 'Standard-Account')
        symbol = params['market']['symbol']
        timeframe = params['market']['timeframe']
        htf = params['market']['htf']
        
        logger.info(f"--- Starte KBot für {symbol} ({timeframe}) mit MTF-Bias von {htf} ---")
        
        exchange = Exchange(account)

        if not exchange.markets:
            logger.critical("Exchange konnte nicht initialisiert werden (Märkte nicht geladen). Breche Zyklus ab.")
            return

        full_trade_cycle(exchange, None, None, params, telegram_config, logger)

    except Exception as e:
        symbol_f = params.get('market', {}).get('symbol', 'Unbekannt')
        tf_f = params.get('market', {}).get('timeframe', 'N/A')
        logger.critical(f"!!! KRITISCHER FEHLER im Hauptzyklus für {symbol_f} ({tf_f}) !!!")
        logger.critical(f"Fehlerdetails: {e}", exc_info=True)
        try:
            error_message = f"🚨 *Kritischer Fehler* in KBot für *{symbol_f} ({tf_f})*:\n\n`{e}`\n\nBot-Instanz könnte instabil sein."
            send_message(
                telegram_config.get('bot_token'),
                telegram_config.get('chat_id'),
                error_message
            )
        except Exception as tel_e:
            logger.error(f"Konnte keine Telegram-Fehlermeldung senden: {tel_e}")


def main():
    parser = argparse.ArgumentParser(description="KBot StochRSI Trading-Skript")
    parser.add_argument('--symbol', required=True, type=str)
    parser.add_argument('--timeframe', required=True, type=str)
    parser.add_argument('--use_macd', required=True, type=str)
    args = parser.parse_args()

    symbol, timeframe = args.symbol, args.timeframe
    use_macd = args.use_macd.lower() == 'true'

    logger = setup_logging(symbol, timeframe)

    try:
        params = load_config(symbol, timeframe, use_macd)
        with open(os.path.join(PROJECT_ROOT, 'secret.json'), "r") as f:
            secrets = json.load(f)

        accounts_to_run = secrets.get('kbot', [])
        if not accounts_to_run:
            logger.critical("Keine Account-Konfigurationen unter 'kbot' in secret.json gefunden!")
            sys.exit(1)

        telegram_config = secrets.get('telegram', {})

    except FileNotFoundError as e:
        logger.critical(f"Kritischer Initialisierungs-Fehler: Datei nicht gefunden - {e}", exc_info=True)
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.critical(f"Kritischer Initialisierungs-Fehler: JSON-Fehler in Konfigurationsdatei - {e}", exc_info=True)
        sys.exit(1)
    except Exception as e:
        logger.critical(f"Kritischer Initialisierungs-Fehler: {e}", exc_info=True)
        sys.exit(1)

    if not isinstance(accounts_to_run, list):
        logger.critical("Fehler: 'kbot'-Eintrag in secret.json ist keine Liste von Accounts.")
        sys.exit(1)

    for account in accounts_to_run:
        run_for_account(account, telegram_config, params, None, None, logger)

    logger.info(f">>> KBot-Lauf für {symbol} ({timeframe}) abgeschlossen <<<\n")

if __name__ == "__main__":
    main()