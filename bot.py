import os
import time
import hmac
import hashlib
import requests
import threading
import json
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ── Configuración ──────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
BINANCE_API_KEY  = os.getenv("BINANCE_API_KEY",  "")
BINANCE_SECRET   = os.getenv("BINANCE_SECRET",   "")
MY_UID           = "518173796"
HTTP_PORT        = int(os.getenv("PORT", "8080"))
INTERNAL_SECRET  = os.getenv("INTERNAL_SECRET", "cinebox_secret_2026_xK9mP3")
CINEBOX_BACKEND  = os.getenv("CINEBOX_BACKEND", "https://cinebox-web-production.up.railway.app")
CINEBOX_BACKEND  = os.getenv("CINEBOX_BACKEND", "https://cinebox-web-production.up.railway.app")

AUTHORIZED_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "5800355077"))

POLL_INTERVAL = 10
BASE_URL      = "https://api.binance.com"

bot_activo = True
seen       = set()
lock       = threading.Lock()
esperando_monto_conversion = {}
esperando_monto_cop        = {}

# ── Binance helpers ────────────────────────────────────────────

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

def fetch_balance():
    try:
        data = binance_get("/sapi/v1/asset/wallet/balance", {})
        if isinstance(data, list):
            for wallet in data:
                if wallet.get("walletName") == "Funding":
                    btc_balance = float(wallet.get("balance", 0))
                    price_data  = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=10).json()
                    btc_price   = float(price_data.get("price", 0))
                    usdt_total  = btc_balance * btc_price
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

def fmt_time(ms):
    try:
        from datetime import timezone, timedelta
        tz_colombia = timezone(timedelta(hours=-5))
        return datetime.fromtimestamp(int(ms) / 1000, tz=tz_colombia).strftime("%d/%m/%Y %H:%M:%S")
    except:
        return str(ms)

def fmt_pay(t):
    incoming    = is_incoming(t)
    monto       = t.get("amount", "?")
    moneda      = t.get("currency", "?")
    contraparte = get_counterpart_name(t)
    orden       = t.get("orderId", "N/A")
    ts          = t.get("transactionTime", int(time.time() * 1000))

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
        f"🔖 Order ID: <code>{str(orden)}</code>"
    )
    return msg

# ── Telegram helpers ───────────────────────────────────────────

def send_telegram(text, chat_id=None, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id or TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": reply_markup or {
            "keyboard": [[{"text": "🏠 Menú"}]],
            "resize_keyboard": True,
            "persistent": True
        }
    }
    requests.post(url, json=payload, timeout=10)

