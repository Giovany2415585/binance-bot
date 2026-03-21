import os
import time
import hmac
import hashlib
import requests
import threading
import json
from datetime import datetime

# ── Configuración ──────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "8622111444:AAHKYOFrIAFGvPdhHlev6UwfNoKtUFsS93o")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "5800355077")
BINANCE_API_KEY  = os.getenv("BINANCE_API_KEY",  "Y5Cw2JrhUeDhSkKVE5cE36Dd715ggI1k3k4lFkjX8wKAhn4kn6EHY6XguO3iiy6g")
BINANCE_SECRET   = os.getenv("BINANCE_SECRET",   "LVB0ZL2LdhKLri6t03SPAiWIjvpAn2QR13znhe8TeGHbWYakBn1Y26r88fVUsFrj")
MY_UID           = "518173796"

POLL_INTERVAL = 10
BASE_URL      = "https://api.binance.com"

bot_activo = True
seen       = set()
lock       = threading.Lock()

def sign(params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return hmac.new(BINANCE_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()

def binance_get(path, params):
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign(params)
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=10)
    r.raise_for_status()
    return r.json()

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)

def get_updates(offset):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    r = requests.get(url, params={"timeout": 5, "offset": offset}, timeout=10)
    return r.json().get("result", [])

def fmt_time(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000).strftime("%d/%m/%Y %H:%M:%S")
    except:
        return str(ms)

def fetch_pay_transactions(since_ms, limit=50):
    try:
        data = binance_get("/sapi/v1/pay/transactions", {"startTimestamp": since_ms, "limit": limit})
        if isinstance(data, dict):
            return data.get("data", [])
        return []
    except Exception as e:
        print(f"[pay error] {e}")
        return []

def fetch_balance():
    try:
        data = binance_get("/sapi/v1/asset/balance", {"asset": "USDT"})
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[balance error] {e}")
        return {}

def is_incoming(t):
    receiver_id = str(t.get("receiverInfo", {}).get("binanceId", ""))
    return receiver_id == MY_UID

def get_counterpart_name(t):
    if is_incoming(t):
        payer = t.get("payerInfo", {})
        return payer.get("name") or str(payer.get("binanceId", "Desconocido"))
    else:
        receiver = t.get("receiverInfo", {})
        return receiver.get("name") or str(receiver.get("binanceId", "Desconocido"))

def fmt_pay(t):
    incoming    = is_incoming(t)
    monto       = t.get("amount", "?")
    moneda      = t.get("currency", "?")
    contraparte = get_counterpart_name(t)
    orden       = t.get("orderId", "N/A")
    ts          = t.get("transactionTime", int(time.time() * 1000))
    nota        = t.get("note", "") or ""

    if incoming:
        emoji  = "💚"
        titulo = "PAGO RECIBIDO"
        quien  = f"👤 De: <b>{contraparte}</b>"
    else:
        emoji  = "🔴"
        titulo = "PAGO ENVIADO"
        quien  = f"👤 Para: <b>{contraparte}</b>"

    msg = (
        f"{emoji} <b>{titulo}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🪙 Moneda: <b>{moneda}</b>\n"
        f"💰 Monto:  <b>{monto}</b>\n"
        f"{quien}\n"
        f"🕐 Fecha:  {fmt_time(ts)}\n"
        f"🔖 Orden:  <code>{str(orden)[:20]}</code>"
    )
    if nota:
        msg += f"\n📝 Nota: {nota}"
    return msg

def cmd_ayuda():
    return (
        "🤖 <b>Comandos disponibles</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "/ultimo — Último pago recibido\n"
        "/ultimos5 — Últimos 5 movimientos\n"
        "/balance — Saldo actual en USDT\n"
        "/recibidos — Últimos pagos recibidos\n"
        "/enviados — Últimos pagos enviados\n"
        "/on — Activar notificaciones\n"
        "/off — Pausar notificaciones\n"
        "/estado — Ver si está activo o pausado\n"
        "/limpiar — Borrar historial\n"
        "/ayuda — Ver esta lista"
    )

def cmd_balance():
    b = fetch_balance()
    if not b:
        return "❌ No se pudo obtener el balance."
    libre     = float(b.get("free", 0))
    bloqueado = float(b.get("locked", 0))
    total     = libre + bloqueado
    msg = (
        f"💼 <b>BALANCE ACTUAL</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>USDT disponible:</b> {libre:.2f}\n"
    )
    if bloqueado > 0:
        msg += f"🔒 <b>USDT bloqueado:</b> {bloqueado:.2f}\n"
    msg += f"💰 <b>Total:</b> {total:.2f} USDT"
    return msg

def cmd_ultimo():
    since = int(time.time() * 1000) - 30 * 24 * 60 * 60 * 1000
    txs = fetch_pay_transactions(since, limit=20)
    recibidos = [t for t in txs if is_incoming(t)]
    if recibidos:
        return fmt_pay(recibidos[0])
    return "📭 No se encontraron pagos recibidos recientes."

