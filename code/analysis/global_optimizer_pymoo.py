# code/analysis/global_optimizer_pymoo.py

# ... (Imports und Helper-Funktionen bleiben gleich) ...

class StochRSIOptimizationProblem(Problem):
    def __init__(self, trend_filter_enabled, sideways_filter_enabled, **kwargs):
        self.trend_filter_enabled = trend_filter_enabled
        self.sideways_filter_enabled = sideways_filter_enabled
        
        # --- GEÄNDERT: n_var von 9 auf 8 reduziert ---
        super().__init__(n_var=8, n_obj=2, n_constr=0,
                         xl=[5, 2, 2, 5, 0.1, 5, 1.0, 2],
                         xu=[50, 10, 10, 50, 1.0, 50, 5.0, 15], **kwargs)

    def _evaluate(self, x, out, *args, **kwargs):
        results = []
        for individual in x:
            params = {
                'stoch_rsi_period': int(round(individual[0])),
                # 'stoch_period' entfernt
                'stoch_k': int(round(individual[1])),
                'stoch_d': int(round(individual[2])),
                'swing_lookback': int(round(individual[3])),
                'sl_buffer_pct': round(individual[4], 2),
                'base_leverage': int(round(individual[5])),
                'target_atr_pct': round(individual[6], 2),
                'max_leverage': 50.0, 'start_capital': START_CAPITAL, 'balance_fraction_pct': 2.0,
                'trend_filter': { 'enabled': self.trend_filter_enabled, 'period': 200 },
                'sideways_filter': { 'enabled': self.sideways_filter_enabled, 'lookback': 50, 'max_crosses': int(round(individual[7])) }
            }
            # ... (Rest der _evaluate Funktion bleibt gleich) ...

# In der main Funktion, beim Erstellen des param_dict:
                    param_dict = {
                        # ...
                        'params': {
                            'stoch_rsi_period': int(round(params[0])),
                            # 'stoch_period' entfernt
                            'stoch_k': int(round(params[1])),
                            'stoch_d': int(round(params[2])),
                            'swing_lookback': int(round(params[3])),
                            'sl_buffer_pct': round(params[4], 2),
                            'base_leverage': int(round(params[5])),
                            'target_atr_pct': round(params[6], 2),
                            'trend_filter': { 'enabled': is_trend_filter_enabled, 'period': 200 },
                            'sideways_filter': { 'enabled': is_sideways_filter_enabled, 'lookback': 50, 'max_crosses': int(round(params[7])) }
                        }
                    }
                    all_champions.append(param_dict)
# ... (Rest der Datei bleibt gleich) ...
