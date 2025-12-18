import json
import os
import sys

def check():
    print("--- Diagnose: secret.json ---")
    file_path = 'secret.json'
    
    if not os.path.exists(file_path):
        print(f"❌ FEHLER: Datei '{file_path}' nicht gefunden!")
        return

    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
        print("✅ JSON-Syntax ist gültig.")
    except json.JSONDecodeError as e:
        print(f"❌ FEHLER: Ungültiges JSON-Format: {e}")
        return

    # Prüfe Hauptschlüssel
    accounts = []
    if 'stbot' in data:
        print("✅ Hauptschlüssel 'stbot' gefunden.")
        accounts = data['stbot']
    elif 'utbot2' in data:
        print("⚠️ WARNUNG: Hauptschlüssel 'stbot' fehlt, aber 'utbot2' gefunden (Test nutzt Fallback).")
        accounts = data['utbot2']
    else:
        print("❌ FEHLER: Weder 'stbot' noch 'utbot2' als Hauptschlüssel gefunden.")
        print(f"   Gefundene Schlüssel: {list(data.keys())}")
        return

    if not isinstance(accounts, list) or len(accounts) == 0:
        print("❌ FEHLER: Account-Liste ist leer oder kein Array [].")
        return

    # Prüfe Keys im ersten Account
    first_acc = accounts[0]
    print(f"\nPrüfe ersten Account Eintrag:")
    
    required_fields = ['apiKey', 'secret', 'password']
    all_ok = True
    
    for field in required_fields:
        val = first_acc.get(field)
        if val and isinstance(val, str) and len(val.strip()) > 0:
            # Maskiere den Key für die Anzeige
            masked = val[:4] + "..." + val[-4:] if len(val) > 8 else "***"
            print(f"   ✅ {field}: Gefunden ({masked})")
        else:
            print(f"   ❌ {field}: FEHLT oder ist leer!")
            # Prüfe auf Tippfehler
            for k in first_acc.keys():
                if k.lower() == field.lower() and k != field:
                    print(f"      -> HINWEIS: Du hast '{k}' geschrieben. Es muss exakt '{field}' heißen!")
            all_ok = False

    if all_ok:
        print("\n🎉 Struktur sieht PERFEKT aus. Wenn der Test trotzdem fehlschlägt, sind die API-Daten selbst ungültig (z.B. falsche IP-Berechtigung bei Bitget).")
    else:
        print("\n⚠️ Bitte korrigiere die Namen der Schlüssel in der secret.json.")

if __name__ == "__main__":
    check()
