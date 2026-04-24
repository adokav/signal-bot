print("BOT BAŞLADI")
send_message("BOT BAŞLADI")
import os
import time
import requests

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

def send_message(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": text}
    requests.post(url, data=data, timeout=10)

def get_btc_price():
    url = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.json()["price"]

send_message("Bot aktif 🚀")

while True:
    try:
        price = get_btc_price()
        send_message(f"BTC fiyatı: {price}")
        time.sleep(300)
    except Exception as e:
        print("Hata:", e)
        time.sleep(60)