def cmd_recibidos():
    since = int(time.time() * 1000) - 7 * 24 * 60 * 60 * 1000
    txs = fetch_pay_transactions(since, limit=20)
    recibidos = [t for t in txs if is_incoming(t)][:5]
    if not recibidos:
        return "📭 No hay pagos recibidos en los últimos 7 días."
    msgs = ["💚 <b>ÚLTIMOS PAGOS RECIBIDOS</b>\n━━━━━━━━━━━━━━━━━━"]
    for t in recibidos:
        msgs.append(fmt_pay(t))
        msgs.append("─────────────────")
    return "\n".join(msgs)

def cmd_enviados():
    since = int(time.time() * 1000) - 7 * 24 * 60 * 60 * 1000
    txs = fetch_pay_transactions(since, limit=20)
    enviados = [t for t in txs if not is_incoming(t)][:5]
    if not enviados:
        return "📭 No hay pagos enviados en los últimos 7 días."
    msgs = ["🔴 <b>ÚLTIMOS PAGOS ENVIADOS</b>\n━━━━━━━━━━━━━━━━━━"]
    for t in enviados:
        msgs.append(fmt_pay(t))
        msgs.append("─────────────────")
    return "\n".join(msgs)

def cmd_ultimos5():
    since = int(time.time() * 1000) - 7 * 24 * 60 * 60 * 1000
    txs = fetch_pay_transactions(since, limit=5)
    if not txs:
        return "📭 No hay movimientos recientes."
    msgs = ["📋 <b>ÚLTIMOS 5 MOVIMIENTOS</b>\n━━━━━━━━━━━━━━━━━━"]
    for t in txs:
        msgs.append(fmt_pay(t))
        msgs.append("─────────────────")
    return "\n".join(msgs)

def handle_command(text):
    global bot_activo, seen
    text = text.strip().lower().split("@")[0]

    if text == "/ayuda":
        send_telegram(cmd_ayuda())
    elif text == "/balance":
        send_telegram(cmd_balance())
    elif text == "/ultimo":
        send_telegram(cmd_ultimo())
    elif text == "/recibidos":
        send_telegram(cmd_recibidos())
    elif text == "/enviados":
        send_telegram(cmd_enviados())
    elif text == "/ultimos5":
        send_telegram(cmd_ultimos5())
    elif text == "/on":
        with lock:
            bot_activo = True
        send_telegram("✅ <b>Notificaciones activadas.</b>")
    elif text == "/off":
        with lock:
            bot_activo = False
        send_telegram("⏸ <b>Notificaciones pausadas.</b> Escribe /on para reactivar.")
    elif text == "/estado":
        estado = "✅ <b>Activo</b>" if bot_activo else "⏸ <b>Pausado</b>"
        send_telegram(f"📊 Estado: {estado}")
    elif text == "/limpiar":
        with lock:
            seen.clear()
        send_telegram("🧹 <b>Historial borrado.</b> El bot notificará pagos recientes nuevamente.")
    elif text == "/debug":
        since = int(time.time() * 1000) - 7 * 24 * 60 * 60 * 1000
        txs = fetch_pay_transactions(since, limit=3)
        if txs:
            send_telegram(f"<code>{json.dumps(txs[0], indent=2)[:3000]}</code>")
        else:
            send_telegram("Sin transacciones")
    elif text == "/start":
        send_telegram("🤖 <b>Bot de Binance Pay activo</b>\nEscribe /ayuda para ver los comandos.")

def commands_loop():
    offset = 0
    print("[commands] Escuchando comandos...")
    while True:
        try:
            updates = get_updates(offset)
            for u in updates:
                offset = u["update_id"] + 1
                msg = u.get("message", {})
                text = msg.get("text", "")
                if text.startswith("/"):
                    print(f"[cmd] {text}")
                    handle_command(text)
        except Exception as e:
            print(f"[commands error] {e}")
        time.sleep(2)

def monitor_loop():
    global seen
    since = int(time.time() * 1000) - 24 * 60 * 60 * 1000
    for t in fetch_pay_transactions(since):
        seen.add(t.get("orderId") or str(t))
    print(f"[bot] Historial previo cargado: {len(seen)} transacciones")

    while True:
        if bot_activo:
            since = int(time.time() * 1000) - 2 * 60 * 1000
            for t in fetch_pay_transactions(since):
                uid = t.get("orderId") or str(t)
                with lock:
                    if uid not in seen:
                        seen.add(uid)
                        send_telegram(fmt_pay(t))
                        direccion = "RECIBIDO" if is_incoming(t) else "ENVIADO"
                        print(f"[{direccion}] {t.get('amount')} {t.get('currency')}")
        time.sleep(POLL_INTERVAL)

def main():
    send_telegram(
        "🤖 <b>Bot de Binance Pay iniciado</b>\n"
        "Monitoreando pagos cada 10 segundos…\n\n"
        "Escribe /ayuda para ver los comandos disponibles."
    )
    print("[bot] Iniciado.")
    t = threading.Thread(target=commands_loop, daemon=True)
    t.start()
    monitor_loop()

if __name__ == "__main__":
    main()
