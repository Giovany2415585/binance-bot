import os
import time
import hmac
import hashlib
import requests
import threading
from datetime import datetime

# ── Configuración ──────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "8622111444:AAHKYOFrIAFGvPdhHlev6UwfNoKtUFsS93o")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "5800355077")
BINANCE_API_KEY  = os.getenv("BINANCE_API_KEY",  "Y5Cw2JrhUeDhSkKVE5cE36Dd715ggI1k3k4lFkjX8wKAhn4kn6EHY6XguO3iiy6g")
BINANCE_SECRET   = os.getenv("BINANCE_SECRET",   "LVB0ZL2LdhKLri6t03SPAiWIjvpAn2QR13znhe8TeGHbWYakBn1Y26r88fVUsFrj")

POLL_INTERVAL = 10
BASE_URL      = "https://api.binance.com"

# ── Helpers Binance ────────────────────────────────────────────
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

# ── Helpers Telegram ───────────────────────────────────────────
def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)

def get_updates(offset: int) -> list:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    r = requests.get(url, params={"timeout": 5, "offset": offset}, timeout=10)
    return r.json().get("result", [])

def fmt_time(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000).strftime("%d/%m/%Y %H:%M:%S")
    except:
        return str(ms)

# ── Fetch Binance Pay ──────────────────────────────────────────
def fetch_pay_transactions(since_ms: int, limit: int = 50) -> list:
    try:
        data = binance_get("/sapi/v1/pay/transactions", {"startTimestamp": since_ms, "limit": limit})
        if isinstance(data, dict):
            return data.get("data", [])
        return []
    except Exception as e:
        print(f"[pay error] {e}")
        return []

def fetch_balance() -> list:
    try:
        data = binance_get("/api/v3/account", {})
        balances = [b for b in data.get("balances", []) if float(b["free"]) > 0 or float(b["locked"]) > 0]
        return balances
    except Exception as e:
        print(f"[balance error] {e}")
        return []

# ── Formatter Binance Pay ──────────────────────────────────────
def fmt_pay(t: dict) -> str:
    flow = t.get("transactionType", "")
    monto = t.get("amount", "?")
    moneda = t.get("currency", "?")
    contraparte = t.get("counterPartyNickname") or t.get("counterParty") or "Desconocido"
    orden = t.get("orderId", "N/A")
    ts = t.get("transactionTime", int(time.time() * 1000))
    nota = t.get("remark", "")

    if flow == "IN":
        emoji = "💚"
        titulo = "PAGO RECIBIDO"
        quien = f"👤 De: <b>{contraparte}</b>"
    else:
        emoji = "🔴"
        titulo = "PAGO ENVIADO"
        quien = f"👤 Para: <b>{contraparte}</b>"

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

# ── Comandos ───────────────────────────────────────────────────
def cmd_ayuda():
    return (
        "🤖 <b>Comandos disponibles</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "/ultimo — Último pago recibido\n"
        "/ultimos5 — Últimos 5 movimientos\n"
        "/balance — Saldo actual por moneda\n"
        "/recibidos — Últimos pagos recibidos\n"
        "/enviados — Últimos pagos enviados\n"
        "/ayuda — Ver esta lista"
    )

def cmd_balance():
    balances = fetch_balance()
    if not balances:
        return "❌ No se pudo obtener el balance."
    lines = ["💼 <b>BALANCE ACTUAL</b>\n━━━━━━━━━━━━━━━━━━"]
    for b in balances[:15]:
        free   = float(b['free'])
        locked = float(b['locked'])
        lines.append(f"🪙 <b>{b['asset']}</b>: {free:.6f} libre | {locked:.6f} bloqueado")
    return "\n".join(lines)

def cmd_ultimo():
    since = int(time.time() * 1000) - 30 * 24 * 60 * 60 * 1000
    txs = fetch_pay_transactions(since, limit=20)
    recibidos = [t for t in txs if t.get("transactionType") == "IN"]
    if recibidos:
        return fmt_pay(recibidos[0])
    return "📭 No se encontraron pagos recibidos recientes."

def cmd_recibidos():
    since = int(time.time() * 1000) - 7 * 24 * 60 * 60 * 1000
    txs = fetch_pay_transactions(since, limit=20)
    recibidos = [t for t in txs if t.get("transactionType") == "IN"][:5]
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
    enviados = [t for t in txs if t.get("transactionType") == "OUT"][:5]
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

# ── Handler de comandos ────────────────────────────────────────
def handle_command(text: str):
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
    elif text == "/start":
        send_telegram("🤖 <b>Bot de Binance Pay activo</b>\nEscribe /ayuda para ver los comandos disponibles.")

# ── Loop de comandos ───────────────────────────────────────────
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

# ── Loop de monitoreo ──────────────────────────────────────────
def monitor_loop():
    seen = set()

    since = int(time.time() * 1000) - 24 * 60 * 60 * 1000
    for t in fetch_pay_transactions(since):
        seen.add(t.get("orderId") or str(t))

    print(f"[bot] Historial previo cargado: {len(seen)} transacciones")

    while True:
        since = int(time.time() * 1000) - 2 * 60 * 1000

        for t in fetch_pay_transactions(since):
            uid = t.get("orderId") or str(t)
            if uid not in seen:
                seen.add(uid)
                send_telegram(fmt_pay(t))
                flow = t.get("transactionType", "?")
                print(f"[{'IN' if flow=='IN' else 'OUT'}] {t.get('amount')} {t.get('currency')}")

        time.sleep(POLL_INTERVAL)

# ── Main ───────────────────────────────────────────────────────
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