def answer_callback(callback_query_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"
    requests.post(url, json={"callback_query_id": callback_query_id}, timeout=10)

def get_updates(offset):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    r   = requests.get(url, params={"timeout": 5, "offset": offset}, timeout=10)
    return r.json().get("result", [])

def is_authorized(chat_id):
    return int(chat_id) == AUTHORIZED_CHAT_ID

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
                {"text": "💱 Dólar en COP", "callback_data": "/dolar"},
                {"text": "📊 Resumen hoy",  "callback_data": "/resumen"}
            ],
            [
                {"text": "🇺🇸 USDT → 🇨🇴 COP", "callback_data": "/convertir"},
                {"text": "🇨🇴 COP → 🇺🇸 USDT", "callback_data": "/convertircop"}
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
    txs = fetch_pay_transactions(limit=1)
    if not txs:
        return "📭 No hay transacciones recientes."
    return fmt_pay(txs[0])

def cmd_ultimos(n=5):
    txs = fetch_pay_transactions(limit=n)
    if not txs:
        return "📭 No hay transacciones recientes."
    return "\n\n".join(fmt_pay(t) for t in txs[:n])

def cmd_recibidos():
    txs = fetch_pay_transactions(limit=20)
    recv = [t for t in txs if is_incoming(t)][:5]
    if not recv:
        return "📭 No hay pagos recibidos recientes."
    return "\n\n".join(fmt_pay(t) for t in recv)

def cmd_enviados():
    txs = fetch_pay_transactions(limit=20)
    sent = [t for t in txs if not is_incoming(t)][:5]
    if not sent:
        return "📭 No hay pagos enviados recientes."
    return "\n\n".join(fmt_pay(t) for t in sent)

def handle_command(text, chat_id):
    global bot_activo
    if text in ("/start", "/ayuda", "🏠 Menú"):
        cmd_ayuda(chat_id)
    elif text == "/balance":
        send_telegram(cmd_balance(), chat_id=chat_id, reply_markup=get_menu_markup())
    elif text == "/ultimo":
        send_telegram(cmd_ultimo(), chat_id=chat_id, reply_markup=get_menu_markup())
    elif text == "/ultimos5":
        send_telegram(cmd_ultimos(5), chat_id=chat_id, reply_markup=get_menu_markup())
    elif text == "/recibidos":
        send_telegram(cmd_recibidos(), chat_id=chat_id, reply_markup=get_menu_markup())
    elif text == "/enviados":
        send_telegram(cmd_enviados(), chat_id=chat_id, reply_markup=get_menu_markup())
    elif text == "/on":
        bot_activo = True
        send_telegram("✅ Notificaciones activadas.", chat_id=chat_id, reply_markup=get_menu_markup())
    elif text == "/off":
        bot_activo = False
        send_telegram("⏸ Notificaciones pausadas.", chat_id=chat_id, reply_markup=get_menu_markup())
    elif text == "/estado":
        estado = "✅ Activo" if bot_activo else "⏸ Pausado"
        send_telegram(f"📊 <b>Estado del bot:</b> {estado}", chat_id=chat_id, reply_markup=get_menu_markup())
    elif text == "/dolar":
        try:
            r      = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=USDTCOP", timeout=10)
            precio = float(r.json().get("price", 0))
            send_telegram(f"💱 <b>DÓLAR HOY</b>\n━━━━━━━━━━━━━━━━━━\n🇨🇴 <b>1 USD = {precio:,.2f} COP</b>", chat_id=chat_id, reply_markup=get_menu_markup())
        except:
            send_telegram("❌ No se pudo obtener el precio.", chat_id=chat_id)
    elif text == "/resumen":
        try:
            from datetime import timezone, timedelta
            tz_colombia = timezone(timedelta(hours=-5))
            hoy         = datetime.now(tz_colombia).replace(hour=0, minute=0, second=0, microsecond=0)
            since       = int(hoy.timestamp() * 1000)
            txs         = fetch_pay_transactions(since, limit=100)
            ingresado   = sum(float(t.get("amount", 0)) for t in txs if is_incoming(t))
            salido      = sum(abs(float(t.get("amount", 0))) for t in txs if not is_incoming(t))
            neto_real   = ingresado - salido
            pagos_in    = len([t for t in txs if is_incoming(t)])
            pagos_out   = len([t for t in txs if not is_incoming(t)])
            signo       = "+" if neto_real >= 0 else "-"
            msg = (
                f"📊 <b>RESUMEN DE HOY</b>\n"
                f"🕐 {datetime.now(tz_colombia).strftime('%d/%m/%Y %H:%M:%S')}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💚 <b>Ingresaron:</b> {ingresado:.2f} USDT ({pagos_in} pagos)\n"
                f"🔴 <b>Salieron:</b> {salido:.2f} USDT ({pagos_out} pagos)\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💰 <b>Neto:</b> {signo}{abs(neto_real):.2f} USDT"
            )
            send_telegram(msg, chat_id=chat_id, reply_markup=get_menu_markup())
        except Exception as e:
            send_telegram("❌ No se pudo obtener el resumen.", chat_id=chat_id)
    elif text == "/convertircop":
        with lock:
            esperando_monto_cop[chat_id] = True
        send_telegram(
            "🔄 <b>Convertir COP a USDT</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "¿Cuántos COP quieres convertir?\n"
            "Escribe uno o varios números\n"
            "separados por espacio o por línea:",
            chat_id=chat_id
        )
    elif text == "/convertir":
        with lock:
            esperando_monto_conversion[chat_id] = True
        send_telegram(
            "🔄 <b>Convertir USDT a COP</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "¿Cuántos USDT quieres convertir?\n"
            "Escribe uno o varios números\n"
            "separados por espacio o por línea:",
            chat_id=chat_id
        )
    elif text == "/debug":
        since = int(time.time() * 1000) - 7 * 24 * 60 * 60 * 1000
        txs   = fetch_pay_transactions(since, limit=3)
        if txs:
            send_telegram(f"<code>{json.dumps(txs[0], indent=2)[:3000]}</code>", chat_id=chat_id)
        else:
            send_telegram("Sin transacciones", chat_id=chat_id)

def parse_numeros(text):
    numeros = []
    for token in text.replace("\n", " ").split():
        try:
            numeros.append(float(token.replace(",", ".")))
        except:
            pass
    return numeros

# ── HTTP Server para verificar Order IDs ───────────────────────

def notify_cinebox_orderid(binance_order_id, monto):
    try:
        res = requests.post(
            f"{CINEBOX_BACKEND}/api/checkout/verify-orderid-internal",
            json={"binanceOrderId": binance_order_id, "amount": float(monto), "secret": INTERNAL_SECRET},
            timeout=30
        )
        data = res.json()
        if data.get("verified"):
            send_telegram(
                f"✅ <b>Pago entregado automáticamente</b>\n"
                f"🔖 Order ID: <code>{binance_order_id}</code>\n"
                f"💰 Monto: {monto} USDT"
            )
            print(f"[CINEBOX] Entrega automatica OK — Order ID: {binance_order_id}")
        else:
            print(f"[CINEBOX] Order ID {binance_order_id} sin orden encontrada: {data.get('message','')}")
    except Exception as e:
        print(f"[CINEBOX] Error: {e}")

def verify_order_id(order_id, monto_esperado):
    """
    Busca el Order ID en las últimas transacciones de Binance.
    Verifica que:
    1. El Order ID exista exactamente (comparación completa)
    2. Sea un pago entrante a tu cuenta
    3. El monto coincida (con tolerancia del 1%)
    """
    try:
        # Buscar en las últimas 24 horas
        since = int(time.time() * 1000) - 24 * 60 * 60 * 1000
        txs   = fetch_pay_transactions(since, limit=100)

        for t in txs:
            tx_order_id = str(t.get("orderId", "")).strip()

            # Comparación EXACTA del Order ID completo
            if tx_order_id != str(order_id).strip():
                continue

            # Verificar que sea un pago entrante a tu cuenta
            if not is_incoming(t):
                print(f"[VERIFY] Order ID {order_id} encontrado pero NO es entrante")
                return {"valid": False, "reason": "El pago no fue enviado a esta cuenta"}

            # Verificar monto con tolerancia del 1%
            monto_real     = float(t.get("amount", 0))
            monto_esperado = float(monto_esperado)
            if monto_real < monto_esperado * 0.99:
                print(f"[VERIFY] Monto insuficiente: recibido {monto_real}, esperado {monto_esperado}")
                return {
                    "valid":    False,
                    "reason":   f"Monto insuficiente: recibido {monto_real} USDT, esperado {monto_esperado} USDT"
                }

            print(f"[VERIFY] Order ID {order_id} verificado ✅ — Monto: {monto_real} USDT")
            return {
                "valid":    True,
                "orderId":  tx_order_id,
                "amount":   monto_real,
                "currency": t.get("currency", "USDT"),
            }

        print(f"[VERIFY] Order ID {order_id} no encontrado en transacciones recientes")
        return {"valid": False, "reason": "Order ID no encontrado — verifica que copiaste el número completo"}

    except Exception as e:
        print(f"[VERIFY] Error: {e}")
        return {"valid": False, "reason": "Error al verificar con Binance"}


class VerifyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/health":
            self._json(200, {"status": "ok"})
            return

        if parsed.path == "/verify-order":
            params         = parse_qs(parsed.query)
            order_id       = params.get("orderId", [None])[0]
            monto_esperado = params.get("monto",   [None])[0]
            secret         = params.get("secret",  [None])[0]

            if secret != INTERNAL_SECRET:
                self._json(401, {"error": "No autorizado"})
                return

            if not order_id or not monto_esperado:
                self._json(400, {"error": "orderId y monto son requeridos"})
                return

            result = verify_order_id(order_id, monto_esperado)
            self._json(200, result)
            return

        self._json(404, {"error": "Not found"})

    def _json(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print(f"[HTTP] {args[0]} {args[1]}")


def http_server_loop():
    server = HTTPServer(("0.0.0.0", HTTP_PORT), VerifyHandler)
    print(f"[HTTP] Servidor escuchando en puerto {HTTP_PORT}")
    server.serve_forever()

# ── Monitor loop ───────────────────────────────────────────────

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
                        print(f"[{direccion}] {t.get('amount')} {t.get('currency')} — Order ID: {uid}")
                        if is_incoming(t):
                            binance_order_id = str(uid).strip()
                            monto = t.get('amount', 0)
                            print(f"[CINEBOX] Pago entrante detectado — Order ID: {binance_order_id} — {monto} USDT")
                            threading.Thread(target=notify_cinebox_orderid, args=(binance_order_id, monto), daemon=True).start()
        time.sleep(POLL_INTERVAL)

# ── Commands loop ──────────────────────────────────────────────

def commands_loop():
    offset = 0
    print("[commands] Escuchando comandos...")
    while True:
        try:
            updates = get_updates(offset)
            for u in updates:
                offset = u["update_id"] + 1

                if "callback_query" in u:
                    cb      = u["callback_query"]
                    chat_id = cb["message"]["chat"]["id"]
                    data    = cb.get("data", "")
                    answer_callback(cb["id"])
                    if is_authorized(chat_id):
                        handle_command(data, chat_id)
                    continue

                msg     = u.get("message", {})
                text    = msg.get("text", "")
                chat_id = msg.get("chat", {}).get("id")

                if not text or not chat_id:
                    continue

                if text == "🏠 Menú":
                    cmd_ayuda(chat_id)
                elif chat_id and esperando_monto_conversion.get(chat_id):
                    with lock:
                        esperando_monto_conversion[chat_id] = False
                    try:
                        numeros = parse_numeros(text)
                        if not numeros:
                            raise ValueError
                        r    = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=USDTCOP", timeout=10)
                        tasa = float(r.json().get("price", 0))
                        lineas = [f"💵 {n:.2f} USDT = {n * tasa:,.2f} COP" for n in numeros]
                        lineas.append(f"📈 Tasa: 1 USD = {tasa:,.2f} COP")
                        send_telegram("\n".join(lineas), chat_id=chat_id)
                    except:
                        send_telegram("❌ Escribe solo números. Ejemplo: 5 10 3", chat_id=chat_id)
                elif chat_id and esperando_monto_cop.get(chat_id):
                    with lock:
                        esperando_monto_cop[chat_id] = False
                    try:
                        numeros = parse_numeros(text)
                        if not numeros:
                            raise ValueError
                        r    = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=USDTCOP", timeout=10)
                        tasa = float(r.json().get("price", 0))
                        lineas = [f"🇨🇴 {n:,.0f} COP = {n / tasa:.2f} USDT" for n in numeros]
                        lineas.append(f"📈 Tasa: 1 USD = {tasa:,.2f} COP")
                        send_telegram("\n".join(lineas), chat_id=chat_id)
                    except:
                        send_telegram("❌ Escribe solo números. Ejemplo: 50000 100000", chat_id=chat_id)
                elif text.startswith("/") and chat_id:
                    print(f"[cmd] {text} from {chat_id}")
                    handle_command(text, chat_id)

        except Exception as e:
            print(f"[commands error] {e}")
        time.sleep(2)

# ── Main ───────────────────────────────────────────────────────

def main():
    send_telegram(
        "🤖 <b>Bot de Binance Pay iniciado</b>\n"
        "Monitoreando pagos cada 10 segundos…\n\n"
        "Toca el botón para ver opciones 👇",
        reply_markup=get_menu_markup()
    )
    print("[bot] Iniciado.")

    # Servidor HTTP en hilo separado
    t_http = threading.Thread(target=http_server_loop, daemon=True)
    t_http.start()

    # Comandos Telegram en hilo separado
    t_cmd = threading.Thread(target=commands_loop, daemon=True)
    t_cmd.start()

    # Monitor principal
    monitor_loop()

if __name__ == "__main__":
    main()