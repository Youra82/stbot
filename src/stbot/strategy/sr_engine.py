# src/stbot/strategy/sr_engine.py
import pandas as pd
import numpy as np
import math
import warnings

# Unterdrücke spezifische Pandas FutureWarnings für sauberen Output
warnings.simplefilter(action='ignore', category=FutureWarning)

class SREngine:
    """
    Python Implementierung von 'Support Resistance - Dynamic v2'.
    Berechnet dynamische S/R Zonen basierend auf Pivot-Clustern und erkennt Breakouts.
    """
    def __init__(self, settings: dict):
        # Parameter aus settings.json / Optimizer
        self.prd = settings.get('pivot_period', 10)
        self.ppsrc = settings.get('source', 'High/Low') # 'High/Low' oder 'Close/Open'
        self.maxnumpp = settings.get('max_pivots', 20)
        self.channel_w_pct = settings.get('channel_width_pct', 10)
        self.maxnumsr = settings.get('max_sr_levels', 5)
        self.min_strength = settings.get('min_strength', 2)

    def process_dataframe(self, df: pd.DataFrame):
        """
        Verarbeitet den DataFrame und fügt die Spalte 'sr_signal' hinzu.
        1 = Resistance Break (Buy), -1 = Support Break (Sell), 0 = Neutral
        """
        if df.empty: return df
        df = df.copy()
        
        # 1. Basis-Daten bestimmen
        if self.ppsrc == 'High/Low':
            src1 = df['high']
            src2 = df['low']
        else:
            src1 = df[['open', 'close']].max(axis=1)
            src2 = df[['open', 'close']].min(axis=1)

        # 2. Pivot Punkte finden
        window = 2 * self.prd + 1
        
        # Rolling Max/Min zentriert berechnen
        max_roll = src1.rolling(window=window, center=True).max()
        min_roll = src2.rolling(window=window, center=True).min()
        
        is_pivot_high = (src1 == max_roll)
        is_pivot_low = (src2 == min_roll)
        
        # Verschieben um 'prd' in die Zukunft (Confirmation)
        pivot_high_confirmed = is_pivot_high.shift(self.prd).fillna(False).astype(bool)
        pivot_low_confirmed = is_pivot_low.shift(self.prd).fillna(False).astype(bool)
        
        pivot_val_high = src1.shift(self.prd)
        pivot_val_low = src2.shift(self.prd)

        # 3. Dynamische Kanal-Breite berechnen (ATR-basiert)
        # Nutze ATR statt fixem Prozentsatz für bessere Anpassung an Volatilität
        if 'atr' in df.columns:
            # ATR-basierte Zonen (bevorzugt)
            cwidths = df['atr'] * (self.channel_w_pct / 10.0)  # channel_w_pct als Multiplikator
        else:
            # Fallback: Prozent-basiert (wie vorher)
            highest_300 = df['high'].rolling(300, min_periods=50).max()
            lowest_300 = df['low'].rolling(300, min_periods=50).min()
            cwidths = (highest_300 - lowest_300) * self.channel_w_pct / 100
        
        # 4. Iteration durch die Kerzen
        closes = df['close'].values
        idx_pivot_h = pivot_high_confirmed.values
        idx_pivot_l = pivot_low_confirmed.values
        val_pivot_h = pivot_val_high.values
        val_pivot_l = pivot_val_low.values
        arr_cwidth = cwidths.fillna(0).values
        
        signals = np.zeros(len(df), dtype=int)
        pivotvals = [] 
        
        for i in range(len(df)):
            # A. Pivots aktualisieren
            new_val = None
            if idx_pivot_h[i]: 
                new_val = val_pivot_h[i]
            elif idx_pivot_l[i]: 
                new_val = val_pivot_l[i]
            
            if new_val is not None and not np.isnan(new_val):
                pivotvals.insert(0, new_val)
                if len(pivotvals) > self.maxnumpp:
                    pivotvals.pop()
            
            if not pivotvals: continue
                
            current_cwidth = arr_cwidth[i]
            # Fallback, falls Berechnung noch nicht möglich war, aber min_periods erfüllt ist
            if current_cwidth == 0 and i > 50:
                 # Kleiner Standardwert als Fallback
                 current_cwidth = closes[i] * 0.01 

            # B. S/R Zonen berechnen
            temp_zones = []
            
            for p_ref in pivotvals:
                lo = p_ref
                hi = p_ref
                strength = 0
                
                for p_comp in pivotvals:
                    wdth = 0.0
                    if p_comp <= lo: wdth = hi - p_comp
                    else: wdth = p_comp - lo
                    
                    if wdth <= current_cwidth:
                        if p_comp <= hi: lo = min(lo, p_comp)
                        else: hi = max(hi, p_comp)
                        strength += 1
                
                temp_zones.append({'hi': hi, 'lo': lo, 'strength': strength})
            
            temp_zones.sort(key=lambda x: x['strength'], reverse=True)
            
            final_zones = []
            for z in temp_zones:
                if z['strength'] < self.min_strength: continue
                
                is_overlapping = False
                for existing in final_zones:
                    if (existing['hi'] >= z['lo'] and existing['hi'] <= z['hi']) or \
                       (existing['lo'] >= z['lo'] and existing['lo'] <= z['hi']) or \
                       (z['hi'] >= existing['lo'] and z['hi'] <= existing['hi']):
                        is_overlapping = True
                        break
                
                if not is_overlapping:
                    final_zones.append(z)
                    if len(final_zones) >= self.maxnumsr:
                        break
            
            # C. Breakout Check
            if i == 0: continue
            
            curr_close = closes[i]
            prev_close = closes[i-1]
            
            for z in final_zones:
                mid = (z['hi'] + z['lo']) / 2
                
                # Breakout-Bestätigung: Preis muss signifikant über/unter Zone schließen
                # 0.2% Threshold gegen Whipsaws
                breakout_threshold = 0.002
                
                if prev_close <= mid and curr_close > mid * (1 + breakout_threshold):
                    signals[i] = 1
                    break 
                
                if prev_close >= mid and curr_close < mid * (1 - breakout_threshold):
                    signals[i] = -1
                    break
        
        df['sr_signal'] = signals
        return df
