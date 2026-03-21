import os
import time
import hmac
import hashlib
import requests
from datetime import datetime

# ── Configuración ──────────────────────────────────────────────
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN",  "8622111444:AAHKYOFrIAFGvPdhHlev6UwfNoKtUFsS93o")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "5800355077")
BINANCE_API_KEY  = os.getenv("BINANCE_API_KEY",  "Y5Cw2JrhUeDhSkKVE5cE36Dd715ggI1k3k4lFkjX8wKAhn4kn6EHY6XguO3iiy6g")
BINANCE_SECRET   = os.getenv("BINANCE_SECRET",   "LVB0ZL2LdhKLri6t03SPAiWIjvpAn2QR13znhe8TeGHbWYakBn1Y26r88fVUsFrj")

POLL_INTERVAL = 10  # segundos entre consultas
BASE_URL      = "https://api.binance.com"

# ── Helpers ────────────────────────────────────────────────────
def sign(params: dict) -> str:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return hmac.new(BINANCE_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def binance_get(path: str, params: dict) -> dict:
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign(params)
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=10)
    r.raise_for_status()
    return r.json()

def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)

def fmt_time(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%d/%m/%Y %H:%M:%S")

# ── Fetch transactions ─────────────────────────────────────────
def fetch_deposits(since_ms: int) -> list:
    try:
        data = binance_get("/sapi/v1/capital/deposit/hisrec", {"startTime": since_ms, "limit": 50, "status": 1})
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[deposits error] {e}")
        return []

def fetch_withdrawals(since_ms: int) -> list:
    try:
        data = binance_get("/sapi/v1/pay/transactions", {"startTimestamp": since_ms, "limit": 50})
        if isinstance(data, dict):
            return data.get("data", [])
        return []
    except Exception as e:
        print(f"[withdrawals error] {e}")
        return []

# ── Formatters ─────────────────────────────────────────────────
def fmt_deposit(d: dict) -> str:
    status_map = {0: "⏳ Pendiente", 1: "✅ Confirmado", 6: "⚠️ Anomalía"}
    status = status_map.get(d.get("status", 0), "❓ Desconocido")
    return (
        f"💚 <b>DEPÓSITO RECIBIDO</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🪙 Moneda: <b>{d.get('coin','?')}</b>\n"
        f"💰 Monto:  <b>{d.get('amount','?')}</b>\n"
        f"📌 Estado: {status}\n"
        f"🕐 Fecha:  {fmt_time(d.get('insertTime', 0))}\n"
        f"🔗 TxID:   <code>{d.get('txId','N/A')[:24]}…</code>"
    )

def fmt_withdrawal(w: dict) -> str:
    status_map = {0:"📧 Correo enviado", 1:"❌ Cancelado", 2:"⏳ Esperando",
                  3:"🔴 Rechazado", 4:"⚙️ Procesando", 5:"⚠️ Fallo",
                  6:"✅ Completado"}
    status = status_map.get(w.get("status", 0), "❓ Desconocido")
    return (
        f"🔴 <b>RETIRO / PAGO ENVIADO</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🪙 Moneda: <b>{w.get('coin','?')}</b>\n"
        f"💸 Monto:  <b>{w.get('amount','?')}</b>\n"
        f"📬 Destino: <code>{str(w.get('address','?'))[:20]}…</code>\n"
        f"📌 Estado: {status}\n"
        f"🕐 Fecha:  {fmt_time(w.get('applyTime', 0) if isinstance(w.get('applyTime'), int) else int(time.time()*1000))}"
    )

# ── Main loop ──────────────────────────────────────────────────
def main():
    send_telegram("🤖 <b>Bot de Binance iniciado</b>\nMonitoreando depósitos y retiros cada 10 segundos…")
    print("[bot] Iniciado. Monitoreando cada 10 seg…")

    seen_deposits    = set()
    seen_withdrawals = set()

    # Cargar historial previo sin notificar (últimas 24h)
    since = int(time.time() * 1000) - 24 * 60 * 60 * 1000
    for d in fetch_deposits(since):
        seen_deposits.add(d.get("txId") or d.get("id") or str(d))
    for w in fetch_withdrawals(since):
        seen_withdrawals.add(w.get("id") or w.get("txId") or str(w))

    print(f"[bot] Historial previo cargado: {len(seen_deposits)} depósitos, {len(seen_withdrawals)} retiros")

    while True:
        since = int(time.time() * 1000) - 2 * 60 * 1000  # últimos 2 min

        for d in fetch_deposits(since):
            uid = d.get("txId") or d.get("id") or str(d)
            if uid not in seen_deposits:
                seen_deposits.add(uid)
                send_telegram(fmt_deposit(d))
                print(f"[+] Depósito: {d.get('amount')} {d.get('coin')}")

        for w in fetch_withdrawals(since):
            uid = w.get("id") or w.get("txId") or str(w)
            if uid not in seen_withdrawals:
                seen_withdrawals.add(uid)
                send_telegram(fmt_withdrawal(w))
                print(f"[-] Retiro: {w.get('amount')} {w.get('coin')}")

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
