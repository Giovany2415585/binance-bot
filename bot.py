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

# ── Fetch Binance ──────────────────────────────────────────────
def fetch_deposits(since_ms: int, limit: int = 50) -> list:
    try:
        data = binance_get("/sapi/v1/capital/deposit/hisrec", {"startTime": since_ms, "limit": limit, "status": 1})
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[deposits error] {e}")
        return []

def fetch_withdrawals(since_ms: int, limit: int = 50) -> list:
    try:
        data = binance_get("/sapi/v1/pay/transactions", {"startTimestamp": since_ms, "limit": limit})
        if isinstance(data, dict):
            return data.get("data", [])
        return []
    except Exception as e:
        print(f"[withdrawals error] {e}")
        return []

def fetch_balance() -> list:
    try:
        data = binance_get("/api/v3/account", {})
        balances = [b for b in data.get("balances", []) if float(b["free"]) > 0 or float(b["locked"]) > 0]
        return balances
    except Exception as e:
        print(f"[balance error] {e}")
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
        f"🔗 TxID:   <code>{str(d.get('txId','N/A'))[:24]}…</code>"
    )

def fmt_withdrawal(w: dict) -> str:
    status_map = {0:"📧 Correo enviado", 1:"❌ Cancelado", 2:"⏳ Esperando",
                  3:"🔴 Rechazado", 4:"⚙️ Procesando", 5:"⚠️ Fallo", 6:"✅ Completado"}
    status = status_map.get(w.get("status", 0), "❓ Desconocido")
    ts = w.get("applyTime", int(time.time() * 1000))
    return (
        f"🔴 <b>RETIRO / PAGO ENVIADO</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🪙 Moneda: <b>{w.get('coin','?')}</b>\n"
        f"💸 Monto:  <b>{w.get('amount','?')}</b>\n"
        f"📬 Destino: <code>{str(w.get('address','?'))[:20]}…</code>\n"
        f"📌 Estado: {status}\n"
        f"🕐 Fecha:  {fmt_time(ts if isinstance(ts, int) else int(time.time()*1000))}"
    )

# ── Comandos ───────────────────────────────────────────────────
def cmd_ayuda():
    return (
        "🤖 <b>Comandos disponibles</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "/ultimo — Último pago recibido\n"
        "/ultimos5 — Últimos 5 movimientos\n"
        "/balance — Saldo actual por moneda\n"
        "/depositos — Últimos depósitos\n"
        "/retiros — Últimos retiros\n"
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
    deps = fetch_deposits(since, limit=5)
    if deps:
        return fmt_deposit(deps[-1])
    return "📭 No se encontraron depósitos recientes."

def cmd_depositos():
    since = int(time.time() * 1000) - 7 * 24 * 60 * 60 * 1000
    deps = fetch_deposits(since, limit=5)
    if not deps:
        return "📭 No hay depósitos en los últimos 7 días."
    msgs = ["💚 <b>ÚLTIMOS DEPÓSITOS</b>\n━━━━━━━━━━━━━━━━━━"]
    for d in deps[-5:]:
        msgs.append(fmt_deposit(d))
        msgs.append("─────────────────")
    return "\n".join(msgs)

def cmd_retiros():
    since = int(time.time() * 1000) - 7 * 24 * 60 * 60 * 1000
    wits = fetch_withdrawals(since, limit=5)
    if not wits:
        return "📭 No hay retiros en los últimos 7 días."
    msgs = ["🔴 <b>ÚLTIMOS RETIROS</b>\n━━━━━━━━━━━━━━━━━━"]
    for w in wits[-5:]:
        msgs.append(fmt_withdrawal(w))
        msgs.append("─────────────────")
    return "\n".join(msgs)

def cmd_ultimos5():
    since = int(time.time() * 1000) - 7 * 24 * 60 * 60 * 1000
    deps = fetch_deposits(since, limit=5)
    wits = fetch_withdrawals(since, limit=5)

    movimientos = []
    for d in deps:
        movimientos.append(("dep", d.get("insertTime", 0), d))
    for w in wits:
        ts = w.get("applyTime", 0)
        movimientos.append(("wit", ts if isinstance(ts, int) else 0, w))

    movimientos.sort(key=lambda x: x[1], reverse=True)

    if not movimientos:
        return "📭 No hay movimientos recientes."

    msgs = ["📋 <b>ÚLTIMOS 5 MOVIMIENTOS</b>\n━━━━━━━━━━━━━━━━━━"]
    for tipo, _, data in movimientos[:5]:
        if tipo == "dep":
            msgs.append(fmt_deposit(data))
        else:
            msgs.append(fmt_withdrawal(data))
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
    elif text == "/depositos":
        send_telegram(cmd_depositos())
    elif text == "/retiros":
        send_telegram(cmd_retiros())
    elif text == "/ultimos5":
        send_telegram(cmd_ultimos5())
    elif text == "/start":
        send_telegram("🤖 <b>Bot de Binance activo</b>\nEscribe /ayuda para ver los comandos disponibles.")

# ── Loop de comandos (hilo separado) ──────────────────────────
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
    seen_deposits    = set()
    seen_withdrawals = set()

    since = int(time.time() * 1000) - 24 * 60 * 60 * 1000
    for d in fetch_deposits(since):
        seen_deposits.add(d.get("txId") or d.get("id") or str(d))
    for w in fetch_withdrawals(since):
        seen_withdrawals.add(w.get("id") or w.get("txId") or str(w))

    print(f"[bot] Historial previo cargado: {len(seen_deposits)} depósitos, {len(seen_withdrawals)} retiros")

    while True:
        since = int(time.time() * 1000) - 2 * 60 * 1000

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

# ── Main ───────────────────────────────────────────────────────
def main():
    send_telegram(
        "🤖 <b>Bot de Binance iniciado</b>\n"
        "Monitoreando depósitos y retiros cada 10 segundos…\n\n"
        "Escribe /ayuda para ver los comandos disponibles."
    )
    print("[bot] Iniciado.")

    t = threading.Thread(target=commands_loop, daemon=True)
    t.start()

    monitor_loop()

if __name__ == "__main__":
    main()
