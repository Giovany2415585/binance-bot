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

AUTHORIZED_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "5800355077"))

POLL_INTERVAL = 10
BASE_URL      = "https://api.binance.com"

# ── Cuentas de Binance a monitorear ────────────────────────────
# Cada cuenta tiene sus propias API keys y su propio binanceId (UID),
# usado para saber si un pago es entrante o saliente en ESA cuenta.
_ACCOUNTS_CONFIG = [
    {
        "label": os.getenv("BINANCE_LABEL",   "CINEBOX.NET"),
        "emoji": os.getenv("BINANCE_EMOJI",   "🏠"),
        "key":   os.getenv("BINANCE_API_KEY", ""),
        "secret": os.getenv("BINANCE_SECRET", ""),
        "uid":   os.getenv("BINANCE_UID",     "518173796"),
    },
    {
        "label": os.getenv("BINANCE_LABEL_2",   "SHOP-CNBX"),
        "emoji": os.getenv("BINANCE_EMOJI_2",   "🏪"),
        "key":   os.getenv("BINANCE_API_KEY_2", ""),
        "secret": os.getenv("BINANCE_SECRET_2", ""),
        "uid":   os.getenv("BINANCE_UID_2",     ""),
    },
]
ACCOUNTS = [a for a in _ACCOUNTS_CONFIG if a["key"] and a["secret"]]

bot_activo = True
seen       = {a["label"]: set() for a in ACCOUNTS}
lock       = threading.Lock()
esperando_monto_conversion = {}
esperando_monto_cop        = {}

# ── Binance helpers ────────────────────────────────────────────

def sign(secret, params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()

def binance_get(account, path, params):
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign(account["secret"], params)
    headers = {"X-MBX-APIKEY": account["key"]}
    r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=10)
    r.raise_for_status()
    return r.json()

def fetch_pay_transactions(account, since_ms=None, limit=50):
    try:
        params = {"limit": limit}
        if since_ms:
            params["startTime"] = since_ms
        data = binance_get(account, "/sapi/v1/pay/transactions", params)
        if isinstance(data, dict):
            return data.get("data", [])
        return []
    except Exception as e:
        print(f"[pay error] [{account['label']}] {e}")
        return []

def fetch_balance(account):
    try:
        data = binance_get(account, "/sapi/v1/asset/wallet/balance", {})
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
        print(f"[balance error] [{account['label']}] {e}")
        return {}

def is_incoming(t, account):
    receiver_id = str(t.get("receiverInfo", {}).get("binanceId", ""))
    return receiver_id == str(account["uid"])

def get_counterpart_name(t, account):
    if is_incoming(t, account):
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

def fmt_pay(t, account):
    incoming    = is_incoming(t, account)
    monto       = t.get("amount", "?")
    moneda      = t.get("currency", "?")
    contraparte = get_counterpart_name(t, account)
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
        f"{account['emoji']} Cuenta: <b>{account['label']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🪙 Moneda: <b>{moneda}</b>\n"
        f"💰 Monto:  <b>{monto}</b>\n"
        f"{quien}\n"
        f"🕐 Fecha:  {fmt_time(ts)}\n"
        f"🔖 Order ID: <code>{str(orden)}</code>"
    )
    return msg

def fetch_all_accounts(since_ms=None, limit=50):
    """Trae transacciones de todas las cuentas configuradas, cada una etiquetada."""
    resultado = []
    for account in ACCOUNTS:
        for t in fetch_pay_transactions(account, since_ms=since_ms, limit=limit):
            resultado.append((t, account))
    resultado.sort(key=lambda pair: pair[0].get("transactionTime", 0), reverse=True)
    return resultado

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
    try:
        requests.post(url, json=payload, timeout=15)
    except Exception as e:
        print(f"[telegram error] {e}")

