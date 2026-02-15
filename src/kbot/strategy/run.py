# src/kbot/strategy/run.py
# =============================================================================
# KBot: Stoch‑RSI Trading Bot
# =============================================================================

import os
import sys
import json
import logging
from logging.handlers import RotatingFileHandler
import argparse

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from kbot.utils.exchange import Exchange
from kbot.utils.telegram import send_message
from kbot.utils.trade_manager import full_trade_cycle


def setup_logging(symbol: str, timeframe: str) -> logging.Logger:
    """Richtet Logging für die Strategie ein."""
    safe_filename = f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"
    log_dir = os.path.join(PROJECT_ROOT, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f'kbot_{safe_filename}.log')
    
    logger = logging.getLogger(f'kbot_{safe_filename}')
    logger.setLevel(logging.INFO)
    
    if not logger.handlers:
        # File Handler mit Rotation
        fh = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3)
        fh_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        fh.setFormatter(fh_formatter)
        logger.addHandler(fh)

        # Console Handler
        ch = logging.StreamHandler()
        ch_formatter = logging.Formatter('%(levelname)s: %(message)s')
        ch.setFormatter(ch_formatter)
        logger.addHandler(ch)
        
    return logger


def load_config(symbol: str, timeframe: str) -> dict:
    """Lädt die Konfiguration für das Symbol/Timeframe."""
    configs_dir = os.path.join(PROJECT_ROOT, 'src', 'kbot', 'strategy', 'configs')
    safe_filename = f"{symbol.replace('/', '').replace(':', '')}_{timeframe}"
    config_filename = f"config_{safe_filename}.json"
    config_path = os.path.join(configs_dir, config_filename)
    
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Konfiguration nicht gefunden: {config_filename}")
        
    with open(config_path, 'r') as f:
        return json.load(f)


def run_for_account(account: dict, telegram_config: dict, params: dict, 
                    logger: logging.Logger):
    """Führt den Bot für einen Account aus."""
    account_name = account.get('name', 'Standard-Account')
    symbol = params['market']['symbol']
    timeframe = params['market']['timeframe']
    
    print(f"\n{'=' * 60}")
    print(f"🤖 KBot Stoch‑RSI")
    print(f"   Symbol: {symbol} | Timeframe: {timeframe}")
    print(f"   Account: {account_name}")
    print(f"{'=' * 60}")
    
    logger.info(f"Starte KBot für {symbol} ({timeframe}) auf Account '{account_name}'")
    
    try:
        exchange = Exchange(account)
        full_trade_cycle(exchange, params, telegram_config, logger)
    except Exception as e:
        logger.error(f"Fehler bei der Ausführung: {e}", exc_info=True)


def main():
    parser = argparse.ArgumentParser(description="KBot Stoch‑RSI Trading Bot")
    parser.add_argument('--symbol', required=True, type=str, 
                       help='Trading Symbol (z.B. BTC/USDT:USDT)')
    parser.add_argument('--timeframe', required=True, type=str, 
                       help='Timeframe (z.B. 1h, 4h, 1d)')
    parser.add_argument('--live', action='store_true',
                       help='Live-Modus (führt echte Trades aus)')
    args = parser.parse_args()

    symbol = args.symbol
    timeframe = args.timeframe
    
    logger = setup_logging(symbol, timeframe)

    try:
        # Konfiguration laden
        params = load_config(symbol, timeframe)
        
        # Secrets laden
        secret_path = os.path.join(PROJECT_ROOT, 'secret.json')
        with open(secret_path, "r") as f:
            secrets = json.load(f)
        
        accounts = secrets.get('kbot', [])
        telegram_config = secrets.get('telegram', {})
        
        if not accounts:
            logger.error("Keine Accounts in secret.json für 'kbot' konfiguriert!")
            sys.exit(1)
        
        # Für jeden Account ausführen
        for account in accounts:
            try:
                run_for_account(account, telegram_config, params, logger)
            except Exception as e:
                logger.error(f"Fehler bei Account {account.get('name', 'unknown')}: {e}")
                continue
                
    except FileNotFoundError as e:
        logger.critical(f"Konfigurationsfehler: {e}")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"Kritischer Fehler: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
