import os
import time
import requests

TOKEN = "8519535855:AAFF2bcBsDsYGb4gLuONP_T29xuQn7PbQPU"
CHAT_ID = "6393685498"

def send_message(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": text
    }
    requests.post(url, data=data)

send_message("BOT BAŞLADI 🚀")

while True:
    pass

send_message("BOT BAŞLADI 🚀")

while True:
    try:
        price = get_btc_price()
        send_message(f"BTC fiyatı: {price}")
        time.sleep(300)
    except Exception as e:
        print("Hata:", e)
        time.sleep(60)
