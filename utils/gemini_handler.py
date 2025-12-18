# utils/gemini_handler.py
# Mock-Implementierung für Tests – funktioniert mit test_workflow.py

class GeminiModel:
    def __init__(self, api_key=None):
        self.api_key = api_key

    def get_trading_decision(self, df, symbol):
        # In Tests wird Mock verwendet → hier nur Fallback
        return {
            'aktion': 'KAUFEN',
            'stop_loss': df['close'].iloc[-1] * 0.99,
            'take_profit': df['close'].iloc[-1] * 1.04
        }
