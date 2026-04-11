import os
import time
import hmac
import hashlib
import requests
import threading
import json
from datetime import datetime

# ── Configuración ──────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
BINANCE_API_KEY  = os.getenv("BINANCE_API_KEY",  "")
BINANCE_SECRET   = os.getenv("BINANCE_SECRET",   "")
MY_UID           = "518173796"

# ── Seguridad: solo tu chat_id puede usar el bot ───────────────
AUTHORIZED_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "5800355077"))

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

def send_telegram(text, chat_id=None, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id or TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    requests.post(url, json=payload, timeout=10)

def answer_callback(callback_query_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"
    requests.post(url, json={"callback_query_id": callback_query_id}, timeout=10)

def get_updates(offset):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    r = requests.get(url, params={"timeout": 5, "offset": offset}, timeout=10)
    return r.json().get("result", [])

def fmt_time(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000).strftime("%d/%m/%Y %H:%M:%S")
    except:
        return str(ms)

def fetch_pay_transactions(since_ms=None, limit=50):
    try:
        params = {"limit": limit}
        if since_ms:
            params["startTime"] = since_ms
        data = binance_get("/sapi/v1/pay/transactions", params)
        if isinstance(data, dict):
            return data.get("data", [])
        return []
    except Exception as e:
        print(f"[pay error] {e}")
        return []

# ── FIX: endpoint correcto para balance USDT ──────────────────
def fetch_balance():
    try:
        data = binance_get("/sapi/v1/asset/wallet/balance", {})
        if isinstance(data, list):
            for wallet in data:
                if wallet.get("walletName") == "Funding":
                    btc_balance = float(wallet.get("balance", 0))
                    price_data = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=10).json()
                    btc_price = float(price_data.get("price", 0))
                    usdt_total = btc_balance * btc_price
                    return {"free": str(usdt_total), "locked": "0"}
        return {}
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

# ── Menú principal con botones inline ─────────────────────────
def get_menu_markup():
    return {
        "inline_keyboard": [
            [
                {"text": "💼 Balance",     "callback_data": "/balance"},
                {"text": "📋 Últimos 5",   "callback_data": "/ultimos5"}
            ],
            [
                {"text": "💚 Recibidos",   "callback_data": "/recibidos"},
                {"text": "🔴 Enviados",    "callback_data": "/enviados"}
            ],
            [
                {"text": "🔔 Último pago", "callback_data": "/ultimo"},
                {"text": "📊 Estado",      "callback_data": "/estado"}
            ],
            [
                {"text": "✅ Activar notif.",  "callback_data": "/on"},
                {"text": "⏸ Pausar notif.",   "callback_data": "/off"}
            ],
            [
                {"text": "🧹 Limpiar historial", "callback_data": "/limpiar"}
            ],
            [
                {"text": "💱 Dólar en COP", "callback_data": "/dolar"}
            ]
        ]
    }

def cmd_ayuda(chat_id):
    send_telegram(
        "🤖 <b>Bot de Binance Pay</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Selecciona una opción:",
        chat_id=chat_id,
        reply_markup=get_menu_markup()
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

# ── Verificación de identidad ──────────────────────────────────
def is_authorized(chat_id):
    return int(chat_id) == AUTHORIZED_CHAT_ID

def handle_command(text, chat_id):
    global bot_activo, seen

    # 🔐 Solo tú puedes usar el bot
    if not is_authorized(chat_id):
        send_telegram("⛔ No autorizado.", chat_id=chat_id)
        return

    text = text.strip().lower().split("@")[0]

    if text in ("/ayuda", "/start", "/menu"):
        cmd_ayuda(chat_id)
    elif text == "/balance":
        send_telegram(cmd_balance(), chat_id=chat_id)
    elif text == "/ultimo":
        send_telegram(cmd_ultimo(), chat_id=chat_id)
    elif text == "/recibidos":
        send_telegram(cmd_recibidos(), chat_id=chat_id)
    elif text == "/enviados":
        send_telegram(cmd_enviados(), chat_id=chat_id)
    elif text == "/ultimos5":
        send_telegram(cmd_ultimos5(), chat_id=chat_id)
    elif text == "/on":
        with lock:
            bot_activo = True
        send_telegram("✅ <b>Notificaciones activadas.</b>", chat_id=chat_id)
    elif text == "/off":
        with lock:
            bot_activo = False
        send_telegram("⏸ <b>Notificaciones pausadas.</b> Escribe /on para reactivar.", chat_id=chat_id)
    elif text == "/estado":
        estado = "✅ <b>Activo</b>" if bot_activo else "⏸ <b>Pausado</b>"
        send_telegram(f"📊 Estado: {estado}", chat_id=chat_id)
    elif text == "/limpiar":
        with lock:
            seen.clear()
        send_telegram("🧹 <b>Historial borrado.</b>", chat_id=chat_id)
    elif text == "/dolar":
        try:
            r = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=USDTCOP", timeout=10)
            precio = float(r.json().get("price", 0))
            send_telegram(f"💱 <b>DÓLAR HOY</b>\n━━━━━━━━━━━━━━━━━━\n🇨🇴 <b>1 USD = {precio:,.2f} COP</b>", chat_id=chat_id)
        except Exception as e:
            send_telegram("❌ No se pudo obtener el precio.", chat_id=chat_id)
    elif text == "/debug":
        since = int(time.time() * 1000) - 7 * 24 * 60 * 60 * 1000
        txs = fetch_pay_transactions(since, limit=3)
        if txs:
            send_telegram(f"<code>{json.dumps(txs[0], indent=2)[:3000]}</code>", chat_id=chat_id)
        else:
            send_telegram("Sin transacciones", chat_id=chat_id)

def commands_loop():
    offset = 0
    print("[commands] Escuchando comandos...")
    while True:
        try:
            updates = get_updates(offset)
            for u in updates:
                offset = u["update_id"] + 1

                # Manejo de botones inline
                if "callback_query" in u:
                    cb = u["callback_query"]
                    chat_id = cb["message"]["chat"]["id"]
                    data    = cb.get("data", "")
                    answer_callback(cb["id"])
                    if is_authorized(chat_id):
                        handle_command(data, chat_id)
                    continue

                # Manejo de comandos de texto
                msg = u.get("message", {})
                text = msg.get("text", "")
                chat_id = msg.get("chat", {}).get("id")
                if text.startswith("/") and chat_id:
                    print(f"[cmd] {text} from {chat_id}")
                    handle_command(text, chat_id)

        except Exception as e:
            print(f"[commands error] {e}")
        time.sleep(2)

def monitor_loop():
    print("[bot] Monitor de Pay desactivado. Usa /balance para ver tu saldo.")
    while True:
        time.sleep(60)

def main():
    send_telegram(
        "🤖 <b>Bot de Binance Pay iniciado</b>\n"
        "Monitoreando pagos cada 10 segundos…\n\n"
        "Toca el botón para ver opciones 👇",
        reply_markup=get_menu_markup()
    )
    print("[bot] Iniciado.")
    t = threading.Thread(target=commands_loop, daemon=True)
    t.start()
    monitor_loop()

if __name__ == "__main__":
    main()