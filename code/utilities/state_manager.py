# code/utilities/state_manager.py
import sqlite3
import os
import json

class StateManager:
    def __init__(self, db_path):
        self.db_path = db_path
        self._initialize_db()

    def _initialize_db(self):
        """Stellt sicher, dass die Datenbank und die Tabelle existieren."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        ''')
        # --- ERWEITERT: Initialzustand mit peak_pnl_pct ---
        cursor.execute("INSERT OR IGNORE INTO state (key, value) VALUES (?, ?)", 
                       ('trade_status', json.dumps({
                           "status": "ok_to_trade", 
                           "last_side": None, 
                           "stop_loss_ids": [],
                           "peak_pnl_pct": 0.0  # Höchster erreichter PnL in % für den aktuellen Trade
                       })))
        conn.commit()
        conn.close()

    def get_state(self):
        """Liest den aktuellen Handelsstatus aus der Datenbank."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM state WHERE key = 'trade_status'")
        result = cursor.fetchone()
        conn.close()
        if result:
            state_data = json.loads(result[0])
            # Stelle Kompatibilität mit altem Zustand sicher
            state_data.setdefault('peak_pnl_pct', 0.0)
            return state_data
        return None

    def set_state(self, status, last_side=None, stop_loss_ids=None, peak_pnl_pct=None):
        """
        Aktualisiert den Handelsstatus in der Datenbank.
        """
        current_state = self.get_state()

        if stop_loss_ids is None:
            stop_loss_ids = current_state.get('stop_loss_ids', [])
        
        # --- NEU: Logik zur Handhabung von peak_pnl_pct ---
        # Wenn ein neuer Status gesetzt wird, der nicht "in_trade" ist, wird peak_pnl_pct zurückgesetzt.
        if status != "in_trade":
            final_peak_pnl_pct = 0.0
        # Wenn peak_pnl_pct explizit übergeben wird, nimm diesen Wert.
        elif peak_pnl_pct is not None:
            final_peak_pnl_pct = peak_pnl_pct
        # Ansonsten behalte den alten Wert bei.
        else:
            final_peak_pnl_pct = current_state.get('peak_pnl_pct', 0.0)

        new_state = {
            "status": status,
            "last_side": last_side,
            "stop_loss_ids": stop_loss_ids,
            "peak_pnl_pct": final_peak_pnl_pct
        }
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("UPDATE state SET value = ? WHERE key = 'trade_status'", 
                       (json.dumps(new_state),))
        conn.commit()
        conn.close()
