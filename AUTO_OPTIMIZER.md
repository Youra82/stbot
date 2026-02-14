# KBot Auto-Optimizer Scheduler

Automatische Optimierung der StochRSI-Strategiekonfigurationen mit Zeitplan-Steuerung und Telegram-Benachrichtigungen.

## 📋 Übersicht

Der Auto-Optimizer führt die Optuna-basierte Parameteroptimierung automatisch nach einem konfigurierbaren Zeitplan aus. Er kann:

- **Automatisch** die aktiven Coins und Timeframes aus `active_strategies` übernehmen
- **Geplant** an einem bestimmten Wochentag und Uhrzeit starten
- **Telegram-Benachrichtigungen** bei Erfolg oder Fehler senden
- Als **Daemon** im Hintergrund laufen

---

## 🚀 Schnellstart

### Status prüfen
```bash
python3 auto_optimizer_scheduler.py --check-only
```
Zeigt an, ob eine Optimierung fällig ist und wann die letzte Ausführung war.

### Sofort optimieren
```bash
python3 auto_optimizer_scheduler.py --force
```
Startet die Optimierung sofort, ignoriert den Zeitplan.

### Als Daemon laufen
```bash
python3 auto_optimizer_scheduler.py --daemon
```
Läuft kontinuierlich und prüft alle 60 Sekunden, ob der geplante Zeitpunkt erreicht ist.

```bash
python3 auto_optimizer_scheduler.py --daemon --interval 300
```
Prüft alle 5 Minuten (300 Sekunden).

---

## ⚙️ Konfiguration (settings.json)

```json
{
    "optimization_settings": {
        "enabled": false,
        "schedule": {
            "day_of_week": 0,
            "hour": 3,
            "minute": 0,
            "interval_days": 7
        },
        "symbols_to_optimize": "auto",
        "timeframes_to_optimize": "auto",
        "lookback_days": 365,
        "start_capital": 1000,
        "cpu_cores": -1,
        "num_trials": 200,
        "constraints": {
            "max_drawdown_pct": 30,
            "min_win_rate_pct": 55,
            "min_pnl_pct": 0
        },
        "auto_clear_cache_days": 0,
        "send_telegram_on_completion": true
    }
}
```

### Parameter-Erklärung

| Parameter | Typ | Beschreibung |
|-----------|-----|--------------|
| `enabled` | bool | `true` = Automatische Optimierung aktiv |
| `schedule.day_of_week` | int | Wochentag (0=Montag, 1=Dienstag, ..., 6=Sonntag) |
| `schedule.hour` | int | Stunde im 24h-Format (0-23) |
| `schedule.minute` | int | Minute (0-59) |
| `schedule.interval_days` | int | Mindestabstand zwischen Optimierungen in Tagen |
| `symbols_to_optimize` | "auto" / Array | `"auto"` = aus active_strategies, oder `["BTC", "ETH"]` |
| `timeframes_to_optimize` | "auto" / Array | `"auto"` = aus active_strategies, oder `["1h", "4h"]` |
| `lookback_days` | int | Anzahl Tage historischer Daten für Backtest |
| `start_capital` | float | Startkapital für Backtest-Simulation |
| `cpu_cores` | int | CPU-Kerne für Parallelisierung (-1 = alle) |
| `num_trials` | int | Anzahl Optuna-Trials pro Symbol/Timeframe |
| `constraints.max_drawdown_pct` | float | Maximaler Drawdown in % |
| `constraints.min_win_rate_pct` | float | Minimale Winrate in % |
| `constraints.min_pnl_pct` | float | Minimaler Gewinn in % |
| `auto_clear_cache_days` | int | Cache nach X Tagen leeren (0 = deaktiviert) |
| `send_telegram_on_completion` | bool | Telegram-Nachricht nach Abschluss senden |

---

## 🔄 Auto-Modus für Symbole und Timeframes

