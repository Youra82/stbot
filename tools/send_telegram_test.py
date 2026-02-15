import json, os
from kbot.utils.telegram import send_document
p = os.path.join('artifacts', 'interactive_charts', 'kbot_BTC_USDT_USDT_1d.html')
with open('secret.json') as f:
    s = json.load(f)
bot = s.get('telegram', {}).get('bot_token')
chat = s.get('telegram', {}).get('chat_id')
print('bot_token present:', bool(bot))
print('chat_id present:', bool(chat))
print('file exists:', os.path.exists(p))
print('calling send_document...')
res = send_document(bot, chat, p, caption='KBot interactive chart (test)')
print('send_document returned:', res)
