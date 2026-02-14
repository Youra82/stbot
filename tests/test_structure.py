# /root/stbot/tests/test_structure.py
import os
import sys
import pytest

# Füge das Projektverzeichnis zum Python-Pfad hinzu, damit Imports funktionieren
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

def test_project_structure():
    """Stellt sicher, dass alle erwarteten Hauptverzeichnisse existieren."""
    assert os.path.isdir(os.path.join(PROJECT_ROOT, 'src')), "Das 'src'-Verzeichnis fehlt."
    assert os.path.isdir(os.path.join(PROJECT_ROOT, 'artifacts')), "Das 'artifacts'-Verzeichnis fehlt."
    assert os.path.isdir(os.path.join(PROJECT_ROOT, 'tests')), "Das 'tests'-Verzeichnis fehlt."
    assert os.path.isdir(os.path.join(PROJECT_ROOT, 'src', 'kbot')), "Das 'src/kbot'-Verzeichnis fehlt."
    assert os.path.isdir(os.path.join(PROJECT_ROOT, 'src', 'kbot', 'strategy')), "Das 'src/kbot/strategy'-Verzeichnis fehlt."
    assert os.path.isdir(os.path.join(PROJECT_ROOT, 'src', 'kbot', 'analysis')), "Das 'src/kbot/analysis'-Verzeichnis fehlt."
    assert os.path.isdir(os.path.join(PROJECT_ROOT, 'src', 'kbot', 'utils')), "Das 'src/kbot/utils'-Verzeichnis fehlt."


def test_core_script_imports():
    """
    Stellt sicher, dass die wichtigsten Funktionen aus den Kernmodulen importiert werden können.
    Dies ist ein schneller Check, ob die grundlegende Code-Struktur intakt ist.
    """
    try:
        # Importiere Kernkomponenten von KBot
        from kbot.utils.trade_manager import housekeeper_routine, check_and_open_new_position, full_trade_cycle
        from kbot.utils.exchange import Exchange

        # StochRSI-Engine + Trade-Logic
        from kbot.strategy.stochrsi_engine import StochRSIEngine
        from kbot.strategy.trade_logic import get_titan_signal

        # Backtester und Optimizer Imports
        from kbot.analysis.backtester import run_backtest
        from kbot.analysis.optimizer import main as optimizer_main
        from kbot.analysis.portfolio_optimizer import run_portfolio_optimizer

    except ImportError as e:
        pytest.fail(f"Kritischer Import-Fehler. Die Code-Struktur scheint defekt zu sein. Fehler: {e}")
