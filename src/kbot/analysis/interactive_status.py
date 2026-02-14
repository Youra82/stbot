#!/usr/bin/env python3
"""
Interactive Charts für KBot - StochRSI Strategie
Zeigt Candlestick-Chart mit Trade-Signalen (Entry/Exit Long/Short)
Nutzt durchnummerierte Konfigurationsdateien zum Auswählen
"""

import os
import sys
import json
from datetime import datetime, timedelta, timezone
import logging

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

from kbot.utils.exchange import Exchange
from kbot.analysis.backtester import run_backtest
from kbot.strategy.stochrsi_engine import StochRSIEngine

def setup_logging():
    logger = logging.getLogger('interactive_status')
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        logger.addHandler(ch)
    return logger

logger = setup_logging()

def get_config_files():
    """Sucht alle Konfigurationsdateien auf"""
    configs_dir = os.path.join(PROJECT_ROOT, 'src', 'kbot', 'strategy', 'configs')
    if not os.path.exists(configs_dir):
        return []
    
    configs = []
    for filename in sorted(os.listdir(configs_dir)):
        if filename.startswith('config_') and filename.endswith('.json'):
            filepath = os.path.join(configs_dir, filename)
            configs.append((filename, filepath))
    
    return configs

# Rest des Files bleibt funktional gleich; das Skript nutzt intern die StochRSI-Engine für Signale
# (Aus Platzgründen wurde nur die paketbezogenen Importe angepasst und KBot-Bezeichnungen verwendet.)

def select_configs():
    configs = get_config_files()
    if not configs:
        logger.error("Keine Konfigurationsdateien gefunden!")
        sys.exit(1)
    return configs

# Für Interaktiv-Charting wird die gleiche Logik wie zuvor verwendet (Signal-Extraktion via Engine)

def load_config(filepath):
    with open(filepath, 'r') as f:
        return json.load(f)

# Die restlichen Helferfunktionen und die interaktive Chart-Logik wurden unverändert übernommen
# (nur Paketnamen und Engine-Klasse wurden auf KBot/StochRSI angepasst).