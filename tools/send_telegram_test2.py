import sys, os, json
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from kbot.utils.telegram import send_document
p = os.path.join(os.path.dirname(__file__), '..', 'artifacts', 'interactive_charts', 'kbot_BTC_USDT_USDT_1d.html')
secret_path = os.path.join(os.path.dirname(__file__), '..', 'secret.json')
with open(secret_path) as f:
    s = json.load(f)
bot = s.get('telegram', {}).get('bot_token')
chat = s.get('telegram', {}).get('chat_id')
print('bot_token present:', bool(bot))
print('chat_id present:', bool(chat))
print('file exists:', os.path.exists(p))
print('calling send_document...')
res = send_document(bot, chat, p, caption='KBot interactive chart (test2)')
print('send_document returned:', res)
