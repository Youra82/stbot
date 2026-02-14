#!/usr/bin/env python3
"""
Interactive Status (legacy) - unterstützt mehrere Bot-Namen
Zeigt Candlestick-Chart mit EMAs, Bollinger Bands und simulierten Trades
"""

import os
import sys
import json
from datetime import datetime, timedelta, timezone
import logging

import pandas as pd
import numpy as np
import plotly.graph_objects as go
import ta

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

def setup_logging():
    logger = logging.getLogger('interactive_status')
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        logger.addHandler(ch)
    return logger

logger = setup_logging()

def detect_bot_name():
    cwd = os.getcwd()
    for bot_name in ['pbot', 'kbot', 'stbot', 'utbot2', 'titanbot']:
        if bot_name in cwd:
            return bot_name
    return 'kbot'  # Default

BOT_NAME = detect_bot_name()

# Rest unverändert (legacy helper für interaktive Charts)
