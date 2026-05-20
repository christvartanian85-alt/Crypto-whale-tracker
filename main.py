import os
import json
import time
import logging
import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# =========================
# CONFIG
# =========================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

VIP_FILE = "vip_wallets.json"
USER_FILE = "users.json"

client = httpx.AsyncClient(timeout=10)

# =========================
# STATE
# =========================

seen_txs = set()
bundle_map = {}
signal_lock = {}

# =========================
# FILE HELPERS
# =========================

def load_json(file, default):
    try:
        with open(file, "r") as f:
            return json.load(f)
    except:
        return default

def save_json(file, data):
    with open(file, "w") as f:
        json.dump(data, f, indent=2)

def load_users():
    return load_json(USER_FILE, {})

def load_vip():
    return load_json(VIP_FILE, [])

# =========================
# BLOCKCHAIN (SOLANA)
# =========================

async def get_txs(address):
    try:
        url = f"https://public-api.solscan.io/account/transactions?account={address}&limit=10"
        r = await client.get(url)
        if r.status_code == 200:
            return r.json()
    except:
        pass
    return []

# =========================
# BUNDLE DETECTOR
# =========================

def add_bundle(token, wallet):
    now = time.time()

    if token not in bundle_map:
        bundle_map[token] = []

    bundle_map[token].append({
        "wallet": wallet,
        "time": now
    })

    # 10 min window
    bundle_map[token] = [
        x for x in bundle_map[token]
        if now - x["time"] < 600
    ]

def check_bundle(token):
    wallets = set(x["wallet"] for x in bundle_map.get(token, []))

    if len(wallets) >= 10:
        return len(wallets)

    return None

# =========================
# SIGNAL LOCK (ANTI SPAM)
# =========================

def can_send_signal(token):
    now = time.time()

    if token in signal_lock:
        if now - signal_lock[token] < 1800:
            return False

    signal_lock[token] = now
    return True

# =========================
# CORE TRACKER
# =========================

async def tracker(app):
    wallets = load_vip()
    users = load_users()

    for w in wallets:
        address = w.get("address")
        chain = w.get("chain")

        if chain != "solana":
            continue

        txs = await get_txs(address)

        for tx in txs[:5]:
            sig = tx.get("txHash")

            if not sig or sig in seen_txs:
                continue

            seen_txs.add(sig)

            token = tx.get("tokenAddress")
            if not token:
                continue

            add_bundle(token, address)

            bundle = check_bundle(token)

            # =========================
            # SIGNAL
            # =========================

            if bundle and can_send_signal(token):
                msg = (
                    f"🚨 SIGNAL ALERT\n\n"
                    f"Token: {token}\n"
                    f"Wallets: {bundle}\n"
                )

                for uid in users:
                    try:
                        await app.bot.send_message(int(uid), msg)
                    except:
                        pass

# =========================
# COMMANDS
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = load_users()
    uid = str(update.effective_user.id)

    if uid not in users:
        users[uid] = {"plan": "free"}
        save_json(USER_FILE, users)

    await update.message.reply_text("🚀 Whale Bot is running")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"📊 Tracking wallets: {len(load_vip())}"
    )

# =========================
# PRO SYSTEM (BASE)
# =========================

async def upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💎 PRO PLAN\n\n"
        "Send 20 USDT (TRC20)\n"
        "Then /verify_payment <tx>"
    )

async def verify_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)

    if not context.args:
        await update.message.reply_text("Usage: /verify_payment <tx>")
        return

    tx = context.args[0]

    users = load_users()
    users[uid] = {"plan": "pending", "tx": tx}
    save_json(USER_FILE, users)

    await update.message.reply_text("⏳ Payment submitted")

    if ADMIN_ID:
        await context.bot.send_message(
            ADMIN_ID,
            f"New payment\nUser: {uid}\nTX: {tx}"
        )

async def grant_pro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    uid = context.args[0]

    users = load_users()
    users[uid] = {"plan": "pro"}

    save_json(USER_FILE, users)

    await update.message.reply_text("✅ PRO activated")

# =========================
# JOB RUNNER
# =========================

async def job(context: ContextTypes.DEFAULT_TYPE):
    await tracker(context.application)

# =========================
# MAIN
# =========================

def main():
    if not TOKEN:
        print("Missing TELEGRAM_BOT_TOKEN")
        return

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("upgrade", upgrade))
    app.add_handler(CommandHandler("verify_payment", verify_payment))
    app.add_handler(CommandHandler("grant_pro", grant_pro))

    app.job_queue.run_repeating(job, interval=120, first=10)

    print("🚀 Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
