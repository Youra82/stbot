# src/kbot/utils/telegram.py
import requests
import logging
import subprocess
import json
import os

logger = logging.getLogger(__name__)

def send_message(bot_token, chat_id, message):
    if not bot_token or not chat_id:
        logger.warning("Telegram Bot-Token oder Chat-ID nicht konfiguriert.")
        return

    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {'chat_id': chat_id, 'text': message, 'parse_mode': 'HTML'}

    try:
        response = requests.post(api_url, data=payload, timeout=10)
        if response.status_code != 200:
            logger.error(f"Fehler beim Senden der Telegram-Nachricht: {response.text}")
    except Exception as e:
        logger.error(f"Ausnahme beim Senden der Telegram-Nachricht: {e}")

def send_photo(bot_token, chat_id, photo_path, caption=""):
    """Sendet ein Bild an einen Telegram-Chat via curl (robust)."""
    if not bot_token or not chat_id:
        logger.warning("Telegram Bot-Token oder Chat-ID nicht konfiguriert.")
        return False

    try:
        command = [
            'curl', '-s', '-X', 'POST',
            f'https://api.telegram.org/bot{bot_token}/sendPhoto',
            '-F', f'chat_id={chat_id}',
            '-F', f'photo=@{photo_path}',
            '-F', f'caption={caption}'
        ]
        result = subprocess.run(command, capture_output=True, text=True)

        if result.returncode != 0:
            logger.error(f"Fehler beim Ausführen des curl-Befehls: {result.stderr}")
            return False

        response_json = json.loads(result.stdout)
        if not response_json.get('ok'):
            logger.error(f"Fehler von Telegram API: {response_json.get('description')}")
            return False

        return True

    except FileNotFoundError:
        logger.error("curl nicht gefunden. Versuche mit requests...")
        # Fallback auf requests
        try:
            api_url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
            with open(photo_path, 'rb') as photo:
                files = {'photo': photo}
                data = {'chat_id': chat_id, 'caption': caption}
                response = requests.post(api_url, data=data, files=files, timeout=60)
                return response.status_code == 200
        except Exception as e:
            logger.error(f"Fehler beim Senden via requests: {e}")
            return False
    except Exception as e:
        logger.error(f"Unerwarteter Fehler beim Senden des Bildes: {e}")
        return False

def send_document(bot_token, chat_id, file_path, caption=""):
    """Sendet ein Dokument (z.B. eine CSV-Datei) an einen Telegram-Chat.

    Rückgabe: True bei Erfolg, False bei Fehler (loggt Details).
    """
    if not bot_token or not chat_id:
        logger.warning("Telegram Bot-Token oder Chat-ID nicht konfiguriert.")
        return False

    api_url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    payload = {
        'chat_id': chat_id,
        'caption': caption
    }

    try:
        with open(file_path, 'rb') as doc:
            files = {'document': doc}
            response = requests.post(api_url, data=payload, files=files, timeout=30)
            response.raise_for_status()
            if response.status_code == 200:
                logger.info(f"Dokument '{os.path.basename(file_path)}' erfolgreich an Chat {chat_id} gesendet.")
                return True
            logger.error(f"Fehler beim Senden des Dokuments via Telegram (Status {response.status_code}): {response.text}")
            return False
    except FileNotFoundError:
        logger.error(f"Zu sendende Datei nicht gefunden: {file_path}")
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Netzwerkfehler beim Senden des Dokuments via Telegram: {e}")
        return False
    except Exception as e:
        logger.error(f"Allgemeiner Fehler beim Senden des Dokuments via Telegram: {e}")
        return False
