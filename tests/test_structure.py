# Pfad: /home/matola/stbot/tests/test_structure.py

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
    # ÄNDERUNG: titanbot durch stbot ersetzt
    assert os.path.isdir(os.path.join(PROJECT_ROOT, 'src', 'stbot')), "Das 'src/stbot'-Verzeichnis fehlt."
    assert os.path.isdir(os.path.join(PROJECT_ROOT, 'src', 'stbot', 'strategy')), "Das 'src/stbot/strategy'-Verzeichnis fehlt."
    assert os.path.isdir(os.path.join(PROJECT_ROOT, 'src', 'stbot', 'analysis')), "Das 'src/stbot/analysis'-Verzeichnis fehlt."
    assert os.path.isdir(os.path.join(PROJECT_ROOT, 'src', 'stbot', 'utils')), "Das 'src/stbot/utils'-Verzeichnis fehlt."


def test_core_script_imports():
    """
    Stellt sicher, dass die wichtigsten Funktionen aus den Kernmodulen importiert werden können.
    Dies ist ein schneller Check, ob die grundlegende Code-Struktur intakt ist.
    """
    try:
        # Importiere Kernkomponenten von STBot
        # ÄNDERUNG: titanbot durch stbot ersetzt (alle Importe)
        from stbot.utils.trade_manager import housekeeper_routine, check_and_open_new_position, full_trade_cycle
        from stbot.utils.exchange import Exchange
        # Der Import von smc_engine wird durch indicators.py ersetzt
        from stbot.strategy.indicators import STBotEngine
        from stbot.strategy.trade_logic import get_titan_signal
        from stbot.analysis.backtester import run_smc_backtest
        # Importiere 'main' aus dem optimizer und gib ihr einen Alias
        from stbot.analysis.optimizer import main as optimizer_main
        from stbot.analysis.portfolio_optimizer import run_portfolio_optimizer

    except ImportError as e:
        pytest.fail(f"Kritischer Import-Fehler. Die Code-Struktur scheint defekt zu sein. Fehler: {e}")
