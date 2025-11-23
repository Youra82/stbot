# /root/utbot2/tests/test_structure.py
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
    assert os.path.isdir(os.path.join(PROJECT_ROOT, 'src', 'utbot2')), "Das 'src/utbot2'-Verzeichnis fehlt."
    assert os.path.isdir(os.path.join(PROJECT_ROOT, 'src', 'utbot2', 'strategy')), "Das 'src/utbot2/strategy'-Verzeichnis fehlt."
    assert os.path.isdir(os.path.join(PROJECT_ROOT, 'src', 'utbot2', 'analysis')), "Das 'src/utbot2/analysis'-Verzeichnis fehlt."
    assert os.path.isdir(os.path.join(PROJECT_ROOT, 'src', 'utbot2', 'utils')), "Das 'src/utbot2/utils'-Verzeichnis fehlt."


def test_core_script_imports():
    """
    Stellt sicher, dass die wichtigsten Funktionen aus den Kernmodulen importiert werden können.
    Dies ist ein schneller Check, ob die grundlegende Code-Struktur intakt ist.
    """
    try:
        # Importiere Kernkomponenten von UtBot2
        from utbot2.utils.trade_manager import housekeeper_routine, check_and_open_new_position, full_trade_cycle
        from utbot2.utils.exchange import Exchange
        
        # KORREKTUR: IchimokuEngine statt SMCEngine
        from utbot2.strategy.ichimoku_engine import IchimokuEngine
        from utbot2.strategy.trade_logic import get_titan_signal
        
        # KORREKTUR: run_backtest statt run_smc_backtest
        from utbot2.analysis.backtester import run_backtest
        
        # Importiere 'main' aus dem optimizer und gib ihr einen Alias
        from utbot2.analysis.optimizer import main as optimizer_main
        from utbot2.analysis.portfolio_optimizer import run_portfolio_optimizer

    except ImportError as e:
        pytest.fail(f"Kritischer Import-Fehler. Die Code-Struktur scheint defekt zu sein. Fehler: {e}")