Wenn `"symbols_to_optimize": "auto"` oder `"timeframes_to_optimize": "auto"` gesetzt ist, werden die Werte automatisch aus `live_trading_settings.active_strategies` extrahiert.

**Es werden nur Strategien berücksichtigt, bei denen `"active": true` ist.**

### Beispiel

```json
"active_strategies": [
    {"symbol": "BTC/USDT:USDT", "timeframe": "4h", "active": true},
    {"symbol": "ETH/USDT:USDT", "timeframe": "1h", "active": true},
    {"symbol": "DOGE/USDT:USDT", "timeframe": "30m", "active": false}
]
```

Ergibt bei `"auto"`:
- **Symbole**: `["BTC", "ETH"]` (DOGE ist nicht aktiv)
- **Timeframes**: `["30m", "1h", "4h"]` (sortiert nach Dauer)

---

## 📱 Telegram-Benachrichtigungen

### Voraussetzung
Die Datei `secret.json` muss Telegram-Credentials enthalten:

```json
{
    "telegram": {
        "bot_token": "123456789:ABCdefGHIjklMNOpqrsTUVwxyz",
        "chat_id": "987654321"
    }
}
```

### Benachrichtigungs-Formate

**Bei Erfolg:**
```
✅ KBot Auto-Optimierung ABGESCHLOSSEN

Dauer: 45 Minuten
Symbole: BTC, ETH, SOL
Zeitfenster: 30m, 1h, 4h
Generierte Configs: 9
Trials pro Kombination: 200
Lookback: 365 Tage

Nächste Optimierung in 7 Tagen.
```

**Bei Fehler:**
```
❌ KBot Auto-Optimierung FEHLGESCHLAGEN

Dauer: 12 Minuten
Fehlercode: 1
Details in logs/scheduler.log
```

---

## 🖥️ Betriebssystem-Unterstützung

| Feature | Windows | Linux/Mac |
|---------|---------|-----------|
| `--check-only` | ✅ | ✅ |
| `--force` | ✅ | ✅ |
| `--daemon` | ✅ | ✅ |
| Bash-Skript | ❌ | ✅ |

Unter Windows wird der Python-Optimizer direkt aufgerufen.
Unter Linux/Mac wird `run_pipeline_automated.sh` verwendet.

---

## 📂 Generierte Dateien

| Datei | Beschreibung |
|-------|--------------|
| `src/stbot/strategy/configs/config_*.json` | Optimierte Strategie-Konfigurationen |
| `data/cache/.last_optimization_run` | Zeitstempel der letzten Ausführung |
| `logs/scheduler.log` | Log-Datei des Schedulers |

---

## 🔧 Beispiel-Workflows

### Wöchentliche Optimierung (Montag 3:00 Uhr)
```json
"schedule": {
    "day_of_week": 0,
    "hour": 3,
    "minute": 0,
    "interval_days": 7
}
```

### Tägliche Optimierung (jeden Tag um 4:30 Uhr)
```json
"schedule": {
    "day_of_week": 0,
    "hour": 4,
    "minute": 30,
    "interval_days": 1
}
```
*Hinweis: `day_of_week` wird bei `interval_days < 7` ignoriert.*

### Alle 3 Tage am Wochenende
```json
"schedule": {
    "day_of_week": 6,
    "hour": 2,
    "minute": 0,
    "interval_days": 3
}
```

---

## 🐛 Troubleshooting

### "Optimierung nicht fällig"
- Prüfe mit `--check-only` den genauen Grund
- Nutze `--force` um den Zeitplan zu umgehen

### "Telegram-Fehler"
- Prüfe `secret.json` auf korrekte Credentials
- Teste manuell: `python -c "from src.stbot.utils.telegram import send_message; ..."`

### Logs prüfen
```bash
cat logs/scheduler.log
```

---

## 📜 Lizenz

Teil des StBot-Projekts. Siehe [LICENSE](LICENSE).
