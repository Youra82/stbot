# src/stbot/analysis/evaluator.py (Angepasst für STBot EMA/MACD/RSI Strategie)
import pandas as pd
import numpy as np
import ta
import sys
import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))
# --- NEUER IMPORT ---
from stbot.strategy.indicators import calculate_macd, calculate_rsi 
# --- ENDE NEUER IMPORT ---

def evaluate_dataset(data: pd.DataFrame, timeframe: str):
    """
    Bewertet einen Datensatz für die Optimierung basierend auf Phasenverteilung
    und Indikator-Event-Dichte.
    """
    if data.empty or len(data) < 200:
        # ... (Fehlerbehandlung bleibt gleich)
        return {
            "score": 0,
            "justification": [
                "- Phasen-Verteilung (0/4): Nicht bewertbar. Zu wenig Daten.",
                "- Handelbarkeit (0/4): Nicht bewertbar. Zu wenig Daten.",
                "- Datenmenge (0/2): Mangelhaft. Weniger als 200 Kerzen."
            ],
            "phase_dist": {}
        }

    # Sicherstellen, dass die Spaltennamen für die Berechnung korrekt sind
    data = data.rename(columns={'close': 'Close', 'volume': 'Volume'}).copy()

    # --- Metrik 1: Phasen-Verteilung (max. 4 Punkte) --- (Unverändert)
    data['ema_50'] = ta.trend.ema_indicator(data['Close'], window=50)
    data['ema_200'] = ta.trend.ema_indicator(data['Close'], window=200)
    data.dropna(inplace=True)

    conditions = [
        (data['Close'] > data['ema_50']) & (data['ema_50'] > data['ema_200']),
        (data['Close'] < data['ema_50']) & (data['ema_50'] < data['ema_200'])
    ]
    choices = ['Aufwärts', 'Abwärts']
    data['phase'] = np.select(conditions, choices, default='Seitwärts')

    phase_dist = data['phase'].value_counts(normalize=True)
    max_phase_pct = phase_dist.max()

    if max_phase_pct > 0.8: score1 = 0
    elif max_phase_pct > 0.7: score1 = 1
    elif max_phase_pct > 0.6: score1 = 2
    elif max_phase_pct > 0.5: score1 = 3
    else: score1 = 4

    dist_text = ", ".join([f"{name}: {pct:.0%}" for name, pct in phase_dist.items()])
    just1 = f"- Phasen-Verteilung ({score1}/4): {'Exzellent' if score1==4 else 'Gut' if score1==3 else 'Mäßig' if score1==2 else 'Einseitig'}. ({dist_text})"

    # --- Metrik 2: Handelbarkeit / Indikator-Event-Dichte (max. 4 Punkte) --- (ANGEPASST)
    try:
        # Wir zählen MACD-Crossover-Events anstelle von SMC-Events
        data['MACD'], data['MACD_Signal'], data['MACD_Hist'] = calculate_macd(data['Close'])
        
        # MACD-Crossover: Ein Trade-Signal tritt auf, wenn MACD_Hist das Vorzeichen wechselt (z.B. von positiv zu negativ)
        data['Cross_Signal'] = np.sign(data['MACD_Hist'])
        data['Cross_Change'] = data['Cross_Signal'].diff().fillna(0).apply(lambda x: 1 if abs(x) == 2 else 0)
        
        event_count = data['Cross_Change'].sum()

        # Berechne Events pro 1000 Kerzen
        event_density = (event_count / len(data)) * 1000 if len(data) > 0 else 0
    except Exception:
        event_density = 0

    if event_density < 5: score2 = 0  # Zu wenige Crossover
    elif event_density < 10: score2 = 1
    elif event_density < 20: score2 = 2
    elif event_density < 40: score2 = 3
    else: score2 = 4
    just2 = f"- Handelbarkeit ({score2}/4): {'Exzellent' if score2==4 else 'Gut' if score2==3 else 'Mäßig' if score2==2 else 'Gering' if score2==1 else 'Sehr Gering'}. {event_density:.1f} Events/1000 Kerzen."

    # --- Metrik 3: Datenmenge (max. 2 Punkte) --- (Unverändert)
    num_candles = len(data)
    if num_candles < 2000: score3 = 0
    elif num_candles < 5000: score3 = 1
    else: score3 = 2
    just3 = f"- Datenmenge ({score3}/2): {'Exzellent' if score3==2 else 'Ausreichend' if score3==1 else 'Gering'}. {num_candles:,} Kerzen."

    # --- Gesamtergebnis ---
    total_score = score1 + score2 + score3
    return {
        "score": total_score,
        "justification": [just1, just2, just3],
        "phase_dist": phase_dist.to_dict()
    }