def answer_callback(callback_query_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"
    requests.post(url, json={"callback_query_id": callback_query_id}, timeout=10)

def get_updates(offset):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    try:
        r = requests.get(url, params={"timeout": 3, "offset": offset}, timeout=8)
        return r.json().get("result", [])
    except Exception as e:
        print(f"[updates error] {e}")
        return []

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
    if not ACCOUNTS:
        return "❌ No hay cuentas de Binance configuradas."
    lineas = ["💼 <b>BALANCE ACTUAL</b>", "━━━━━━━━━━━━━━━━━━"]
    total  = 0.0
    algun_error = False
    for account in ACCOUNTS:
        b = fetch_balance(account)
        if not b:
            lineas.append(f"{account['emoji']} <b>{account['label']}:</b> ❌ no disponible")
            algun_error = True
            continue
        libre = float(b.get("free", 0))
        total += libre
        lineas.append(f"{account['emoji']} <b>{account['label']}:</b> {libre:.2f} USDT")
    lineas.append("━━━━━━━━━━━━━━━━━━")
    lineas.append(f"💰 <b>Total combinado:</b> {total:.2f} USDT")
    if algun_error:
        lineas.append("⚠️ Alguna cuenta no pudo consultarse.")
    return "\n".join(lineas)

def cmd_ultimo():
    pares = fetch_all_accounts(limit=5)
    if not pares:
        return "📭 No hay transacciones recientes."
    t, account = pares[0]
    return fmt_pay(t, account)

def cmd_ultimos(n=5):
    pares = fetch_all_accounts(limit=n)
    if not pares:
        return "📭 No hay transacciones recientes."
    return "\n\n".join(fmt_pay(t, account) for t, account in pares[:n])

def cmd_recibidos():
    pares = fetch_all_accounts(limit=20)
    recv  = [(t, account) for t, account in pares if is_incoming(t, account)][:5]
    if not recv:
        return "📭 No hay pagos recibidos recientes."
    return "\n\n".join(fmt_pay(t, account) for t, account in recv)

def cmd_enviados():
    pares = fetch_all_accounts(limit=20)
    sent  = [(t, account) for t, account in pares if not is_incoming(t, account)][:5]
    if not sent:
        return "📭 No hay pagos enviados recientes."
    return "\n\n".join(fmt_pay(t, account) for t, account in sent)

def handle_command(text, chat_id):
    global bot_activo
    if text in ("/start", "/ayuda", "🏠 Menú"):
        cmd_ayuda(chat_id)
    elif text == "/balance":
        send_telegram(cmd_balance(), chat_id=chat_id)
    elif text == "/ultimo":
        send_telegram(cmd_ultimo(), chat_id=chat_id)
    elif text == "/ultimos5":
        send_telegram(cmd_ultimos(5), chat_id=chat_id)
    elif text == "/recibidos":
        send_telegram(cmd_recibidos(), chat_id=chat_id)
    elif text == "/enviados":
        send_telegram(cmd_enviados(), chat_id=chat_id)
    elif text == "/on":
        bot_activo = True
        send_telegram("✅ Notificaciones activadas.", chat_id=chat_id)
    elif text == "/off":
        bot_activo = False
        send_telegram("⏸ Notificaciones pausadas.", chat_id=chat_id)
    elif text == "/estado":
        estado  = "✅ Activo" if bot_activo else "⏸ Pausado"
        cuentas = ", ".join(f"{a['emoji']} {a['label']}" for a in ACCOUNTS) or "ninguna configurada"
        send_telegram(
            f"📊 <b>Estado del bot:</b> {estado}\n"
            f"🔗 <b>Cuentas monitoreadas:</b> {cuentas}",
            chat_id=chat_id
        )
    elif text == "/dolar":
        try:
            r      = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=USDTCOP", timeout=10)
            precio = float(r.json().get("price", 0))
            send_telegram(f"💱 <b>DÓLAR HOY</b>\n━━━━━━━━━━━━━━━━━━\n🇨🇴 <b>1 USD = {precio:,.2f} COP</b>", chat_id=chat_id)
        except:
            send_telegram("❌ No se pudo obtener el precio.", chat_id=chat_id)
    elif text == "/resumen":
        try:
            from datetime import timezone, timedelta
            tz_colombia = timezone(timedelta(hours=-5))
            hoy         = datetime.now(tz_colombia).replace(hour=0, minute=0, second=0, microsecond=0)
            since       = int(hoy.timestamp() * 1000)

            lineas = [
                "📊 <b>RESUMEN DE HOY</b>",
                f"🕐 {datetime.now(tz_colombia).strftime('%d/%m/%Y %H:%M:%S')}",
                "━━━━━━━━━━━━━━━━━━",
            ]
            total_ingresado = 0.0
            total_salido    = 0.0
            for account in ACCOUNTS:
                txs       = fetch_pay_transactions(account, since, limit=100)
                ingresado = sum(float(t.get("amount", 0)) for t in txs if is_incoming(t, account))
                salido    = sum(abs(float(t.get("amount", 0))) for t in txs if not is_incoming(t, account))
                pagos_in  = len([t for t in txs if is_incoming(t, account)])
                pagos_out = len([t for t in txs if not is_incoming(t, account)])
                total_ingresado += ingresado
                total_salido    += salido
                lineas.append(f"{account['emoji']} <b>{account['label']}</b>")
                lineas.append(f"  💚 Ingresaron: {ingresado:.2f} USDT ({pagos_in} pagos)")
                lineas.append(f"  🔴 Salieron: {salido:.2f} USDT ({pagos_out} pagos)")

            neto  = total_ingresado - total_salido
            signo = "+" if neto >= 0 else "-"
            lineas.append("━━━━━━━━━━━━━━━━━━")
            lineas.append(f"💰 <b>Neto combinado:</b> {signo}{abs(neto):.2f} USDT")
            send_telegram("\n".join(lineas), chat_id=chat_id)
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
        pares = fetch_all_accounts(since, limit=3)
        if pares:
            t, account = pares[0]
            send_telegram(f"{account['emoji']} {account['label']}\n<code>{json.dumps(t, indent=2)[:3000]}</code>", chat_id=chat_id)
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

# ── Monitor loop ───────────────────────────────────────────────

def monitor_loop():
    if not ACCOUNTS:
        print("[bot] ⚠️ No hay cuentas de Binance configuradas. Nada que monitorear.")
        return

    for account in ACCOUNTS:
        since = int(time.time() * 1000) - 24 * 60 * 60 * 1000
        for t in fetch_pay_transactions(account, since):
            seen[account["label"]].add(t.get("orderId") or str(t))
        print(f"[bot] [{account['label']}] Historial previo cargado: {len(seen[account['label']])} transacciones")

    while True:
        if bot_activo:
            since = int(time.time() * 1000) - 2 * 60 * 1000
            for account in ACCOUNTS:
                for t in fetch_pay_transactions(account, since):
                    uid = t.get("orderId") or str(t)
                    with lock:
                        if uid not in seen[account["label"]]:
                            seen[account["label"]].add(uid)
                            send_telegram(fmt_pay(t, account))
                            direccion = "RECIBIDO" if is_incoming(t, account) else "ENVIADO"
                            print(f"[{direccion}] [{account['label']}] {t.get('amount')} {t.get('currency')} — Order ID: {uid}")
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

                if not is_authorized(chat_id):
                    continue
                if text == "🏠 Menú":
                    threading.Thread(target=cmd_ayuda, args=(chat_id,), daemon=True).start()
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
                    threading.Thread(target=handle_command, args=(text, chat_id), daemon=True).start()

        except Exception as e:
            print(f"[commands error] {e}")
        time.sleep(2)

# ── Main ───────────────────────────────────────────────────────

def main():
    # Eliminar webhook al iniciar para asegurar modo polling
    try:
        requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook",
            timeout=10
        )
        print("[bot] Webhook eliminado al iniciar")
    except:
        pass

    cuentas_txt = ", ".join(f"{a['emoji']} {a['label']}" for a in ACCOUNTS) or "⚠️ ninguna configurada"
    send_telegram(
        "🤖 <b>Bot de Binance Pay iniciado</b>\n"
        f"🔗 Cuentas: {cuentas_txt}\n"
        "Monitoreando pagos cada 10 segundos…\n\n"
        "Toca el botón para ver opciones 👇",
        reply_markup=get_menu_markup()
    )
    print(f"[bot] Iniciado. Cuentas monitoreadas: {cuentas_txt}")

    # Comandos Telegram en hilo separado
    t_cmd = threading.Thread(target=commands_loop, daemon=True)
    t_cmd.start()

    # Monitor principal
    monitor_loop()

if __name__ == "__main__":
    main()
